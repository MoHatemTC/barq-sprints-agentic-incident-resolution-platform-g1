"""Production-shaped events and per-incident governance across cache reuse."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph, run_graph
from agent.semantic_cache import SemanticCache
from agent.state import EventPayload
from app.workers import cluster_runtime, tasks
from app.workers.db import InMemoryRepo
from app.workers.retry_policy import RetryConfig
from tests.agent_support import MFA, ORDER_P1, VPN, FakeServiceNow, event_for, make_deps


@pytest.fixture(autouse=True)
def _leader_has_another_caller(monkeypatch):
    # The leader incident is not readable in these tests; it was reported by someone
    # else, so the same-caller rule does not apply unless a test says so.
    monkeypatch.setattr(tasks, "leader_caller_id", lambda *args, **kwargs: "f" * 32)


def graph_run(deps, record, cached=None):
    return run_graph(
        build_graph(deps, checkpointer=InMemorySaver()),
        EventPayload.model_validate(event_for(record)),
        execution_id=str(uuid4()),
        correlation_id="cache-test",
        attempt=1,
        deps=deps,
        cached_draft=cached,
    )


def test_minimal_event_loads_governed_incident_before_clustering(monkeypatch):
    service = FakeServiceNow()
    deps = make_deps(servicenow=service)
    monkeypatch.setattr(cluster_runtime, "get_agent_dependencies", lambda: deps)
    payload = event_for(VPN)
    payload["short_description"] = "Untrusted injected broker description"
    actual = cluster_runtime.load_cluster_incident(payload, str(uuid4()), "original-correlation")
    assert actual["short_description"] == VPN["short_description"]
    assert service.calls == ["read_incident"]


def test_cached_draft_saves_generation_but_runs_follower_gates_and_write():
    leader = graph_run(make_deps(agent_confidence_floor=0.1), VPN)
    assert leader["cache_draft"]
    follower_record = {**VPN, "sys_id": "b" * 32, "number": "INC0010991"}
    service = FakeServiceNow({"follower": follower_record})
    deps = make_deps(servicenow=service, agent_confidence_floor=0.1)
    result = graph_run(deps, follower_record, leader["cache_draft"])
    assert result["processing_state"] == "complete"
    assert result["cache_draft_used"]
    assert "generate" not in deps.llm.purposes()
    assert {"injection_classifier", "classify", "diagnose", "verify_evidence"} <= set(
        deps.llm.purposes()
    )
    assert {
        "load",
        "determine_risk",
        "retrieve",
        "verify_evidence",
        "safety_check",
        "confidence_check",
        "act",
    } <= set(result["path"])
    assert [sys_id for sys_id, _ in service.updates] == [follower_record["sys_id"]]
    critic = next(call for call in deps.llm.calls if call["purpose"] == "verify_evidence")
    assert follower_record["short_description"] in critic["prompt"]


def test_cached_draft_cannot_skip_high_risk_approval():
    leader = graph_run(make_deps(agent_confidence_floor=0.1), VPN)
    assert leader["cache_draft"]
    service = FakeServiceNow()
    result = graph_run(make_deps(servicenow=service), ORDER_P1, leader["cache_draft"])
    assert result["paused"]
    assert len(service.updates) == 1
    assert service.updates[0][1].ai_processing_state.value == "awaiting_approval"
    assert service.updates[0][1].ai_resolution is None


def test_stale_citation_regenerates_instead_of_using_cached_draft():
    leader = graph_run(make_deps(agent_confidence_floor=0.1), VPN)
    cached = deepcopy(leader["cache_draft"])
    cached["steps"][0]["article_id"] = "removed-version"
    deps = make_deps(agent_confidence_floor=0.1)
    result = graph_run(deps, VPN, cached)
    assert not result["cache_draft_used"]
    assert "generate" in deps.llm.purposes()


def test_elevated_policy_approval_has_a_real_resumable_checkpoint():
    service = FakeServiceNow()
    deps = make_deps(servicenow=service, agent_confidence_floor=0.1)
    graph = build_graph(deps, checkpointer=InMemorySaver())
    event = EventPayload.model_validate(event_for(MFA))
    execution = str(uuid4())
    context = dict(execution_id=execution, correlation_id="elevated-test", attempt=1, deps=deps)
    parked = run_graph(graph, event, **context)
    assert parked["paused"] and parked["outcome"] == "suggested"
    assert not service.updates[-1][1].ai_resolution
    resumed = run_graph(graph, event, resume={"decision": "approved"}, **context)
    assert not resumed["paused"]
    assert resumed["processing_state"] == "complete"
    assert resumed["cache_draft"]
    assert service.updates[-1][1].ai_resolution


def setup_pair():
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=lambda text: [1.0, 0.0])
    leader_id, follower_id = uuid4(), uuid4()
    payload = event_for(VPN)
    follower_payload = {
        **payload,
        "event_id": "follower-event",
        "sys_id": "b" * 32,
        "number": "INC0010991",
    }
    for eid, event in ((leader_id, payload), (follower_id, follower_payload)):
        repo.seed_execution(eid, status="queued", event_id=event["event_id"])
        repo.event_payloads[event["event_id"]] = event
    incident = {
        "sys_id": VPN["sys_id"],
        "number": VPN["number"],
        "short_description": VPN["short_description"],
        "active": True,
        "ai_human_lock": False,
    }
    admission = cache.admit(incident, leader_id)
    return repo, cache, leader_id, follower_id, follower_payload, incident, admission.cluster_id


def test_waiter_survives_broker_failure_with_original_trace_and_no_retry_budget(monkeypatch):
    repo, cache, _, eid, payload, incident, cluster = setup_pair()
    monkeypatch.setattr(
        tasks,
        "load_cluster_incident",
        lambda *args: {**incident, "sys_id": payload["sys_id"], "number": payload["number"]},
    )
    task = SimpleNamespace(request=SimpleNamespace(retries=0))
    result = tasks._run_incident(
        task,
        payload,
        str(eid),
        RetryConfig(3, 1.0, 60.0, False),
        repo,
        semantic_cache=cache,
        graph_backend="langgraph",
        correlation_id="follower-original-trace",
    )
    assert result["status"] == "cluster_waiting"
    assert repo.get_status(eid) == "queued"
    assert repo.retry_states[eid]["attempt_count"] == 0
    assert repo.ready_cluster_waiters() == []
    cache.publish_solution(cluster, {"cache_draft": {"steps": []}})
    monkeypatch.setattr(
        cluster_runtime,
        "send_incident_event",
        lambda *args: (_ for _ in ()).throw(ConnectionError("broker down")),
    )
    with pytest.raises(ConnectionError):
        cluster_runtime.dispatch_cluster_waiters(repo)
    sent = []
    monkeypatch.setattr(cluster_runtime, "send_incident_event", lambda *args: sent.append(args))
    assert cluster_runtime.dispatch_cluster_waiters(repo) == 1
    assert sent[0] == (payload, str(eid), "follower-original-trace")
    repo.claim_for_running(eid)
    assert repo.ready_cluster_waiters() == []


def test_worker_db_status_beats_stale_api_process_anchor():
    repo, cache, leader, _, _, _, cluster = setup_pair()
    cache.mark_cluster_awaiting_approval(cluster)
    candidate = {"cache_draft": {"steps": []}}
    repo.update_cluster_status(cluster, "resolved", solution=candidate)
    assert cache.get_cluster_status(cluster).value == "resolved"
    assert cache.get_cluster_solution(cluster) == candidate
    repo.clusters[cluster].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert cache.admit({"short_description": "VPN failure"}, leader).mode.value == "independent"


def test_database_failure_cannot_publish_resolved_redis_anchor(monkeypatch):
    repo, cache, _, _, _, _, cluster = setup_pair()
    monkeypatch.setattr(
        repo,
        "update_cluster_status",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("db down")),
    )
    with pytest.raises(ConnectionError):
        cache.publish_solution(cluster, {"cache_draft": {}})
    assert cache._anchors[cluster].status.value == "running"


def test_follower_resolves_with_zero_llm_calls_and_writes_to_servicenow(monkeypatch):
    repo, cache, _, eid, payload, _incident, cluster = setup_pair()
    # A production-shaped record read through the real loader: the follower is only
    # reused if it passes its own eligibility, screening and risk gates, which a
    # minimal dict with no ai_enabled/category/priority fields would not.
    follower_inc = {
        **VPN,
        "sys_id": payload["sys_id"],
        "number": payload["number"],
    }
    service = FakeServiceNow({"follower": follower_inc})
    deps = make_deps(servicenow=service, agent_confidence_floor=0.1)
    monkeypatch.setattr(cluster_runtime, "get_agent_dependencies", lambda: deps)
    solution = {
        "outcome": "suggested",
        "summary": "AI Suggested Response drafted. Confidence 0.95.",
        "suggestion": "1. Reset VPN profile",
        "resolution": "1. Reset VPN profile",
        "confidence": 0.95,
        "classification": "network",
        "work_note": (
            "AI Suggested Response drafted. Confidence 0.95. Guarded suggestion completed."
        ),
        "processing_state": "complete",
        "write_back": "written",
        "cache_draft": {"rendered": "1. Reset VPN profile", "steps": []},
    }
    cache.publish_solution(cluster, solution)

    repo.claim_for_running(eid)
    task = SimpleNamespace(request=SimpleNamespace(retries=0))
    result = tasks._run_incident(
        task,
        payload,
        str(eid),
        RetryConfig(3, 1.0, 60.0, False),
        repo,
        semantic_cache=cache,
        graph_backend="langgraph",
        correlation_id="follower-original-trace",
    )
    assert result["status"] == "succeeded"
    assert result["cluster_role"] == "follower"
    assert result["result"] == solution
    assert repo.get_status(eid) == "succeeded"
    # Zero LLM calls for follower:
    assert len(deps.llm.calls) == 0
    # Follower ServiceNow ticket updated:
    assert [sys_id for sys_id, _ in service.updates] == [payload["sys_id"]]
    update = service.updates[0][1]
    assert update.ai_processing_state.value == "complete"
    assert update.ai_resolution == "1. Reset VPN profile"


@pytest.mark.parametrize("already_waiting", [False, True])
def test_a_follower_never_waits_behind_a_leader_parked_for_a_person(monkeypatch, already_waiting):
    # Seen live: a P1 outage report sat queued behind a similar low-priority ticket that
    # was waiting for an engineer's approval. It must run its own governed graph now.
    repo, cache, _, eid, payload, incident, cluster = setup_pair()
    monkeypatch.setattr(
        tasks,
        "load_cluster_incident",
        lambda *args: {**incident, "sys_id": payload["sys_id"], "number": payload["number"]},
    )
    graph_calls: list[dict] = []

    def fake_graph(event, **kwargs):
        graph_calls.append(event)
        return {"outcome": "escalated_high_risk", "paused": False, "processing_state": "pending"}

    monkeypatch.setattr(tasks, "invoke_graph", fake_graph)
    task = SimpleNamespace(request=SimpleNamespace(retries=0))
    if already_waiting:
        waiting = tasks._run_incident(
            task,
            payload,
            str(eid),
            RetryConfig(3, 1.0, 60.0, False),
            repo,
            semantic_cache=cache,
            graph_backend="langgraph",
            correlation_id="follower-of-parked-leader",
        )
        assert waiting["status"] == "cluster_waiting"
        assert graph_calls == []
        assert repo.ready_cluster_waiters() == []
    cache.mark_cluster_awaiting_approval(cluster)
    if already_waiting:
        sent = []
        monkeypatch.setattr(cluster_runtime, "send_incident_event", lambda *args: sent.append(args))
        assert cluster_runtime.dispatch_cluster_waiters(repo) == 1
        assert sent == [(payload, str(eid), "follower-of-parked-leader")]
    result = tasks._run_incident(
        task,
        payload,
        str(eid),
        RetryConfig(3, 1.0, 60.0, False),
        repo,
        semantic_cache=cache,
        graph_backend="langgraph",
        correlation_id="follower-of-parked-leader",
    )
    assert result["status"] != "cluster_waiting"
    assert len(graph_calls) == 1
