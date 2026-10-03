"""Real urgency: attacks, outages, repeats, and lowering a priority-only HIGH.

Design: docs/barq_agentic_platform_design.md §6.3; scenarios A5, A6, A10, S2.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from agent.nodes import act, determine_risk
from agent.triage import TriageOutput, similarity
from tests.agent_support import VPN, FakeServiceNow, make_deps
from tests.test_nodes import base_state, classification, reasoned_state, snapshot

CALLER = "c" * 32
ONE_PERSON = TriageOutput(
    affects_one_person=True,
    business_critical=False,
    outage_likely=False,
    security_related=False,
    reason="only the caller's own VPN login is affected",
)


def risk_for(
    record: dict[str, Any],
    *,
    related: dict[str, Any] | None = None,
    triage: TriageOutput | None = None,
    **settings: Any,
) -> dict[str, Any]:
    backend = FakeServiceNow({"r": record})
    if related is not None:
        backend.related = {"recent_same_category": [], "caller_recent": [], **related}
    deps = make_deps(servicenow=backend, **settings)
    if triage is not None:
        deps.llm.answers["triage"] = triage
    state = base_state(record, incident=snapshot(record), classification=classification())
    return determine_risk(state, deps)["risk"]


def vpn(**fields: Any) -> dict[str, Any]:
    return {**VPN, "caller_id": CALLER, **fields}


def similar(n: int) -> list[dict[str, str]]:
    return [
        {"number": f"INC00099{i:02d}", "short_description": VPN["short_description"]}
        for i in range(n)
    ]


def test_similarity_ignores_filler_words() -> None:
    assert similarity("VPN invalid credentials", "vpn says invalid credentials again") > 0.5
    assert similarity("Printer jam on floor 3", "VPN invalid credentials") == 0.0


def test_bracketed_prefixes_and_one_shared_word_are_not_similarity() -> None:
    # A shared ticket prefix made unrelated test incidents look like an outage live.
    assert (
        similarity(
            "[BARQ-TEST-2026-10-03] VPN invalid credentials",
            "[BARQ-TEST-2026-10-03] Intranet portal times out",
        )
        == 0.0
    )
    assert similarity("[URGENT] laptop battery", "[URGENT] laptop screen flickers") == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "I clicked a link in an email and now my files are encrypted",
        "Someone else logged in to my account last night",
        "Suspicious login from another country on my mailbox",
    ],
)
def test_a_reported_attack_goes_to_a_person(text: str) -> None:
    risk = risk_for(vpn(description=text))
    assert risk["level"] == "high"
    assert "security incident" in risk["reasons"][0]


def test_a_burst_of_similar_incidents_is_a_likely_outage() -> None:
    risk = risk_for(vpn(), related={"recent_same_category": similar(2)})
    assert risk["level"] == "high"
    assert "likely outage" in risk["reasons"][0]
    assert "INC0009900" in risk["reasons"][0]


def test_outage_words_are_a_likely_outage() -> None:
    risk = risk_for(vpn(description="Nobody can connect to the VPN, the whole office is down."))
    assert risk["level"] == "high"
    assert "several people" in risk["reasons"][0]


def test_one_unrelated_incident_is_not_an_outage() -> None:
    other = [{"number": "INC0009999", "short_description": "Printer jam on floor 3"}]
    assert risk_for(vpn(), related={"recent_same_category": other})["level"] == "low"


def test_a_repeat_from_the_same_caller_needs_approval() -> None:
    risk = risk_for(vpn(), related={"caller_recent": similar(1)})
    assert risk["level"] == "elevated"
    assert risk["approval_required"] is True
    assert "repeat from the same caller" in risk["reasons"][0]


P1 = {"priority": "1", "impact": "1", "urgency": "1"}


def test_priority_one_is_lowered_only_when_every_rule_holds_and_the_model_agrees() -> None:
    risk = risk_for(vpn(**P1), related={}, triage=ONE_PERSON, agent_reassess_priority=True)
    assert risk["level"] == "low"
    assert risk["reassessed"] is True
    assert risk["reasons"][0].startswith("Reassessed by BARQ AI Agent")


@pytest.mark.parametrize(
    "case",
    [
        {"settings": {"agent_reassess_priority": False}},
        {"triage": TriageOutput(**{**ONE_PERSON.model_dump(), "business_critical": True})},
        {"triage": TriageOutput(**{**ONE_PERSON.model_dump(), "affects_one_person": False})},
        {"related": {"recent_same_category": similar(2)}},
        {"related": {"caller_recent": similar(1)}},
        {"record": {"description": "The whole office lost the VPN"}},
        {"record": {"description": "I clicked a link in a phishing email"}},
    ],
)
def test_otherwise_priority_one_stays_with_a_person(case: dict[str, Any]) -> None:
    settings = {"agent_reassess_priority": True, **case.get("settings", {})}
    risk = risk_for(
        vpn(**P1, **case.get("record", {})),
        related=case.get("related", {}),
        triage=case.get("triage", ONE_PERSON),
        **settings,
    )
    assert risk["level"] == "high"
    assert risk["reassessed"] is False


def test_a_failed_look_around_never_lowers_and_says_so() -> None:
    backend = FakeServiceNow({"r": vpn(**P1)})

    async def broken(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("down")

    backend.related_incidents = broken  # type: ignore[method-assign]
    deps = make_deps(servicenow=backend, agent_reassess_priority=True)
    deps.llm.answers["triage"] = ONE_PERSON
    state = base_state(vpn(**P1), incident=snapshot(vpn(**P1)), classification=classification())
    risk = determine_risk(state, deps)["risk"]
    assert risk["level"] == "high"
    assert "Could not look at related incidents" in risk["reasons"][-1]


def test_the_work_note_explains_a_reassessment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["agent.nodes.act"],
        "_request_human_decision",
        lambda payload: {"decision": "approved", "decided_by": "t", "source": "no_graph"},
    )
    state = reasoned_state(incident=snapshot(vpn()))
    state["risk"] = {
        "level": "low",
        "reasons": ["Reassessed by BARQ AI Agent: handled as low risk although priority 1 …"],
        "approval_required": False,
        "service_tier": 2,
        "reassessed": True,
    }
    output = act(state, make_deps())["output"]
    assert output["work_note"].startswith("Reassessed by BARQ AI Agent")
    assert "Take over from AI" in output["work_note"]


def test_a_question_to_the_caller_keeps_the_reassessment_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.conversation import ClarifyingQuestionOutput

    state = reasoned_state(incident=snapshot(vpn()))
    state["risk"] = {
        "level": "low",
        "reasons": ["Reassessed by BARQ AI Agent: one person"],
        "approval_required": False,
        "service_tier": 2,
        "reassessed": True,
    }
    state["confidence"] = {**state["confidence"], "passed": False}
    deps = make_deps(agent_autonomy_level="autonomous")
    deps.llm.answers["clarifying_question"] = ClarifyingQuestionOutput(
        useful=True, question="Which error do you see?"
    )
    output = act(state, deps)["output"]
    assert output["outcome"] == "asked_caller"
    assert output["work_note"].startswith("Reassessed by BARQ AI Agent")


def test_a_repeat_with_no_fix_goes_to_an_engineer_not_a_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.conversation import ClarifyingQuestionOutput

    seen: list[Any] = []
    monkeypatch.setattr(
        sys.modules["agent.nodes.act"],
        "_request_human_decision",
        lambda payload: seen.append(payload) or {"decision": "rejected", "decided_by": "t"},
    )
    state = reasoned_state(incident=snapshot(vpn()))
    state["risk"] = {
        "level": "elevated",
        "reasons": ["repeat from the same caller (INC0009900)"],
        "approval_required": True,
        "service_tier": 2,
        "reassessed": False,
    }
    state["confidence"] = {**state["confidence"], "passed": False}
    deps = make_deps(agent_autonomy_level="autonomous")
    deps.llm.answers["clarifying_question"] = ClarifyingQuestionOutput(useful=True, question="?")
    output = act(state, deps)["output"]
    assert output["outcome"] != "asked_caller"
    assert len(seen) == 1
