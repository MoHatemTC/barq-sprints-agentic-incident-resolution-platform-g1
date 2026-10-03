"""The agent works the incident itself, as far as the autonomy level allows.

Design: docs/barq_agentic_platform_design.md §6 (decision table) and §7 (tools).
Scenarios: A1, A3, A7, D1, D7, L2, L4 and the kill switch (I1).
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from agent.nodes import act, validate
from agent.nodes.act import AGENT_NAME, _fulfil, caller_comment, close_notes
from agent.state import FinalOutput, Outcome
from app.models.incident import IncidentFulfilmentPayload
from tests.agent_support import SCOPE, VPN, FakeServiceNow, event_for, make_deps
from tests.test_nodes import base_state, reasoned_state, snapshot

CALLER = "c" * 32
SERVICE_ACCOUNT = "d" * 32
NETWORK_GROUP = "e" * 32
LOCK = f"{SCOPE}_ai_human_lock"


@pytest.fixture(autouse=True)
def _operator_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["agent.nodes.act"],
        "_request_human_decision",
        lambda payload: {"decision": "approved", "decided_by": "test", "source": "no_graph"},
    )


def vpn(**fields: Any) -> dict[str, Any]:
    return {**VPN, "caller_id": CALLER, **fields}


def run(record: dict[str, Any], **settings: Any) -> tuple[FakeServiceNow, dict[str, Any]]:
    backend = FakeServiceNow({"vpn": record})
    settings.setdefault("agent_assignment_groups", {"network": NETWORK_GROUP})
    deps = make_deps(servicenow=backend, **settings)
    output = act(reasoned_state(incident=snapshot(record)), deps)["output"]
    return backend, output


def test_suggest_level_leaves_the_incident_to_people() -> None:
    backend, output = run(vpn())
    assert output["outcome"] == "suggested"
    assert output["fulfilment"] == []
    assert backend.fulfilments == []
    assert backend.records[VPN["sys_id"]]["state"] == "1"


def test_assist_routes_and_starts_but_does_not_resolve() -> None:
    backend, output = run(vpn(), agent_autonomy_level="assist")
    assert output["fulfilment"] == ["assign_incident:applied"]
    record = backend.records[VPN["sys_id"]]
    assert record["state"] == "2"
    assert record["assignment_group"] == NETWORK_GROUP
    assert any(kind == "work_notes" and AGENT_NAME in text for _, kind, text in backend.journal)


def test_autonomous_gives_the_caller_the_fix_and_resolves() -> None:
    backend, output = run(vpn(), agent_autonomy_level="autonomous")
    assert output["fulfilment"] == ["assign_incident:applied", "resolve_incident:applied"]
    record = backend.records[VPN["sys_id"]]
    assert record["state"] == "6"
    assert record["close_code"] == "Solution provided"
    assert record["close_notes"].startswith(f"Resolved by {AGENT_NAME}")
    comments = [text for _, kind, text in backend.journal if kind == "comments"]
    assert len(comments) == 1
    assert comments[0].startswith(f"Hello, this is {AGENT_NAME}")
    assert "an engineer will take over" in comments[0]
    assert "[KB0001" in comments[0]  # the cited fix reaches the caller


def test_no_caller_is_never_resolved() -> None:
    backend, output = run(vpn(caller_id=""), agent_autonomy_level="autonomous")
    assert output["fulfilment"] == [
        "assign_incident:applied",
        "resolve_incident:skipped_no_caller",
    ]
    assert backend.records[VPN["sys_id"]]["state"] == "2"


def test_service_account_caller_is_never_resolved() -> None:
    backend, output = run(
        vpn(caller_id=SERVICE_ACCOUNT),
        agent_autonomy_level="autonomous",
        agent_service_account_ids=[SERVICE_ACCOUNT],
    )
    assert "resolve_incident:skipped_no_caller" in output["fulfilment"]
    assert backend.records[VPN["sys_id"]]["state"] == "2"


def test_a_group_a_person_chose_is_kept() -> None:
    chosen = "a" * 32
    backend, _ = run(vpn(assignment_group=chosen), agent_autonomy_level="assist")
    assert backend.records[VPN["sys_id"]]["assignment_group"] == chosen


def test_unmapped_category_is_started_without_routing() -> None:
    backend, output = run(vpn(), agent_autonomy_level="assist", agent_assignment_groups={})
    assert output["fulfilment"] == ["assign_incident:applied"]
    assert "assignment_group" not in backend.records[VPN["sys_id"]]


def test_incident_moved_on_by_a_person_is_left_alone() -> None:
    # On Hold: a person took it while the agent was working (L2, L4).
    backend, output = run(vpn(state="3"), agent_autonomy_level="autonomous")
    assert output["fulfilment"] == ["assign_incident:skipped_state_changed"]
    assert backend.records[VPN["sys_id"]]["state"] == "3"
    assert not [kind for _, kind, _ in backend.journal if kind == "comments"]


def test_human_lock_set_during_the_run_stops_fulfilment() -> None:
    record = vpn()
    backend = FakeServiceNow({"vpn": record})
    backend.on_update = lambda sys_id: backend.records[sys_id].__setitem__(LOCK, "true")
    deps = make_deps(
        servicenow=backend,
        agent_autonomy_level="autonomous",
        agent_assignment_groups={"network": NETWORK_GROUP},
    )
    output = act(reasoned_state(incident=snapshot(record)), deps)["output"]
    assert output["fulfilment"] == ["assign_incident:skipped_human_lock"]
    assert backend.fulfilments == []


def test_a_crash_after_fulfilment_never_repeats_it() -> None:
    record = vpn()
    backend = FakeServiceNow({"vpn": record})
    backend.crash_before_log_recorded = RuntimeError("worker killed")
    deps = make_deps(
        servicenow=backend,
        agent_autonomy_level="autonomous",
        agent_assignment_groups={"network": NETWORK_GROUP},
    )
    state = reasoned_state(incident=snapshot(record))
    with pytest.raises(RuntimeError):
        act(state, deps)
    backend.crash_before_log_recorded = None
    output = act(state, deps)["output"]
    assert output["fulfilment"] == ["assign_incident:applied", "resolve_incident:applied"]
    assert len(backend.fulfilments) == 2
    assert len([kind for _, kind, _ in backend.journal if kind == "comments"]) == 1


def test_escalations_are_never_fulfilled() -> None:
    deps = make_deps(agent_autonomy_level="autonomous")
    escalation = FinalOutput(
        outcome=Outcome.ESCALATED_HIGH_RISK,
        summary="P1",
        human_review_required=True,
        processing_state="awaiting_approval",
        approval_required=True,
    )
    state = reasoned_state(incident=snapshot(vpn()))
    incident = snapshot(vpn())
    assert _fulfil(state, deps, incident, escalation) == []


def test_kill_switch_stops_before_any_model_call() -> None:
    deps = make_deps(agent_autonomy_level="off")
    state = base_state(incident=snapshot(vpn()))
    state["event"] = event_for(VPN)
    result = validate(state, deps)["eligibility"]
    assert result["eligible"] is False
    assert any("switched off" in reason for reason in result["reasons"])


def test_texts_stay_within_servicenow_field_limits() -> None:
    long_fix = "step " * 2000
    assert len(caller_comment(long_fix)) <= 4000
    assert caller_comment(long_fix).endswith("an engineer will take over.")
    assert len(close_notes(long_fix, 0.9)) <= 4000


def test_resolution_payload_requires_close_information() -> None:
    with pytest.raises(ValueError):
        IncidentFulfilmentPayload(state="6", comments="fix")
    with pytest.raises(ValueError):
        IncidentFulfilmentPayload(state="7")  # the agent never closes directly
    with pytest.raises(ValueError):
        IncidentFulfilmentPayload(assignment_group="not-a-sys-id")
