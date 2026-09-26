"""Best-effort plain-language explanations for final registry refusals."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent.llm import LLMClient
from agent.prompts import (
    REFUSAL_EXPLAINER_SYSTEM,
    RefusalExplanation,
    refusal_explanation_prompt,
)
from agent.tools.permissions import PermissionClass


class RefusalFacts(BaseModel):
    """The complete and deliberately narrow data boundary for the explainer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(max_length=128)
    permission_class: PermissionClass | None
    execution_id: str = Field(max_length=128)
    refusal_reason: str = Field(max_length=64)
    approval_id: str | None = Field(default=None, max_length=128)


_FALLBACK_EXPLANATIONS = {
    "unknown_tool": "The requested tool is not permitted by server policy.",
    "approval_missing": "This high-risk action requires an approval that was not found.",
    "approval_rejected": (
        "This high-risk action was blocked because the latest approval decision is rejected."
    ),
    "approval_cancelled": (
        "This high-risk action was blocked because the latest approval decision is cancelled."
    ),
    "approval_expired": (
        "This high-risk action was blocked because the latest approval decision is expired."
    ),
    "approval_scope_invalid": (
        "This high-risk action was blocked because its approval scope could not be safely verified."
    ),
    "approval_ambiguous": (
        "This high-risk action was blocked because the latest approval decision was ambiguous."
    ),
    "approval_check_failed": (
        "The action was blocked because approval could not be safely verified."
    ),
    "invalid_context": "The action was blocked because its execution context was invalid.",
    "audit_unavailable": (
        "The action was blocked because required enforcement auditing was unavailable."
    ),
}
_GENERIC_FALLBACK = "The requested action was blocked by server policy."


def fallback_explanation(refusal_reason: str) -> str:
    """Return a deterministic explanation containing no caller-controlled details."""
    return _FALLBACK_EXPLANATIONS.get(refusal_reason, _GENERIC_FALLBACK)


class RefusalExplainer:
    """Translate final refusal facts into prose without participating in enforcement."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def explain(self, facts: RefusalFacts) -> str:
        """Explain ``facts`` or return the safe static explanation on any model failure."""
        fallback = fallback_explanation(facts.refusal_reason)
        try:
            response = self._llm.structured(
                purpose="tool_refusal",
                system=REFUSAL_EXPLAINER_SYSTEM,
                prompt=refusal_explanation_prompt(
                    tool_name=facts.tool_name,
                    permission_class=(
                        facts.permission_class.value if facts.permission_class is not None else None
                    ),
                    execution_id=facts.execution_id,
                    refusal_reason=facts.refusal_reason,
                    approval_id=facts.approval_id,
                ),
                schema=RefusalExplanation,
            )
            if not isinstance(response, RefusalExplanation):
                raise TypeError("refusal explainer returned an invalid response")
            return response.message
        except Exception:
            return fallback


__all__ = ["RefusalExplainer", "RefusalFacts", "fallback_explanation"]
