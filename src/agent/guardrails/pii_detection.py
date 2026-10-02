"""Detect, validate, and mask residual PII in regex-redacted text.

The public facade makes one privacy-sensitive structured-model call. Validation
and masking remain deterministic and operate on the exact strings sent to the
model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import ValidationError

from agent.llm import (
    InvalidModelOutputError,
    LLMClient,
    ModelRefusalError,
    ModelTimeoutError,
    UnexpectedModelError,
)
from agent.prompts import (
    MAX_PII_FINDINGS,
    PII_DETECTION_SYSTEM,
    PIICategory,
    PIIDetectionOutput,
    PIIField,
    PIIFinding,
    PIIText,
    pii_detection_prompt,
)
from app.workers.retry_policy import RetryableError, TerminalError
from observability.redaction import PII_REDACTION_MARKERS, REDACTION_MARKERS

PIIFailureCategory = Literal[
    "timeout",
    "transient_provider",
    "terminal_provider",
    "model_refusal",
    "invalid_output",
    "invalid_findings",
    "unexpected",
    "input_too_large",
    "detector_disabled",
]
_PII_FAILURE_CATEGORIES = frozenset(get_args(PIIFailureCategory))


@dataclass(frozen=True, slots=True)
class PIIProtectionOutcome:
    """Privacy-safe result of residual-PII detection and masking."""

    available: bool
    protected: PIIText | None = None
    finding_count: int = 0
    categories: tuple[PIICategory, ...] = ()
    failure_category: PIIFailureCategory | None = None

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise ValueError("available must be a strict boolean")
        if type(self.finding_count) is not int or self.finding_count < 0:
            raise ValueError("finding_count must be a non-negative integer")
        if not all(isinstance(category, PIICategory) for category in self.categories):
            raise ValueError("categories must contain only supported PII categories")
        expected_categories = tuple(sorted(set(self.categories), key=lambda item: item.value))
        if self.categories != expected_categories:
            raise ValueError("categories must be unique and deterministically ordered")
        if self.available:
            if not isinstance(self.protected, PIIText) or self.failure_category is not None:
                raise ValueError("successful PII outcome has inconsistent fields")
            if (self.finding_count == 0) != (not self.categories):
                raise ValueError("successful PII outcome has inconsistent summary metadata")
            return
        if self.protected is not None or self.failure_category is None:
            raise ValueError("failed PII outcome has inconsistent fields")
        if self.failure_category not in _PII_FAILURE_CATEGORIES:
            raise ValueError("failed PII outcome has an unsupported failure category")
        if self.finding_count != 0 or self.categories:
            raise ValueError("failed PII outcome must not expose finding metadata")


class PIIFindingValidationError(ValueError):
    """A safe, stable error describing an invalid set of PII findings."""


def _field_value(text: PIIText, field: PIIField) -> str:
    return text.short_description if field is PIIField.SHORT_DESCRIPTION else text.description


def _intersects_marker(value: str, start: int, end: int) -> bool:
    for marker in REDACTION_MARKERS:
        marker_start = value.find(marker)
        while marker_start >= 0:
            marker_end = marker_start + len(marker)
            if start < marker_end and end > marker_start:
                return True
            marker_start = value.find(marker, marker_start + 1)
    return False


def _validate_finding_shape(finding: object) -> PIIFinding:
    # Pydantic normally enforces all of these conditions. Repeat them here so
    # model_construct() or another unchecked producer cannot bypass the boundary.
    if not isinstance(finding, PIIFinding):
        raise PIIFindingValidationError("invalid PII finding type")
    if not isinstance(finding.field, PIIField):
        raise PIIFindingValidationError("unsupported PII field")
    if not isinstance(finding.category, PIICategory):
        raise PIIFindingValidationError("unsupported PII category")
    if type(finding.start) is not int or type(finding.end) is not int:
        raise PIIFindingValidationError("PII offsets must be strict integers")
    if finding.start < 0 or finding.end <= finding.start:
        raise PIIFindingValidationError("invalid PII offset range")
    return finding


def validate_pii_findings(
    response: object,
    text: PIIText,
) -> tuple[PIIFinding, ...]:
    """Validate all findings atomically against the exact supplied field values.

    Any malformed or ambiguous finding rejects the complete response. Exception
    messages contain only stable categories and never incident or model content.
    """

    if not isinstance(response, PIIDetectionOutput):
        raise PIIFindingValidationError("invalid PII detection response type")
    if not isinstance(response.findings, list):
        raise PIIFindingValidationError("invalid PII findings collection")
    if len(response.findings) > MAX_PII_FINDINGS:
        raise PIIFindingValidationError("too many PII findings")

    unique: dict[tuple[PIIField, int, int, PIICategory], PIIFinding] = {}
    range_categories: dict[tuple[PIIField, int, int], PIICategory] = {}

    for candidate in response.findings:
        finding = _validate_finding_shape(candidate)
        value = _field_value(text, finding.field)
        if finding.end > len(value):
            raise PIIFindingValidationError("PII offset is out of bounds")
        if not value[finding.start : finding.end].strip():
            raise PIIFindingValidationError("PII range is empty or whitespace-only")
        if _intersects_marker(value, finding.start, finding.end):
            raise PIIFindingValidationError("PII range intersects a redaction marker")

        range_key = (finding.field, finding.start, finding.end)
        prior_category = range_categories.get(range_key)
        if prior_category is not None and prior_category is not finding.category:
            raise PIIFindingValidationError("conflicting PII categories for one range")
        range_categories[range_key] = finding.category
        unique[(finding.field, finding.start, finding.end, finding.category)] = finding

    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda finding: (
                finding.field.value,
                finding.start,
                finding.end,
                finding.category.value,
            ),
        )
    )

    previous_by_field: dict[PIIField, PIIFinding] = {}
    for finding in ordered:
        previous = previous_by_field.get(finding.field)
        if previous is not None and finding.start < previous.end:
            raise PIIFindingValidationError("overlapping PII ranges")
        previous_by_field[finding.field] = finding

    return ordered


def _mask_field(
    value: str,
    findings: Sequence[PIIFinding],
) -> str:
    protected = value
    for finding in sorted(findings, key=lambda item: item.start, reverse=True):
        marker = PII_REDACTION_MARKERS[finding.category.value]
        protected = protected[: finding.start] + marker + protected[finding.end :]
    return protected


def _mask_pii_findings(
    text: PIIText,
    findings: Sequence[PIIFinding],
) -> PIIText:
    """Replace validated ranges by category, independently for each field."""

    by_field: dict[PIIField, list[PIIFinding]] = {
        PIIField.SHORT_DESCRIPTION: [],
        PIIField.DESCRIPTION: [],
    }
    for finding in findings:
        by_field[finding.field].append(finding)
    return PIIText(
        short_description=_mask_field(
            text.short_description,
            by_field[PIIField.SHORT_DESCRIPTION],
        ),
        description=_mask_field(
            text.description,
            by_field[PIIField.DESCRIPTION],
        ),
    )


def _failure(category: PIIFailureCategory) -> PIIProtectionOutcome:
    return PIIProtectionOutcome(available=False, failure_category=category)


def protect_residual_pii(
    llm: LLMClient,
    text: PIIText,
    *,
    max_chars: int,
) -> PIIProtectionOutcome:
    """Detect and mask contextual PII in the complete regex-redacted input.

    Failures return no text or finding metadata. The caller is responsible for
    applying its workflow-level fail-closed replacement and routing policy.
    """

    if not text.short_description.strip() and not text.description.strip():
        return PIIProtectionOutcome(available=True, protected=text)
    if len(text.short_description) + len(text.description) > max_chars:
        return _failure("input_too_large")

    try:
        response = llm.structured(
            purpose="pii_detection",
            system=PII_DETECTION_SYSTEM,
            prompt=pii_detection_prompt(text),
            schema=PIIDetectionOutput,
            trace_content=False,
            max_retries=0,
        )
    except ModelTimeoutError:
        return _failure("timeout")
    except ModelRefusalError:
        return _failure("model_refusal")
    except InvalidModelOutputError:
        return _failure("invalid_output")
    except UnexpectedModelError:
        return _failure("unexpected")
    except RetryableError:
        return _failure("transient_provider")
    except TerminalError:
        return _failure("terminal_provider")
    except ValidationError:
        return _failure("invalid_output")
    except Exception:  # noqa: BLE001 - never expose a sensitive unexpected error
        return _failure("unexpected")

    if not isinstance(response, PIIDetectionOutput):
        return _failure("invalid_output")
    if not isinstance(response.findings, list):
        return _failure("invalid_output")

    try:
        findings = validate_pii_findings(response, text)
        protected = _mask_pii_findings(text, findings)
    except PIIFindingValidationError:
        return _failure("invalid_findings")
    except Exception:  # noqa: BLE001 - no partial text may escape a masking failure
        return _failure("unexpected")

    categories = tuple(
        sorted({finding.category for finding in findings}, key=lambda item: item.value)
    )
    return PIIProtectionOutcome(
        available=True,
        protected=protected,
        finding_count=len(findings),
        categories=categories,
    )


__all__ = [
    "PIIFailureCategory",
    "PIIFindingValidationError",
    "PIIProtectionOutcome",
    "protect_residual_pii",
    "validate_pii_findings",
]
