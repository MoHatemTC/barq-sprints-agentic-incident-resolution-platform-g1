from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agent.prompts import (
    INJECTION_CLASSIFIER_SYSTEM,
    InjectionClassification,
    injection_classifier_prompt,
)

if TYPE_CHECKING:
    from agent.llm import LLMClient

logger = structlog.get_logger(__name__)

CLASSIFIER_PURPOSE = "injection_classifier"


@dataclass(frozen=True, slots=True)
class ClassifierOutcome:
    available: bool
    is_injection: bool = False
    reason: str | None = None
    # set only when available is false
    failure_category: str | None = None


def classify_injection(llm: LLMClient, sanitized_text: str) -> ClassifierOutcome:
    """Classify the given text for semantic injection attempts.

    sanitized_text: The text to classify, with sensitive information redacted
    and passed through pattern screening.

    Returns a ClassifierOutcome indicating whether the classification was
    successful and whether the text is considered an injection attempt.
    """
    if not sanitized_text.strip():
        logger.debug("Empty sanitized text; skipping classification")
        return ClassifierOutcome(available=True, is_injection=False, reason="Empty sanitized text")

    try:
        result = llm.structured(
            purpose=CLASSIFIER_PURPOSE,
            system=INJECTION_CLASSIFIER_SYSTEM,
            prompt=injection_classifier_prompt(sanitized_text),
            schema=InjectionClassification,
        )
    except Exception as exc:
        logger.warning("injection_classifier_unavailable: %s", type(exc).__name__)
        return ClassifierOutcome(available=False, failure_category=type(exc).__name__)

    if not isinstance(result, InjectionClassification):
        logger.warning("injection_classifier_malformed_output")
        return ClassifierOutcome(available=False, failure_category="malformed_output")

    return ClassifierOutcome(available=True, is_injection=result.is_injection, reason=result.reason)


__all__ = ["CLASSIFIER_PURPOSE", "ClassifierOutcome", "classify_injection"]
