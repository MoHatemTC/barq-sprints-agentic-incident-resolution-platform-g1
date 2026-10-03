"""Input screening for chat messages (enforced, fail-closed).

Mirrors the incident pipeline's guardrail order (``agent.nodes.load``):
deterministic pattern screening on the raw text, deterministic redaction,
enforced residual-PII detection, then semantic injection classification. Chat
always runs PII in enforced mode: if the detector cannot run, the turn is
blocked with an explicit message rather than sent to an answering model.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.guardrails.input_screening import screen_text
from agent.guardrails.pii_detection import protect_residual_pii
from agent.guardrails.semantic_injection_classifier import classify_injection
from agent.llm import LLMClient
from agent.prompts import PIIText
from observability.redaction import REDACTED, redact_text_with_count

_MAX_SCREENING_CHARS = 6000

#: Explicit user-facing refusals per blocked layer. Never leak screening detail.
_BLOCKED_MESSAGES = {
    "pattern_screening": (
        "This message was withheld by input screening. Rephrase the question "
        "without embedded instructions."
    ),
    "residual_pii": (
        "Your message was withheld because privacy screening is unavailable right "
        "now. Please try again later."
    ),
    "classifier_unavailable": (
        "Your message was withheld because safety screening is unavailable right "
        "now. Please try again later."
    ),
    "semantic_classifier": ("This message was withheld by input screening. Rephrase the question."),
}


@dataclass(frozen=True, slots=True)
class ChatScreening:
    """Privacy-safe outcome of the chat screening pipeline."""

    blocked: bool
    sanitized_text: str
    layer: str | None
    refusal: str | None
    redaction_count: int


def screen_chat_input(
    llm: LLMClient,
    text: str,
    *,
    max_chars: int = _MAX_SCREENING_CHARS,
) -> ChatScreening:
    """Screen one chat message; return sanitized text or an explicit refusal."""
    if len(text) > max_chars:
        return ChatScreening(
            blocked=True,
            sanitized_text=REDACTED,
            layer="pattern_screening",
            refusal=f"Message too long (limit {max_chars} characters).",
            redaction_count=0,
        )

    # 1. Deterministic pattern screening on the RAW text, then 2. redaction.
    pattern_result = screen_text(text)
    sanitized, redaction_count = redact_text_with_count(text)

    if pattern_result.flagged:
        return ChatScreening(
            blocked=True,
            sanitized_text=REDACTED,
            layer="pattern_screening",
            refusal=_BLOCKED_MESSAGES["pattern_screening"],
            redaction_count=redaction_count,
        )

    # 3. Enforced residual-PII detection on the redacted text.
    pii = protect_residual_pii(
        llm,
        PIIText(short_description="", description=sanitized),
        max_chars=max_chars,
    )
    if not pii.available or pii.protected is None:
        return ChatScreening(
            blocked=True,
            sanitized_text=REDACTED,
            layer="residual_pii",
            refusal=_BLOCKED_MESSAGES["residual_pii"],
            redaction_count=redaction_count,
        )
    protected = pii.protected.description

    # 4. Semantic injection classification on the protected text.
    classifier = classify_injection(llm, protected)
    if not classifier.available:
        return ChatScreening(
            blocked=True,
            sanitized_text=REDACTED,
            layer="classifier_unavailable",
            refusal=_BLOCKED_MESSAGES["classifier_unavailable"],
            redaction_count=redaction_count,
        )
    if classifier.is_injection:
        return ChatScreening(
            blocked=True,
            sanitized_text=REDACTED,
            layer="semantic_classifier",
            refusal=_BLOCKED_MESSAGES["semantic_classifier"],
            redaction_count=redaction_count,
        )

    return ChatScreening(
        blocked=False,
        sanitized_text=protected,
        layer=None,
        refusal=None,
        redaction_count=redaction_count,
    )


__all__ = ["ChatScreening", "screen_chat_input"]
