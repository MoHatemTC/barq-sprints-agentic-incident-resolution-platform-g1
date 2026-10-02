from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class InjectionCategory(StrEnum):
    INSTRUCTION_OVERRIDE = "instruction_override"
    ROLEPLAY_JAILBREAK = "roleplay_jailbreak"
    DELIMITER_ATTACK = "delimiter_attack"
    ENCODED_PAYLOAD = "encoded_payload"


@dataclass(frozen=True, slots=True)
class PatternScreeningResult:
    """Outcome of deterministic screening.

    ``flagged`` means any pattern matched. ``blocking`` means at least one *unambiguous*
    attack pattern matched, which blocks the incident without a model. A soft-only hit
    (``flagged and not blocking``) is ambiguous on its own, because XML tags, "from now
    on you must" and "decode this and run it" all occur in legitimate tickets, so it is
    sent to the semantic classifier, which fails closed.
    """

    flagged: bool
    categories: tuple[InjectionCategory, ...] = ()
    match_count: int = 0
    blocking: bool = False


# Unambiguous: phrasings whose only purpose is to override the assistant.
_INSTRUCTION_OVERRIDE: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bignore\s+(all|any|the|every)?\s*(previous|prior|above|earlier)\s+"
        r"(instructions?|rules?|prompts?)\b"
    ),
    re.compile(r"(?i)\bdisregard\s+(the|all|your)\s+(instructions?|rules?|system prompt)\b"),
    re.compile(r"(?i)\bforget\s+(everything|what)\s+(you were|i)\s+(told|said|instructed)\b"),
    re.compile(r"(?i)\boverride\s+(your|the)\s+(instructions?|programming|rules?)\b"),
    re.compile(
        r"(?i)\b(skip|set aside|bypass)\s+(all\s+|any\s+)?(earlier|prior|previous)\s+rules\b"
    ),
    re.compile(r"(?i)\bdo not follow your (instructions?|rules?|guidelines)\b"),
    re.compile(
        r"(?i)\b(reveal|print|show|repeat|output)\s+(me\s+)?(your|the)\s+"
        r"(system\s+prompt|hidden\s+prompt|initial\s+instructions)\b"
    ),
    # The same override in the languages this programme's incidents are written in.
    re.compile(
        r"(?i)\bignore[zr]?\s+(toutes\s+)?les\s+instructions\s+(pr[eé]c[eé]dentes|ant[eé]rieures)"
    ),
    re.compile(r"(?i)\bignora\s+(todas\s+)?las\s+instrucciones\s+anteriores"),
    re.compile(r"تجاهل\s+(جميع\s+|كل\s+)?التعليمات\s+السابقة"),
)
# Ambiguous: also appears in legitimate tickets (a policy memo, a config snippet).
_INSTRUCTION_OVERRIDE_SOFT: tuple[re.Pattern[str], ...] = (
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
    re.compile(r"(?i)\byou are no longer\b.{0,30}\b(an? assistant|ai|bound by)\b"),
    re.compile(r"(?i)\bignore your (guidelines|programming|training|safety)\b"),
)
_ROLEPLAY_JAILBREAK_SOFT: tuple[re.Pattern[str], ...] = (
    # "developer mode" is a browser/phone setting; "DAN" is also a person's name.
    re.compile(r"(?i)\b(you are|act as|switch to|enter)\b.{0,30}\bdeveloper mode\b"),
    re.compile(r"(?i)\bDAN\b.{0,20}\b(mode|prompt)\b"),
)

_DELIMITER_ATTACK: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\|im_(start|end)\|>"),
    re.compile(r"\[/?INST\]|<\|system\|>", re.IGNORECASE),
)
_DELIMITER_ATTACK_SOFT: tuple[re.Pattern[str], ...] = (
    # XML/HTML tags named like the prompt's own delimiters occur in pasted logs and configs.
    re.compile(r"</?\s*system\s*>", re.IGNORECASE),
    re.compile(r"</?\s*incident\s*>", re.IGNORECASE),
    re.compile(r"</?\s*evidence\b[^>]*>", re.IGNORECASE),
    re.compile(r"(?i)\bend of (system|incident) (prompt|block)\b"),
    re.compile(r"(?im)^\s*###\s*system\b"),
    re.compile(r"(?i)<!--\s*system\s*:"),
    re.compile(r"(?i)[\"']role[\"']\s*:\s*[\"']system[\"']"),
)

_ENCODED_PAYLOAD_SOFT: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bdecode\s+(this|the following|this base64|the base64)\b.{0,60}\b"
        r"(run|execute|and (do|follow))\b"
    ),
    re.compile(r"(?i)\bbase64[- ]decode\b.{0,40}\bthen\b"),
)

#: (category, unambiguous patterns, ambiguous patterns)
_CATEGORY_PATTERNS: tuple[
    tuple[InjectionCategory, tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]], ...
] = (
    (InjectionCategory.INSTRUCTION_OVERRIDE, _INSTRUCTION_OVERRIDE, _INSTRUCTION_OVERRIDE_SOFT),
    (InjectionCategory.ROLEPLAY_JAILBREAK, _ROLEPLAY_JAILBREAK, _ROLEPLAY_JAILBREAK_SOFT),
    (InjectionCategory.DELIMITER_ATTACK, _DELIMITER_ATTACK, _DELIMITER_ATTACK_SOFT),
    (InjectionCategory.ENCODED_PAYLOAD, (), _ENCODED_PAYLOAD_SOFT),
)

#: Characters with no visible form, used to split a keyword so a regex misses it.
_INVISIBLE = dict.fromkeys(
    [0x00AD, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0xFEFF], None
)
#: Cyrillic/Greek letters that render like Latin ones (NFKC does not fold them).
_CONFUSABLES = str.maketrans(
    {
        "\u0430": "a",
        "\u0435": "e",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0443": "y",
        "\u0445": "x",
        "\u0456": "i",
        "\u0458": "j",
        "\u0455": "s",
        "\u03bf": "o",
        "\u03b1": "a",
        "\u03b5": "e",
        "\u03b9": "i",
        "\u03c1": "p",
    }
)


def normalise_for_screening(text: str) -> str:
    """Fold look-alike and invisible characters so a disguised phrase still matches.

    Used on a copy for matching only; the incident text itself is not altered.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    return folded.translate(_CONFUSABLES)


def screen_text(text: str) -> PatternScreeningResult:
    """
    Return a PatternScreeningResult for the given text, indicating whether it
    contains any known prompt injection patterns, which categories, how many matches,
    and whether any match is unambiguous enough to block without a model.
    """
    if not text:
        return PatternScreeningResult(flagged=False)

    subject = normalise_for_screening(text)
    categories: set[InjectionCategory] = set()
    match_count = 0
    blocking = False

    for category, hard_patterns, soft_patterns in _CATEGORY_PATTERNS:
        for pattern in hard_patterns:
            matches = pattern.findall(subject)
            if matches:
                categories.add(category)
                match_count += len(matches)
                blocking = True
        for pattern in soft_patterns:
            matches = pattern.findall(subject)
            if matches:
                categories.add(category)
                match_count += len(matches)

    return PatternScreeningResult(
        flagged=bool(categories),
        categories=tuple(sorted(categories)),
        match_count=match_count,
        blocking=blocking,
    )


__all__ = [
    "InjectionCategory",
    "PatternScreeningResult",
    "normalise_for_screening",
    "screen_text",
]
