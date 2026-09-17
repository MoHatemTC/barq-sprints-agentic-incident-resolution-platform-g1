"""Unit tests for each of the eleven nodes and the deterministic policy (S2.5).

Every node is called directly with a hand-built state and mocked dependencies:
no graph, no model, no network, no database.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.nodes import (
    NODE_ORDER,
    NODES,
    act,
    classify,
    confidence_check,
    determine_risk,
    diagnose,
    generate,
    load,
    retrieve,
    safety_check,
    validate,
    verify_evidence,
)
from agent.nodes.act import decide_outcome
from agent.nodes.confidence_check import score
from agent.policy import (
    assess_risk,
    check_eligibility,
    effective_priority,
    service_name,
    snapshot_incident,
)
from agent.prompts import ClassifyOutput, DiagnoseOutput, GenerateOutput, StepOutput
from agent.servicenow import PERMITTED_ACTIONS, ActionNotPermittedError
from agent.state import (
    ClassificationResult,
    Diagnosis,
    Draft,
    DraftStep,
    IncidentSnapshot,
    Outcome,
    RiskLevel,
)
from app.exceptions.servicenow import (
    ServiceNowConnectionError,
    ServiceNowHumanLockError,
    ServiceNowNotFoundError,
)
from app.models.incident import Incident
from app.models.knowledge import Classification
from app.workers.retry_policy import RetryableError, TerminalError
from tests.agent_support import (
    EXECUTION_ID,
    LEAVE,
    MFA,
    ORDER_P1,
    PRINTER,
    VPN,
    FakeLLM,
    FakeRetriever,
    FakeServiceNow,
    event_for,
    evidence,
    make_deps,
    vpn_answers,
)


def snapshot(record: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    incident = Incident.model_validate(record).model_dump(mode="json")
    return snapshot_incident({**incident, **overrides}).model_dump(mode="json")


def classification(label: str = "network", confidence: float = 0.9) -> dict[str, Any]:
    return ClassificationResult(
        label=Classification(label), rationale="r", model_confidence=confidence
    ).model_dump(mode="json")


def base_state(record: dict[str, Any] = VPN, **extra: Any) -> dict[str, Any]:
    return {
        "execution_id": EXECUTION_ID,
        "correlation_id": "corr-1",
        "event": event_for(record),
        "started_at": "2026-09-08T06:14:00+00:00",
        **extra,
    }


def reasoned_state(**extra: Any) -> dict[str, Any]:
    """VPN state as it stands after confidence_check on the happy path."""
    deps = make_deps()
    state = base_state(incident=snapshot(VPN), classification=classification())
    state |= validate(state, deps)
    state |= determine_risk(state, deps)
    state |= retrieve(state, deps)
    state |= diagnose(state, deps)
    state |= generate(state, deps)
    state |= verify_evidence(state, deps)
    state |= safety_check(state, deps)
    state |= confidence_check(state, deps)
    return state | extra


def test_the_graph_has_exactly_the_eleven_brief_nodes() -> None:
    assert NODE_ORDER == (
        "load",
        "validate",
        "classify",
        "determine_risk",
        "retrieve",
        "diagnose",
        "generate",
        "verify_evidence",
        "safety_check",
        "confidence_check",
        "act",
    )
    assert set(NODES) == set(NODE_ORDER)


# -- load ---------------------------------------------------------------------------------


class TestLoad:
    def test_reads_the_incident_through_the_gateway(self) -> None:
        backend = FakeServiceNow()
        deps = make_deps(servicenow=backend)
        update = load(base_state(), deps)
        incident = update["incident"]
        assert incident["number"] == "INC0010023"
        assert incident["priority"] == 3
        assert incident["service"] == "corporate-vpn"
        assert incident["ai_enabled"] is True
        assert incident["ai_human_lock"] is False
        assert deps.servicenow.calls == ["read_incident"]

    def test_transient_servicenow_failure_is_retryable(self) -> None:
        backend = FakeServiceNow()
        backend.read_error = ServiceNowConnectionError("down")
        with pytest.raises(RetryableError):
            load(base_state(), make_deps(servicenow=backend))

    def test_missing_incident_is_terminal(self) -> None:
        backend = FakeServiceNow()
        backend.read_error = ServiceNowNotFoundError("gone")
        with pytest.raises(TerminalError):
            load(base_state(), make_deps(servicenow=backend))


# -- validate -----------------------------------------------------------------------------


class TestValidate:
    def test_eligible_incident(self) -> None:
        update = validate(base_state(incident=snapshot(VPN)), make_deps())
        assert update["eligibility"] == {"eligible": True, "reasons": []}

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"ai_enabled": False}, "AI assistance is not enabled"),
            ({"ai_human_lock": True}, "human lock is set"),
            ({"ai_human_lock": None}, "human lock is unknown"),
            ({"state": "3"}, "on hold"),
            ({"state": "6"}, "not active"),
            ({"active": False}, "not active"),
            ({"category": "database"}, "not in the supported set"),
            ({"ai_processing_state": "complete"}, "already processed"),
        ],
    )
    def test_each_eligibility_check_fails_closed(
        self, overrides: dict[str, Any], reason: str
    ) -> None:
        state = base_state(incident=snapshot(VPN, **overrides))
        eligibility = validate(state, make_deps())["eligibility"]
        assert eligibility["eligible"] is False
        assert any(reason in r for r in eligibility["reasons"])

    def test_event_and_incident_numbers_must_agree(self) -> None:
        state = base_state(incident=snapshot(VPN))
        state["event"] = {**state["event"], "number": "INC0099999"}
        eligibility = validate(state, make_deps())["eligibility"]
        assert eligibility["eligible"] is False


# -- classify -----------------------------------------------------------------------------


class TestClassify:
    def test_records_label_and_redacts_the_prompt(self) -> None:
        llm = FakeLLM(vpn_answers())
        update = classify(base_state(incident=snapshot(VPN)), make_deps(llm=llm))
        assert update["classification"]["label"] == "network"
        prompt = llm.calls[0]["prompt"]
        assert "+971 50 123 4567" not in prompt
        assert "***PHONE***" in prompt
        assert "<incident>" in prompt and "INC0010023" in prompt

    def test_confidence_is_clamped(self) -> None:
        llm = FakeLLM({"classify": ClassifyOutput(label="other", rationale="x", confidence=7)})
        update = classify(base_state(incident=snapshot(LEAVE)), make_deps(llm=llm))
        assert update["classification"]["model_confidence"] == 1.0

    def test_length_bound_applies(self) -> None:
        llm = FakeLLM(vpn_answers())
        long_text = snapshot(VPN, description="x" * 20_000)
        classify(base_state(incident=long_text), make_deps(llm=llm, agent_max_incident_chars=500))
        assert len(llm.calls[0]["prompt"]) < 1500


# -- determine_risk -----------------------------------------------------------------------


class TestDetermineRisk:
    def test_p1_on_tier1_is_high(self) -> None:
        state = base_state(incident=snapshot(ORDER_P1), classification=classification("software"))
        risk = determine_risk(state, make_deps())["risk"]
        assert risk["level"] == "high"
        assert risk["service_tier"] == 1
        assert any("Priority 1" in r for r in risk["reasons"])

    def test_p3_on_tier2_is_low(self) -> None:
        state = base_state(incident=snapshot(VPN), classification=classification())
        risk = determine_risk(state, make_deps())["risk"]
        assert risk == {
            "level": "low",
            "reasons": ["Priority 3, service tier 2"],
            "approval_required": False,
            "service_tier": 2,
        }

    def test_mfa_reset_on_identity_is_elevated_and_needs_approval(self) -> None:
        state = base_state(incident=snapshot(MFA), classification=classification("access"))
        risk = determine_risk(state, make_deps())["risk"]
        assert risk["level"] == "elevated"
        assert risk["approval_required"] is True
        assert len(risk["reasons"]) == 2

    def test_security_classification_is_high(self) -> None:
        state = base_state(incident=snapshot(VPN), classification=classification("security"))
        assert determine_risk(state, make_deps())["risk"]["level"] == "high"

    def test_derived_priority_overrides_a_lowered_one(self) -> None:
        # Impact 1 / urgency 1 derives P1 even if someone typed P3 (§3.3).
        state = base_state(
            incident=snapshot(VPN, priority=3, impact=1, urgency=1),
            classification=classification(),
        )
        assert determine_risk(state, make_deps())["risk"]["level"] == "high"

    def test_unknown_priority_fails_closed(self) -> None:
        state = base_state(
            incident=snapshot(VPN, priority=None, impact=None, urgency=None),
            classification=classification(),
        )
        assert determine_risk(state, make_deps())["risk"]["level"] == "high"

    def test_configured_risk_priorities_are_honoured(self) -> None:
        state = base_state(incident=snapshot(VPN), classification=classification())
        risk = determine_risk(state, make_deps(agent_risk_priorities=[1, 2, 3]))["risk"]
        assert risk["level"] == "high"

    def test_reads_no_evidence(self) -> None:
        # The node must decide from the record alone; evidence in state is ignored.
        state = base_state(incident=snapshot(ORDER_P1), classification=classification("software"))
        state["retrieval"] = {"hits": "must not be read"}
        assert determine_risk(state, make_deps())["risk"]["level"] == "high"


# -- retrieve -----------------------------------------------------------------------------


class TestRetrieve:
    def test_passes_classification_top_k_and_threshold(self) -> None:
        retriever = FakeRetriever()
        state = base_state(incident=snapshot(VPN), classification=classification())
        result = retrieve(state, make_deps(retriever=retriever))["retrieval"]
        assert result["sufficient"] is True
        assert result["threshold"] == 0.55
        call = retriever.calls[0]
        assert call["classification"] is Classification.NETWORK
        assert call["top_k"] == 5
        assert call["incident_category"] == "network"
        assert "***PHONE***" in call["query"]

    def test_below_threshold_is_insufficient(self) -> None:
        retriever = FakeRetriever(hits=[evidence("KB0004", relevance=0.31)])
        state = base_state(incident=snapshot(PRINTER), classification=classification("hardware"))
        result = retrieve(state, make_deps(retriever=retriever))["retrieval"]
        assert result["sufficient"] is False
        assert result["best_relevance"] == 0.31

    def test_no_hits_is_insufficient(self) -> None:
        state = base_state(incident=snapshot(LEAVE), classification=classification("other"))
        result = retrieve(state, make_deps(retriever=FakeRetriever(hits=[])))["retrieval"]
        assert result["sufficient"] is False

    def test_store_outage_is_retryable(self) -> None:
        retriever = FakeRetriever(error=RetryableError("qdrant down"))
        state = base_state(incident=snapshot(VPN), classification=classification())
        with pytest.raises(RetryableError):
            retrieve(state, make_deps(retriever=retriever))


# -- diagnose -----------------------------------------------------------------------------


class TestDiagnose:
    def _state(self) -> dict[str, Any]:
        state = base_state(incident=snapshot(VPN), classification=classification())
        return state | retrieve(state, make_deps())

    def test_grounded_diagnosis(self) -> None:
        llm = FakeLLM(vpn_answers())
        diagnosis = diagnose(self._state(), make_deps(llm=llm))["diagnosis"]
        assert diagnosis["matched_article_ids"] == ["KB0001-v2"]
        assert diagnosis["symptom_match"] is True
        assert '<evidence article_id="KB0001-v2"' in llm.calls[0]["prompt"]

    def test_citations_outside_the_retrieved_set_are_discarded(self) -> None:
        answer = DiagnoseOutput(
            probable_cause="c",
            matched_article_ids=["KB0099-v1", "KB0001-v2", "KB0001-v2"],
            symptom_match=True,
            confidence=0.8,
            rationale="r",
        )
        diagnosis = diagnose(self._state(), make_deps(llm=FakeLLM({"diagnose": answer})))[
            "diagnosis"
        ]
        assert diagnosis["matched_article_ids"] == ["KB0001-v2"]

    def test_symptom_match_requires_a_real_match(self) -> None:
        answer = DiagnoseOutput(
            probable_cause="c",
            matched_article_ids=["KB0099-v1"],
            symptom_match=True,
            confidence=0.8,
            rationale="r",
        )
        diagnosis = diagnose(self._state(), make_deps(llm=FakeLLM({"diagnose": answer})))[
            "diagnosis"
        ]
        assert diagnosis["matched_article_ids"] == []
        assert diagnosis["symptom_match"] is False


# -- generate -----------------------------------------------------------------------------


class TestGenerate:
    def _state(self, answers: dict[str, Any] | None = None) -> dict[str, Any]:
        deps = make_deps(llm=FakeLLM(answers or vpn_answers()))
        state = base_state(incident=snapshot(VPN), classification=classification())
        state |= retrieve(state, deps)
        return state | diagnose(state, deps)

    def test_numbered_cited_procedure(self) -> None:
        draft = generate(self._state(), make_deps())["draft"]
        assert draft["rendered"].splitlines()[0] == (
            "1. Confirm the password was changed in the last 24 hours. [KB0001 v2 §Resolution]"
        )
        assert draft["rendered"].splitlines()[2].startswith("3. ")
        assert draft["rendered"].endswith(
            "Sources: KB0001 v2 — VPN authentication fails after a password change"
        )
        assert draft["dropped_steps"] == 0

    def test_steps_citing_unretrieved_articles_are_dropped(self) -> None:
        answers = vpn_answers()
        answers["generate"] = GenerateOutput(
            steps=[
                StepOutput(
                    text="Restart the server.", article_id="KB0010-v2", section="Resolution"
                ),
                StepOutput(text="Clear the cache.", article_id="KB0001-v2", section="Resolution"),
                StepOutput(text="   ", article_id="KB0001-v2", section="Resolution"),
            ]
        )
        draft = generate(self._state(), make_deps(llm=FakeLLM(answers)))["draft"]
        assert [s["text"] for s in draft["steps"]] == ["Clear the cache."]
        assert draft["dropped_steps"] == 2
        assert draft["rendered"].startswith("1. Clear the cache.")

    def test_draft_fits_the_4000_char_field(self) -> None:
        answers = vpn_answers()
        answers["generate"] = GenerateOutput(
            steps=[
                StepOutput(text="x" * 900, article_id="KB0001-v2", section="Resolution")
                for _ in range(8)
            ]
        )
        draft = generate(self._state(), make_deps(llm=FakeLLM(answers)))["draft"]
        assert len(draft["rendered"]) <= 4000
        assert 0 < len(draft["steps"]) < 8


# -- the Sprint 4 gates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "key", "gate"),
    [
        (verify_evidence, "verification", "verify_evidence"),
        (safety_check, "safety", "safety_check"),
    ],
)
def test_sprint4_gates_are_explicit_pass_throughs(node: Any, key: str, gate: str) -> None:
    update = node(reasoned_state(), make_deps())
    assert update == {
        key: {"gate": gate, "passed": True, "implemented": False, "checks": [], "reason": None}
    }


# -- confidence_check ---------------------------------------------------------------------


class TestConfidenceCheck:
    def test_above_floor_passes(self) -> None:
        result = confidence_check(reasoned_state(), make_deps())["confidence"]
        assert result == {"score": 0.82, "floor": 0.45, "passed": True}

    def test_below_floor_fails(self) -> None:
        state = reasoned_state()
        state["diagnosis"] = {**state["diagnosis"], "model_confidence": 0.44}
        assert confidence_check(state, make_deps())["confidence"]["passed"] is False

    def test_floor_is_configurable(self) -> None:
        result = confidence_check(reasoned_state(), make_deps(agent_confidence_floor=0.9))
        assert result["confidence"]["passed"] is False

    @pytest.mark.parametrize(
        ("match", "kept", "dropped", "expected"),
        [(True, 3, 0, 0.8), (False, 3, 0, 0.4), (True, 1, 1, 0.4), (True, 0, 2, 0.0)],
    )
    def test_score_formula(self, match: bool, kept: int, dropped: int, expected: float) -> None:
        diagnosis = Diagnosis(
            probable_cause="c",
            matched_article_ids=["KB0001-v2"],
            symptom_match=match,
            model_confidence=0.8,
            rationale="r",
        )
        step = DraftStep(text="t", article_id="KB0001-v2", section="Resolution")
        draft = Draft(steps=[step] * kept, rendered="", dropped_steps=dropped, sources=[])
        assert score(diagnosis, draft) == expected


# -- act ----------------------------------------------------------------------------------


class TestAct:
    def test_suggestion_is_written_with_review_flag(self) -> None:
        backend = FakeServiceNow()
        deps = make_deps(servicenow=backend)
        output = act(reasoned_state(incident=snapshot(VPN)), deps)["output"]
        assert output["outcome"] == "suggested"
        assert output["write_back"] == "written"
        sys_id, payload = backend.updates[0]
        body = payload.to_table_api_body()
        assert sys_id == VPN["sys_id"]
        assert body["x_2215032_ai_inc_0_ai_suggestion"].startswith("1. Confirm")
        assert body["x_2215032_ai_inc_0_ai_human_review_required"] == "true"
        assert body["x_2215032_ai_inc_0_ai_processing_state"] == "awaiting_approval"
        assert "x_2215032_ai_inc_0_ai_processing_end" not in body
        assert body["x_2215032_ai_inc_0_ai_processing_start"].startswith("2026-09-08")
        assert output["approval_required"] is False
        assert body["x_2215032_ai_inc_0_ai_confidence"] == "0.82"
        assert body["x_2215032_ai_inc_0_ai_model_name"] == "gemini/gemini-3.5-flash"
        assert body["x_2215032_ai_inc_0_ai_classification"] == "network"
        assert body["work_notes"].startswith("AI Suggested Response drafted. Confidence 0.82.")
        # Nothing outside §11.6 is ever called.
        assert set(deps.servicenow.calls) <= set(PERMITTED_ACTIONS)
        assert "comments" not in body

    def test_high_risk_escalation_note(self) -> None:
        backend = FakeServiceNow()
        state = base_state(incident=snapshot(ORDER_P1), classification=classification("software"))
        state |= determine_risk(state, make_deps())
        state["eligibility"] = {"eligible": True, "reasons": []}
        output = act(state, make_deps(servicenow=backend))["output"]
        assert output["outcome"] == "escalated_high_risk"
        body = backend.updates[0][1].to_table_api_body()
        assert body["work_notes"].startswith(
            "AI Suggested Response: risk assessed as high before retrieval — Priority 1"
        )
        assert "x_2215032_ai_inc_0_ai_suggestion" not in body

    def test_no_evidence_note_names_the_best_match(self) -> None:
        backend = FakeServiceNow()
        retriever = FakeRetriever(hits=[evidence("KB0004", relevance=0.31, title="Print queue")])
        deps = make_deps(servicenow=backend, retriever=retriever)
        state = base_state(incident=snapshot(PRINTER), classification=classification("hardware"))
        state["eligibility"] = {"eligible": True, "reasons": []}
        state |= determine_risk(state, deps)
        state |= retrieve(state, deps)
        output = act(state, deps)["output"]
        assert output["outcome"] == "escalated_no_evidence"
        assert (
            "Best match KB0004 v2 (Print queue) scored 0.31 against a threshold of 0.55."
            in output["work_note"]
        )

    @pytest.mark.parametrize(
        ("retrieval", "expected"),
        [
            (
                {"category_filter": None, "hits": []},
                "The classification 'other' has no knowledge-base category, so no search was run.",
            ),
            (
                {"category_filter": "hardware", "hits": []},
                "Searched published hardware articles: nothing matched.",
            ),
            (
                {"category_filter": "inquiry,network", "hits": [], "sufficient": False},
                "Searched published inquiry, network articles: nothing matched.",
            ),
        ],
    )
    def test_no_evidence_note_wording(self, retrieval: dict[str, Any], expected: str) -> None:
        state = base_state(incident=snapshot(LEAVE), classification=classification("other"))
        state["eligibility"] = {"eligible": True, "reasons": []}
        state |= determine_risk(state, make_deps())
        state["retrieval"] = {
            "query": "q",
            "best_relevance": 0.0,
            "threshold": 0.55,
            "sufficient": False,
            "latency_ms": 0.0,
            **retrieval,
        }
        output = act(state, make_deps())["output"]
        assert expected in output["work_note"]

    def test_elevated_risk_suggestion_awaits_approval(self) -> None:
        state = reasoned_state(incident=snapshot(MFA), classification=classification("access"))
        state |= determine_risk(state, make_deps())
        output = act(state, make_deps())["output"]
        assert output["outcome"] == "suggested"
        assert output["processing_state"] == "awaiting_approval"
        assert output["approval_required"] is True
        assert "Approval required before any action" in output["work_note"]

    def test_ineligible_writes_nothing(self) -> None:
        backend = FakeServiceNow()
        state = base_state(incident=snapshot(VPN, ai_enabled=False))
        state |= validate(state, make_deps())
        output = act(state, make_deps(servicenow=backend))["output"]
        assert output["outcome"] == "skipped_ineligible"
        assert backend.updates == [] and backend.notes == []

    def test_lock_taken_between_read_and_write_is_respected(self) -> None:
        backend = FakeServiceNow()
        backend.write_error = ServiceNowHumanLockError("locked")
        output = act(reasoned_state(incident=snapshot(VPN)), make_deps(servicenow=backend))[
            "output"
        ]
        assert output["outcome"] == "skipped_human_lock"
        assert output["write_back"] == "skipped"

    def test_missing_start_time_is_omitted_not_sent_as_null(self) -> None:
        backend = FakeServiceNow()
        state = reasoned_state(incident=snapshot(VPN))
        state.pop("started_at")
        act(state, make_deps(servicenow=backend))
        body = backend.updates[0][1].to_table_api_body()
        assert "x_2215032_ai_inc_0_ai_processing_start" not in body

    def test_dry_run_records_but_does_not_write(self) -> None:
        backend = FakeServiceNow()
        deps = make_deps(servicenow=backend, agent_write_back_enabled=False)
        output = act(reasoned_state(incident=snapshot(VPN)), deps)["output"]
        assert output["write_back"] == "dry_run"
        assert backend.updates == []

    def test_gate_failure_blocks(self) -> None:
        state = reasoned_state(incident=snapshot(VPN))
        state["safety"] = {**state["safety"], "passed": False, "reason": "secret in draft"}
        output = act(state, make_deps())["output"]
        assert output["outcome"] == "escalated_blocked"
        assert "safety_check check: secret in draft" in output["work_note"]

    def test_forbidden_action_does_not_exist(self) -> None:
        deps = make_deps()
        with pytest.raises(ActionNotPermittedError):
            deps.servicenow._call("resolve_incident", VPN["sys_id"], lambda b: b.get_incident(""))
        assert "resolve_incident" not in deps.servicenow.calls


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, Outcome.SUGGESTED),
        ({"eligibility": {"eligible": False, "reasons": ["x"]}}, Outcome.SKIPPED_INELIGIBLE),
        ({"risk": None}, Outcome.ESCALATED_HIGH_RISK),
        ({"retrieval": None}, Outcome.ESCALATED_NO_EVIDENCE),
        ({"diagnosis": None}, Outcome.ESCALATED_NO_EVIDENCE),
        ({"verification": None}, Outcome.ESCALATED_BLOCKED),
        ({"confidence": None}, Outcome.ESCALATED_LOW_CONFIDENCE),
    ],
)
def test_decide_outcome_fails_closed_on_missing_sections(
    changes: dict[str, Any], expected: Outcome
) -> None:
    state = reasoned_state(incident=snapshot(VPN))
    for key, value in changes.items():
        if value is None:
            state.pop(key)
        else:
            state[key] = value
    assert decide_outcome(state) is expected


# -- policy helpers -------------------------------------------------------------------------


class TestPolicy:
    def test_service_name_ignores_bare_references(self) -> None:
        assert service_name({"business_service": {"value": "abc", "link": "x"}}) is None
        assert service_name({"business_service": {"display_value": "Identity"}}) == "identity"
        assert service_name({"service": " corporate-VPN "}) == "corporate-vpn"

    @pytest.mark.parametrize(
        ("impact", "urgency", "recorded", "expected"),
        [(1, 1, 5, 1), (2, 2, 2, 2), (3, 3, 2, 2), (None, None, 4, 4), (3, 1, None, 2)],
    )
    def test_effective_priority(
        self, impact: int | None, urgency: int | None, recorded: int | None, expected: int
    ) -> None:
        incident = IncidentSnapshot(
            sys_id="s", number="INC1", priority=recorded, impact=impact, urgency=urgency
        )
        assert effective_priority(incident) == expected

    def test_mfa_pattern_does_not_flag_plain_vpn(self) -> None:
        incident = IncidentSnapshot.model_validate(snapshot(VPN))
        label = ClassificationResult(label=Classification.NETWORK, rationale="", model_confidence=1)
        assert assess_risk(incident, label, risk_priorities=[1]).level is RiskLevel.LOW

    def test_eligibility_accepts_the_supported_categories_case_insensitively(self) -> None:
        incident = IncidentSnapshot.model_validate(snapshot(VPN))
        result = check_eligibility(
            incident, event_number="INC0010023", supported_categories=["NETWORK"]
        )
        assert result.eligible
