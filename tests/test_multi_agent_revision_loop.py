"""Unit and integration tests for the multi-agent revision loop (Sprint 3 / Phase 5.2).

Deterministic, fast, CI-safe tests with mocked LLM calls and in-memory checkpointer:
- Scenario A (Clean Pass): Initial draft passes Critic on attempt 0 -> safety_check
  -> act(SUGGESTED).
- Scenario B (Successful Correction): Ungrounded draft -> Critic rejects -> Resolution revises ->
  Critic passes on attempt 1 -> act(SUGGESTED).
- Scenario C (Budget Exhaustion): Critic rejects repeatedly up to agent_max_revisions ->
  conditional edge routes to act(ESCALATED_BLOCKED) with audit notes.
- Scenario D (State Auditability & Checkpoint Resume): Verify revision_count integrity,
  state transitions, and resume after transient failure during revision.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph, run_graph
from agent.prompts import (
    CriticOutput,
    GenerateOutput,
    StepOutput,
)
from agent.state import EventPayload, UnsupportedClaim
from app.workers.retry_policy import RetryableError
from tests.agent_support import (
    EXECUTION_ID,
    VPN,
    FakeLLM,
    FakeServiceNow,
    event_for,
    make_deps,
    vpn_answers,
)


def run(
    record: dict[str, Any],
    deps: Any,
    *,
    checkpointer: Any = None,
    attempt: int = 1,
    execution_id: str = EXECUTION_ID,
) -> dict[str, Any]:
    graph = build_graph(deps, checkpointer=checkpointer)
    return run_graph(
        graph,
        EventPayload.model_validate(event_for(record)),
        execution_id=execution_id,
        correlation_id="corr-revision-test",
        attempt=attempt,
        deps=deps,
    )


class TestScenarioACleanPass:
    def test_clean_pass_on_attempt_zero(self) -> None:
        """Scenario A: Grounded initial draft passes Critic immediately on attempt 0."""
        backend = FakeServiceNow()
        deps = make_deps(servicenow=backend)

        result = run(VPN, deps)

        expected_path = [
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
        ]
        assert result["path"] == expected_path
        assert result["outcome"] == "suggested"
        assert result["suggested"] is True
        assert result["escalated"] is False
        assert result["write_back"] == "written"
        assert result["suggestion"].startswith("1. Confirm the password was changed")

        # Verify ServiceNow update
        assert len(backend.updates) == 1
        sys_id, payload = backend.updates[0]
        body = payload.to_table_api_body()
        assert sys_id == VPN["sys_id"]
        assert "x_2215032_ai_inc_0_ai_suggestion" in body
        assert body["x_2215032_ai_inc_0_ai_human_review_required"] == "true"
        assert body["x_2215032_ai_inc_0_ai_processing_state"] == "awaiting_approval"


class TestScenarioBSuccessfulCorrection:
    def test_ungrounded_draft_corrected_on_attempt_one(self) -> None:
        """Scenario B: Critic rejects attempt 0; Resolution revises; Critic passes attempt 1."""
        backend = FakeServiceNow()

        ungrounded_draft = GenerateOutput(
            steps=[
                StepOutput(
                    text="Reboot the core authentication gateway.",
                    article_id="KB0001-v2",
                    section="Resolution",
                )
            ]
        )
        grounded_draft = GenerateOutput(
            steps=[
                StepOutput(
                    text="Confirm the password was changed in the last 24 hours.",
                    article_id="KB0001-v2",
                    section="Resolution",
                ),
                StepOutput(
                    text="Clear the cached VPN credential and reconnect.",
                    article_id="KB0001-v2",
                    section="Resolution",
                ),
            ]
        )

        critic_reject = CriticOutput(
            passed=False,
            invalid_citations=[],
            unsupported_claims=[
                UnsupportedClaim(
                    step_index=1,
                    claim="Reboot the core authentication gateway.",
                    reason="KB0001 does not instruct to reboot the authentication gateway.",
                    citation="KB0001-v2 §Resolution",
                )
            ],
            safety_issues=["Unauthorized reboot action."],
            feedback_instructions="Remove reboot step and advise clearing client credential cache.",
        )
        critic_pass = CriticOutput(
            passed=True,
            invalid_citations=[],
            unsupported_claims=[],
            feedback_instructions="",
        )

        answers = vpn_answers()
        answers["generate"] = [ungrounded_draft, grounded_draft]
        answers["verify_evidence"] = [critic_reject, critic_pass]
        llm = FakeLLM(answers)
        deps = make_deps(llm=llm, servicenow=backend)

        result = run(VPN, deps)

        expected_path = [
            "load",
            "validate",
            "classify",
            "determine_risk",
            "retrieve",
            "diagnose",
            "generate",
            "verify_evidence",
            "generate",
            "verify_evidence",
            "safety_check",
            "confidence_check",
            "act",
        ]
        assert result["path"] == expected_path
        assert result["outcome"] == "suggested"
        assert result["suggested"] is True

        # Assert final suggestion is the revised grounded draft
        assert "Clear the cached VPN credential and reconnect." in result["suggestion"]
        assert "Reboot the core authentication gateway." not in result["suggestion"]

        # Assert second generate prompt received the previous draft and critic feedback
        gen_calls = [c for c in llm.calls if c["purpose"] == "generate"]
        assert len(gen_calls) == 2
        rev_prompt = gen_calls[1]["prompt"]
        assert "<previous_draft>" in rev_prompt
        assert "Reboot the core authentication gateway." in rev_prompt
        assert "<critic_feedback>" in rev_prompt
        assert "Unauthorized reboot action." in rev_prompt
        assert "Remove reboot step and advise clearing client credential cache." in rev_prompt


class TestScenarioCBudgetExhaustion:
    def test_budget_exhaustion_escalates_to_blocked_with_critic_notes(self) -> None:
        """Scenario C: Draft fails verification repeatedly up to agent_max_revisions -> act."""
        backend = FakeServiceNow()

        hallucinated_step = GenerateOutput(
            steps=[
                StepOutput(
                    text="Flush corporate DNS cache on server.",
                    article_id="KB0001-v2",
                    section="Resolution",
                )
            ]
        )

        critic_reject_1 = CriticOutput(
            passed=False,
            invalid_citations=[],
            unsupported_claims=[
                UnsupportedClaim(
                    step_index=1,
                    claim="Flush corporate DNS cache on server.",
                    reason="KB0001 does not instruct server DNS flush.",
                    citation="KB0001-v2 §Resolution",
                )
            ],
            safety_issues=[],
            feedback_instructions="Do not flush server DNS cache (Attempt 0).",
        )
        critic_reject_2 = CriticOutput(
            passed=False,
            invalid_citations=[],
            unsupported_claims=[
                UnsupportedClaim(
                    step_index=1,
                    claim="Flush corporate DNS cache on server.",
                    reason="Persistent ungrounded claim.",
                    citation="KB0001-v2 §Resolution",
                )
            ],
            safety_issues=[],
            feedback_instructions="Do not flush server DNS cache (Attempt 1).",
        )
        critic_reject_3 = CriticOutput(
            passed=False,
            invalid_citations=[],
            unsupported_claims=[
                UnsupportedClaim(
                    step_index=1,
                    claim="Flush corporate DNS cache on server.",
                    reason="Persistent ungrounded claim.",
                    citation="KB0001-v2 §Resolution",
                )
            ],
            safety_issues=[],
            feedback_instructions="Do not flush server DNS cache (Attempt 2).",
        )

        answers = vpn_answers()
        answers["generate"] = [hallucinated_step, hallucinated_step, hallucinated_step]
        answers["verify_evidence"] = [critic_reject_1, critic_reject_2, critic_reject_3]
        llm = FakeLLM(answers)
        deps = make_deps(llm=llm, servicenow=backend, agent_max_revisions=2)

        result = run(VPN, deps)

        expected_path = [
            "load",
            "validate",
            "classify",
            "determine_risk",
            "retrieve",
            "diagnose",
            "generate",
            "verify_evidence",
            "generate",
            "verify_evidence",
            "generate",
            "verify_evidence",
            "act",
        ]
        assert result["path"] == expected_path
        assert result["outcome"] == "escalated_blocked"
        assert result["escalated"] is True
        assert result["suggested"] is False

        # Verify ServiceNow incident update withheld the suggestion and recorded work notes
        assert len(backend.updates) == 1
        body = backend.updates[0][1].to_table_api_body()
        assert "x_2215032_ai_inc_0_ai_suggestion" not in body
        assert body["x_2215032_ai_inc_0_ai_human_review_required"] == "true"
        assert body["x_2215032_ai_inc_0_ai_processing_state"] == "awaiting_approval"

        # Comprehensive work note must mention gate failure and critic reasons
        work_notes = body["work_notes"]
        assert "draft blocked by the verify_evidence check" in work_notes
        assert "Do not flush server DNS cache (Attempt 2)." in work_notes


class TestScenarioDStateAuditabilityAndResilience:
    def test_state_revision_count_increments_faithfully(self) -> None:
        """Scenario D1: revision_count strictly increments on each revision pass."""
        backend = FakeServiceNow()
        bad_draft = GenerateOutput(
            steps=[
                StepOutput(
                    text="Unsupported step.",
                    article_id="KB0001-v2",
                    section="Resolution",
                )
            ]
        )
        good_draft = GenerateOutput(
            steps=[
                StepOutput(
                    text="Clear the cached VPN credential.",
                    article_id="KB0001-v2",
                    section="Resolution",
                )
            ]
        )
        critic_reject = CriticOutput(
            passed=False,
            invalid_citations=[],
            unsupported_claims=[
                UnsupportedClaim(
                    step_index=1,
                    claim="Unsupported step.",
                    reason="Not in KB.",
                    citation="KB0001-v2 §Resolution",
                )
            ],
            feedback_instructions="Fix step.",
        )
        critic_pass = CriticOutput(
            passed=True,
            invalid_citations=[],
            unsupported_claims=[],
            feedback_instructions="",
        )

        answers = vpn_answers()
        answers["generate"] = [bad_draft, good_draft]
        answers["verify_evidence"] = [critic_reject, critic_pass]
        llm = FakeLLM(answers)
        deps = make_deps(llm=llm, servicenow=backend)

        result = run(VPN, deps)
        assert result["outcome"] == "suggested"

        # Verify LLM calls count
        assert llm.purposes() == [
            "injection_classifier",
            "classify",
            "diagnose",
            "generate",
            "verify_evidence",
            "generate",
            "verify_evidence",
        ]

    def test_checkpoint_resumes_cleanly_during_revision_loop(self) -> None:
        """Scenario D2: A transient failure on revision generate resumes from checkpoint."""
        saver = InMemorySaver()
        backend = FakeServiceNow()

        bad_draft = GenerateOutput(
            steps=[
                StepOutput(
                    text="Bad step.",
                    article_id="KB0001-v2",
                    section="Resolution",
                )
            ]
        )
        good_draft = GenerateOutput(
            steps=[
                StepOutput(
                    text="Clear the cached VPN credential.",
                    article_id="KB0001-v2",
                    section="Resolution",
                )
            ]
        )
        critic_reject = CriticOutput(
            passed=False,
            invalid_citations=[],
            unsupported_claims=[
                UnsupportedClaim(
                    step_index=1,
                    claim="Bad step.",
                    reason="Not in KB.",
                    citation="KB0001-v2 §Resolution",
                )
            ],
            feedback_instructions="Fix bad step.",
        )
        critic_pass = CriticOutput(
            passed=True,
            invalid_citations=[],
            unsupported_claims=[],
            feedback_instructions="",
        )

        answers_attempt1 = vpn_answers()
        # Attempt 1: First generate succeeds, first verify rejects, second generate fails
        answers_attempt1["generate"] = [
            bad_draft,
            RetryableError("LLM rate limit on revision"),
        ]
        answers_attempt1["verify_evidence"] = [critic_reject]
        llm1 = FakeLLM(answers_attempt1)
        deps1 = make_deps(llm=llm1, servicenow=backend)

        with pytest.raises(RetryableError, match="LLM rate limit"):
            run(VPN, deps1, checkpointer=saver, attempt=1)

        # Confirm nodes completed before failure
        assert llm1.purposes() == [
            "injection_classifier",
            "classify",
            "diagnose",
            "generate",
            "verify_evidence",
            "generate",
        ]
        assert backend.calls == ["read_incident"]

        # Attempt 2: Resume with model recovered
        answers_attempt2 = vpn_answers()
        answers_attempt2["generate"] = good_draft
        answers_attempt2["verify_evidence"] = critic_pass
        llm2 = FakeLLM(answers_attempt2)
        deps2 = make_deps(llm=llm2, servicenow=backend)

        result = run(VPN, deps2, checkpointer=saver, attempt=2)

        assert result["resumed"] is True
        assert result["outcome"] == "suggested"
        assert result["suggested"] is True
        # Earlier nodes (classify, diagnose, initial generate, initial verify) were NOT rerun
        assert llm2.purposes() == ["generate", "verify_evidence"]
        # load was skipped on resume; read_incident was not called again
        assert backend.calls == [
            "read_incident",
            "write_ai_fields",
            "write_execution_log",
        ]
