"""``verify_evidence`` — The Critic/Verifier Agent (Sprint 3.1).

Independently validates drafted resolution steps against retrieved evidence chunks using:
1. Deterministic pre-check: All cited article IDs and sections exist in retrieved hits.
2. Semantic verification: Isolated LLM completion verifying the evidence substantively
   supports the claimed action.
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.prompts import (
    CRITIC_SYSTEM,
    CriticOutput,
    critic_prompt,
    evidence_block,
)
from agent.state import (
    AgentState,
    CriticFeedback,
    Diagnosis,
    Draft,
    DraftStep,
    EvidenceItem,
    GateResult,
    InvalidCitation,
    RetrievalResult,
    UnsupportedClaim,
)


def validate_citation(
    step_index: int,
    step: DraftStep,
    hits: list[EvidenceItem],
) -> InvalidCitation | None:
    """Deterministic citation check: confirms article_id and section exist in retrieved hits."""
    matching = [hit for hit in hits if hit.article_id == step.article_id]
    if not matching:
        return InvalidCitation(
            step_index=step_index,
            citation=f"{step.article_id} §{step.section}",
            reason=f"Article ID '{step.article_id}' was not found in retrieved evidence.",
        )
    if not any(hit.section == step.section for hit in matching):
        return InvalidCitation(
            step_index=step_index,
            citation=f"{step.article_id} §{step.section}",
            reason=f"Cited section '{step.section}' was not found in article '{step.article_id}'.",
        )
    return None


def verify_evidence(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    draft_raw = state.get("draft")
    retrieval_raw = state.get("retrieval")
    diagnosis_raw = state.get("diagnosis")
    attempt = state.get("revision_count", 0)

    # Defensively fail closed if draft or retrieval is missing
    if not draft_raw or not retrieval_raw or not diagnosis_raw:
        result = GateResult(
            gate="verify_evidence",
            passed=False,
            implemented=True,
            reason="Missing draft, retrieval, or diagnosis section for verification.",
        )
        feedback = CriticFeedback(
            passed=False,
            attempt=attempt,
            feedback_instructions="Missing required state sections for verification.",
        )
        return {
            "verification": result.model_dump(mode="json"),
            "critic_feedback": feedback.model_dump(mode="json"),
        }

    draft = Draft.model_validate(draft_raw)
    retrieval = RetrievalResult.model_validate(retrieval_raw)
    diagnosis = Diagnosis.model_validate(diagnosis_raw)

    with deps.tracer.span(
        "agent.critic",
        as_type="agent",
        input={
            "attempt": attempt,
            "steps_count": len(draft.steps),
            "evidence_count": len(retrieval.hits),
            "cause": diagnosis.probable_cause,
        },
        metadata={
            "agent": "critic",
            "execution_id": state.get("execution_id"),
        },
    ) as span:
        # 1. Deterministic citation pre-check
        deterministic_invalid: list[InvalidCitation] = []
        valid_step_pairs: list[tuple[int, DraftStep]] = []

        for idx, step in enumerate(draft.steps, start=1):
            inv = validate_citation(idx, step, retrieval.hits)
            if inv is not None:
                deterministic_invalid.append(inv)
            else:
                valid_step_pairs.append((idx, step))

        # If any deterministic citations failed, construct failure feedback immediately
        # (Still run semantic verification on valid steps if any, or report citation errors)
        all_invalid = list(deterministic_invalid)
        all_unsupported: list[UnsupportedClaim] = []
        all_safety_issues: list[str] = []
        semantic_feedback_text = ""
        # The critic's own conclusion. Absent when there was nothing to ask about
        # (no step survived the deterministic citation check), in which case the
        # deterministic findings decide the verdict on their own.
        critic_passed: bool | None = None

        # 2. Semantic LLM verification (isolated completion for steps with valid citations)
        if valid_step_pairs:
            # Build prompt containing ONLY the candidate steps and their cited evidence chunks
            steps_text = "\n".join(
                f"Step {idx}: {step.text} [Citation: {step.article_id} §{step.section}]"
                for idx, step in valid_step_pairs
            )
            # Filter evidence to chunks cited by these steps (exact article_id and section)
            cited_sections = {(s.article_id, s.section) for _, s in valid_step_pairs}
            relevant_evidence = [
                h for h in retrieval.hits if (h.article_id, h.section) in cited_sections
            ]

            critic_response = deps.llm.structured(
                purpose="verify_evidence",
                system=CRITIC_SYSTEM,
                prompt=critic_prompt(
                    steps_text=steps_text,
                    evidence_text=evidence_block(relevant_evidence),
                ),
                schema=CriticOutput,
            )

            all_invalid.extend(critic_response.invalid_citations)
            all_unsupported.extend(critic_response.unsupported_claims)
            all_safety_issues.extend(critic_response.safety_issues)
            semantic_feedback_text = critic_response.feedback_instructions
            critic_passed = critic_response.passed

        # Consolidate verdict. The critic's own ``passed`` is honoured as well as
        # its enumerated findings: a model that returns ``passed=false`` with all
        # three lists empty — a natural answer when it spots a problem it cannot
        # itemise, and the one the system prompt invites — must not be recorded as
        # a pass. Manual §11.6 requires the evidence check to be code the model
        # cannot route around, and a gate that overrides the critic's verdict is
        # neither. The gate fails closed on disagreement.
        passed = (
            critic_passed is not False
            and len(all_invalid) == 0
            and len(all_unsupported) == 0
            and len(all_safety_issues) == 0
        )

        instructions_parts = []
        if deterministic_invalid:
            instructions_parts.append(
                f"Fix {len(deterministic_invalid)} invalid citations: "
                + "; ".join(f"Step {c.step_index} ({c.reason})" for c in deterministic_invalid)
            )
        if all_unsupported:
            instructions_parts.append(
                f"Fix {len(all_unsupported)} unsupported claims: "
                + "; ".join(f"Step {u.step_index} ({u.reason})" for u in all_unsupported)
            )
        if all_safety_issues:
            instructions_parts.append(
                f"Address {len(all_safety_issues)} safety issues: " + "; ".join(all_safety_issues)
            )
        if semantic_feedback_text:
            instructions_parts.append(semantic_feedback_text)

        feedback_instructions = " | ".join(instructions_parts) if instructions_parts else ""

        feedback = CriticFeedback(
            passed=passed,
            attempt=attempt,
            invalid_citations=all_invalid,
            unsupported_claims=all_unsupported,
            safety_issues=all_safety_issues,
            feedback_instructions=feedback_instructions,
        )

        reason = (
            None if passed else (feedback_instructions or "Draft failed evidence verification.")
        )
        result = GateResult(
            gate="verify_evidence",
            passed=passed,
            implemented=True,
            checks=[
                {
                    "check": "citations",
                    "valid": len(all_invalid) == 0,
                    "invalid_count": len(all_invalid),
                },
                {
                    "check": "claims",
                    "supported": len(all_unsupported) == 0,
                    "unsupported_count": len(all_unsupported),
                },
                {
                    "check": "safety",
                    "safe": len(all_safety_issues) == 0,
                    "safety_issues_count": len(all_safety_issues),
                },
            ],
            reason=reason,
        )

        result_payload = {
            "verification": result.model_dump(mode="json"),
            "critic_feedback": feedback.model_dump(mode="json"),
        }
        span.update(
            output={
                "passed": passed,
                "invalid_citations_count": len(all_invalid),
                "unsupported_claims_count": len(all_unsupported),
                "feedback_instructions": feedback_instructions,
            }
        )
        return result_payload
