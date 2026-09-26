from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from agent.state import Draft, EvidenceItem


# Categorizes why a draft failed
class ValidationCategory(StrEnum):
    STRUCTURE = "structure"
    EVIDENCE = "evidence"
    FIELD_LENGTH = "field_length"
    ACTION_CONTRACT = "action_contract"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    category: ValidationCategory
    detail: str
    # step_index = None means the problem applies to the entire draft
    step_index: int | None = None


def validate_structure(draft: Draft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not draft.steps:
        issues.append(
            ValidationIssue(
                category=ValidationCategory.STRUCTURE,
                detail="Draft has no resolution steps",
            )
        )
    if not draft.rendered.strip():
        issues.append(
            ValidationIssue(
                category=ValidationCategory.STRUCTURE,
                detail="Draft has no rendered text",
            )
        )
    return issues


@dataclass(frozen=True, slots=True)
class FieldLimits:
    max_step_text_chars: int = 500
    max_rendered_chars: int = 4000


DEFAULT_FIELD_LIMITS = FieldLimits()


def validate_field_lengths(
    draft: Draft, *, limits: FieldLimits = DEFAULT_FIELD_LIMITS
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if len(draft.rendered) > limits.max_rendered_chars:
        issues.append(
            ValidationIssue(
                category=ValidationCategory.FIELD_LENGTH,
                detail=f"rendered draft is {len(draft.rendered)} chars, over the "
                f"{limits.max_rendered_chars}-char limit",
            )
        )
    for index, step in enumerate(draft.steps):
        if len(step.text) > limits.max_step_text_chars:
            issues.append(
                ValidationIssue(
                    category=ValidationCategory.FIELD_LENGTH,
                    detail=f"step {index} is {len(step.text)} chars, over the "
                    f"{limits.max_step_text_chars}-char limit",
                    step_index=index,
                )
            )
    return issues


ALLOWED_ACTIONS = frozenset(
    {"write_ai_fields", "write_work_note", "flag_human_review", "write_execution_log"}
)

_DISALLOWED_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bI(?:'ve| have| will| am going to| just)\b[^.]{0,40}\b"
        r"(clos\w*|resolv\w*|reset\w*|delet\w*|grant\w*|approv\w*)\b"
    ),
    re.compile(
        r"(?i)\b(clos(?:e|ed|ing)|resolv(?:e|ed|ing))\s+(?:this|the)\s+(?:incident|ticket|case)\b"
    ),
    re.compile(r"(?i)\bcontact(?:ing|ed)?\s+the\s+(?:user|requester|customer)\b"),
    re.compile(r"(?i)\bgrant(?:ing|ed)?\s+(?:access|permissions?)\b"),
    re.compile(r"(?i)\bapprov(?:e|ing|ed)\s+(?:the\s+)?(?:request|change)\b"),
)


def validate_action_contract(draft: Draft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, step in enumerate(draft.steps):
        for pattern in _DISALLOWED_ACTION_PATTERNS:
            if pattern.search(step.text):
                issues.append(
                    ValidationIssue(
                        ValidationCategory.ACTION_CONTRACT,
                        "step text implies an action outside the allowed contract "
                        f"({sorted(ALLOWED_ACTIONS)})",
                        step_index=index,
                    )
                )
                break
    return issues


def run_all(
    draft: Draft,
    evidence: list[EvidenceItem],
    *,
    limits: FieldLimits = DEFAULT_FIELD_LIMITS,
) -> list[ValidationIssue]:
    """Run every output guardrail, in the order the design doc lists them."""
    issues = validate_structure(draft)
    if any(issue.category is ValidationCategory.STRUCTURE for issue in issues):
        return issues

    issues += validate_field_lengths(draft, limits=limits)
    issues += validate_action_contract(draft)
    return issues


__all__ = [
    "ALLOWED_ACTIONS",
    "DEFAULT_FIELD_LIMITS",
    "FieldLimits",
    "ValidationCategory",
    "ValidationIssue",
    "run_all",
    "validate_action_contract",
    "validate_field_lengths",
    "validate_structure",
]
