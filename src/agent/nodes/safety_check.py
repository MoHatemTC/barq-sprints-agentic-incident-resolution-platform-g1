"""
Is the model-generated output structurally and
semantically valid, and does it comply with the action contract?
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.guardrails.output_validation import ValidationIssue, run_all
from agent.state import AgentState, Draft, EvidenceItem, GateResult, RetrievalResult


def safety_check(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    with deps.tracer.span(
        "guardrail.safety_check",
        as_type="guardrail",
        input={"stage": "safety_check"},
    ) as span:
        draft_raw = state.get("draft")
        if draft_raw is None:
            # generate produced nothing to check. Fail closed, same as every
            # other gate in this graph when its input section is missing.
            gate = GateResult(
                gate="safety_check",
                passed=False,
                implemented=True,
                reason="no draft was recorded to validate",
            )
            span.update(output={"passed": False, "reason": gate.reason})
            return {"safety": gate.model_dump(mode="json")}

        draft = Draft.model_validate(draft_raw)
        retrieval_raw = state.get("retrieval")
        evidence: list[EvidenceItem] = (
            RetrievalResult.model_validate(retrieval_raw).hits if retrieval_raw else []
        )

        issues = run_all(draft, evidence)
        passed = not issues
        gate = GateResult(
            gate="safety_check",
            passed=passed,
            implemented=True,
            checks=[_issue_to_check(issue) for issue in issues],
            reason=None if passed else _summarize(issues),
        )
        span.update(
            output={
                "passed": passed,
                "issue_count": len(issues),
                "categories": sorted({issue.category.value for issue in issues}),
            }
        )
        return {"safety": gate.model_dump(mode="json")}


def _issue_to_check(issue: ValidationIssue) -> dict[str, Any]:
    return {
        "category": issue.category.value,
        "detail": issue.detail,
        "step_index": issue.step_index,
    }


def _summarize(issues: list[ValidationIssue]) -> str:
    categories = sorted({issue.category.value for issue in issues})
    return f"{len(issues)} check(s) failed: {', '.join(categories)}"


__all__ = ["safety_check"]
