from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class InjectionCategory(StrEnum):
    INSTRUCTION_OVERRIDE = "instruction_override"
    ROLEPLAY_JAILBREAK = "roleplay_jailbreak"
    DELIMITER_ATTACK = "delimiter_attack"
    ENCODED_PAYLOAD = "encoded_payload"


@dataclass(frozen=True, slots=True)
class PatternScreeningResult:
    flagged: bool
    categories: tuple[InjectionCategory, ...] = ()
    match_count: int = 0


_INSTRUCTION_OVERRIDE: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bignore\s+(all|any|the|every)?\s*(previous|prior|above|earlier)\s+"
        r"(instructions?|rules?|prompts?)\b"
    ),
    re.compile(r"(?i)\bdisregard\s+(the|all|your)\s+(instructions?|rules?|system prompt)\b"),
    re.compile(r"(?i)\bforget\s+(everything|what)\s+(you were|i)\s+(told|said|instructed)\b"),
    re.compile(r"(?i)\bnew\s+instructions?\s*:"),
    re.compile(r"(?i)\byou must now\b"),
    re.compile(r"(?i)\boverride\s+(your|the)\s+(instructions?|programming|rules?)\b"),
    re.compile(r"(?i)\bfrom now on,?\s+you (are|will|must)\b"),
)

_ROLEPLAY_JAILBREAK: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bact as (an?|the)?\s*(an?\s+)?(unfiltered|unrestricted|uncensored|jailbroken)\b"
    ),
    re.compile(
        r"(?i)\bpretend (you are|to be)\b.{0,40}\b"
        r"(no (restrictions|rules|filters)|without (restrictions|rules))\b"
    ),
    re.compile(r"(?i)\bdeveloper mode\b"),
    re.compile(r"(?i)\bDAN\b.{0,20}\b(mode|prompt)\b"),
    re.compile(r"(?i)\byou are no longer\b.{0,30}\b(an? assistant|ai|bound by)\b"),
    re.compile(r"(?i)\bignore your (guidelines|programming|training|safety)\b"),
)

_DELIMITER_ATTACK: tuple[re.Pattern[str], ...] = (
    re.compile(r"</?\s*system\s*>", re.IGNORECASE),
    re.compile(r"<\|im_(start|end)\|>"),
    re.compile(r"</?\s*incident\s*>", re.IGNORECASE),
    re.compile(r"</?\s*evidence\b[^>]*>", re.IGNORECASE),
    re.compile(r"(?i)\bend of (system|incident) (prompt|block)\b"),
    re.compile(r"(?im)^\s*###\s*system\b"),
)

_ENCODED_PAYLOAD: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bdecode\s+(this|the following|this base64|the base64)\b.{0,60}\b"
        r"(run|execute|and (do|follow))\b"
    ),
    re.compile(r"(?i)\bbase64[- ]decode\b.{0,40}\bthen\b"),
)

# Detects long sequences of base64 characters
_LONG_BASE64_TOKEN = re.compile(r"\b[A-Za-z0-9+/]{80,}={0,2}\b")
_IMPLAUSIBLY_LONG_BASE64 = 200

_CATEGORY_PATTERNS: tuple[tuple[InjectionCategory, tuple[re.Pattern[str], ...]], ...] = (
    (InjectionCategory.INSTRUCTION_OVERRIDE, _INSTRUCTION_OVERRIDE),
    (InjectionCategory.ROLEPLAY_JAILBREAK, _ROLEPLAY_JAILBREAK),
    (InjectionCategory.DELIMITER_ATTACK, _DELIMITER_ATTACK),
    (InjectionCategory.ENCODED_PAYLOAD, _ENCODED_PAYLOAD),
)


def screen_text(text: str) -> PatternScreeningResult:
    """
    Return a PatternScreeningResult for the given text, indicating whether it
    contains any known prompt injection patterns, and if so, which categories and how many matches.
    """
    if not text:
        return PatternScreeningResult(flagged=False)

    categories: set[InjectionCategory] = set()
    match_count = 0

    for category, patterns in _CATEGORY_PATTERNS:
        for pattern in patterns:
            matches = pattern.findall(text)
            if matches:
                categories.add(category)
                match_count += len(matches)

    # Check for implausibly long base64 sequences
    long_base64_matches = _LONG_BASE64_TOKEN.findall(text)
    if long_base64_matches and (
        categories or any(len(match) > _IMPLAUSIBLY_LONG_BASE64 for match in long_base64_matches)
    ):
        if InjectionCategory.ENCODED_PAYLOAD not in categories:
            categories.add(InjectionCategory.ENCODED_PAYLOAD)
        match_count += len(long_base64_matches)

    flagged = bool(categories)
    return PatternScreeningResult(
        flagged=flagged, categories=tuple(sorted(categories)), match_count=match_count
    )


__all__ = ["InjectionCategory", "PatternScreeningResult", "screen_text"]
