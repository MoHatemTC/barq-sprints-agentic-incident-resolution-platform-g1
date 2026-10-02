"""Credential and personal-data redaction shared by tracing, logging and prompts.

Two layers, applied recursively to any JSON-like value:

1. **Key-based**: a mapping value whose key names a secret (``password``,
   ``client_secret``, ``authorization``…) is replaced outright, whatever it holds.
2. **Pattern-based**: free text is scanned for credential shapes (bearer tokens,
   JWTs, provider API keys, ``user:pass@`` URLs, ``password=…`` pairs) and for
   personal data (e-mail addresses, IBANs, payment-card PANs, phone numbers).

Everything that leaves the process for Langfuse passes through
:func:`redact_value` (it is the Langfuse ``mask`` function), and the agent runs
incident text through :func:`redact_text` before any model sees it (PRD FR-18,
manual §11.6 "credential and key redaction, personal-data redaction").
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "***REDACTED***"
REDACTED_EMAIL = "***EMAIL***"
REDACTED_PHONE = "***PHONE***"

# Category-specific markers used by the residual-PII guardrail. The keys are
# deliberately plain strings so this low-level observability module does not
# depend on the agent prompt schemas.
PII_REDACTION_MARKERS: dict[str, str] = {
    "person_name": "***PII_PERSON_NAME***",
    "postal_address": "***PII_POSTAL_ADDRESS***",
    "date_of_birth": "***PII_DATE_OF_BIRTH***",
    "government_id": "***PII_GOVERNMENT_ID***",
    "financial_account": "***PII_FINANCIAL_ACCOUNT***",
    "payment_card": "***PII_PAYMENT_CARD***",
    "employee_or_customer_id": "***PII_EMPLOYEE_OR_CUSTOMER_ID***",
}
REDACTION_MARKERS: frozenset[str] = frozenset(
    {REDACTED, REDACTED_EMAIL, REDACTED_PHONE, *PII_REDACTION_MARKERS.values()}
)

#: Mapping keys whose values are always secret. Compared case-insensitively after
#: stripping ``-`` and ``_`` so ``Client-Secret`` and ``client_secret`` both match.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "password",
        "passwd",
        "refreshtoken",
        "secret",
        "secretkey",
        "setcookie",
        "token",
        "webhookauthtoken",
        "xapikey",
    }
)

# Order matters: the most specific credential shapes run first so a JWT inside a
# bearer header is not half-matched by the generic key=value rule. Each rule keeps
# its label (``Bearer``, ``password=``) so a reader still knows what was removed.
_CREDENTIAL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer <token> / Basic <b64>
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]{8,}=*"), rf"\1 {REDACTED}"),
    # JSON Web Tokens
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        REDACTED,
    ),
    # Provider keys: Anthropic, sk-/pk- style (LiteLLM, Langfuse, OpenAI), GitHub, AWS, Slack
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"\b(?:sk|pk)-(?:lf-)?[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), REDACTED),
    # PEM private keys
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # scheme://user:password@host
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"), REDACTED),
    # password=..., client_secret: ..., api key is ...
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|token)(\s*[:=]\s*|\s+is\s+)[\"']?[^\s\"',;]{4,}"
        ),
        rf"\1\2{REDACTED}",
    ),
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# ISO 13616 lengths for the countries supported by this deterministic layer.
# Keeping the length table explicit prevents a checksum-valid string with an
# impossible country length from being treated as an IBAN.
_IBAN_LENGTHS: dict[str, int] = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BI": 27,
    "BR": 29,
    "BY": 28,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DJ": 27,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "EG": 29,
    "ES": 24,
    "FI": 18,
    "FK": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HN": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IQ": 23,
    "IS": 26,
    "IT": 27,
    "JO": 30,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LC": 32,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "LY": 25,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MN": 20,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NI": 28,
    "NL": 18,
    "NO": 15,
    "OM": 23,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "RU": 33,
    "SA": 24,
    "SC": 31,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "SO": 23,
    "ST": 25,
    "SV": 28,
    "TL": 23,
    "TN": 24,
    "TR": 26,
    "UA": 29,
    "VA": 22,
    "VG": 24,
    "XK": 20,
    "YE": 30,
}
_IBAN_COUNTRY_ALTERNATIVES = "|".join(
    rf"{country}[0-9]{{2}}(?: ?[A-Z0-9]){{{length - 4}}}"
    for country, length in sorted(_IBAN_LENGTHS.items())
)
# A following space plus digit is treated as continuation of the financial
# identifier, not prose, so an overlength spaced candidate cannot lose only its
# valid-checksum prefix. Alphabetic text after a space remains ordinary prose.
_IBAN = re.compile(
    rf"(?<![\w-])(?:{_IBAN_COUNTRY_ALTERNATIVES})(?![\w-])(?! +[0-9])",
    re.ASCII | re.IGNORECASE,
)

# A PAN candidate has 13--19 ASCII digits and permits one space or hyphen
# between adjacent digits. The second pair of assertions prevents taking a
# valid suffix/prefix from a longer separator-delimited digit sequence.
_PAYMENT_CARD = re.compile(
    r"(?<![\w-])(?<![0-9][ -])[0-9](?:[ -]?[0-9]){12,18}(?![\w-])(?![ -][0-9])",
    re.ASCII,
)
# International or local phone numbers with at least 8 digits; avoids matching
# incident numbers (INC0010052), sys_ids (hex) and ISO timestamps.
_PHONE = re.compile(r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{3,4}(?![\w-])")
_DIGITS = re.compile(r"\d")


def _normalise_key(key: object) -> str:
    return str(key).lower().replace("-", "").replace("_", "")


def _redact_phone(match: re.Match[str]) -> str:
    text = match.group(0)
    return REDACTED_PHONE if len(_DIGITS.findall(text)) >= 8 else text


def _redact_iban(match: re.Match[str]) -> str:
    candidate = match.group(0)
    normalised = candidate.replace(" ", "").upper()
    country = normalised[:2]
    if (
        len(normalised) != _IBAN_LENGTHS.get(country)
        or not all("A" <= char <= "Z" for char in country)
        or not all("0" <= char <= "9" for char in normalised[2:4])
        or not all("0" <= char <= "9" or "A" <= char <= "Z" for char in normalised)
    ):
        return candidate

    remainder = 0
    for char in normalised[4:] + normalised[:4]:
        numeric = char if "0" <= char <= "9" else str(ord(char) - ord("A") + 10)
        for digit in numeric:
            remainder = (remainder * 10 + int(digit)) % 97
    return PII_REDACTION_MARKERS["financial_account"] if remainder == 1 else candidate


def _redact_payment_card(match: re.Match[str]) -> str:
    candidate = match.group(0)
    digits = candidate.replace(" ", "").replace("-", "")
    if not 13 <= len(digits) <= 19 or not all("0" <= char <= "9" for char in digits):
        return candidate

    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = ord(char) - ord("0")
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return PII_REDACTION_MARKERS["payment_card"] if total % 10 == 0 else candidate


def redact_text(text: str) -> str:
    """Return ``text`` with credentials and personal data replaced by markers."""
    if not text:
        return text
    for pattern, replacement in _CREDENTIAL_RULES:
        text = pattern.sub(replacement, text)
    text = _EMAIL.sub(REDACTED_EMAIL, text)
    text = _IBAN.sub(_redact_iban, text)
    text = _PAYMENT_CARD.sub(_redact_payment_card, text)
    return _PHONE.sub(_redact_phone, text)


def redact_text_with_count(text: str) -> tuple[str, int]:
    """
    Used for observability: return text + the number of redactions applied. The count is used to
    determine whether to log a redaction event, and to report the number of redactions in the
    observability event.
    """
    if not text:
        return text, 0
    total = 0
    for pattern, replacement in _CREDENTIAL_RULES:
        text, count = pattern.subn(replacement, text)
        total += count
    text, count = _EMAIL.subn(REDACTED_EMAIL, text)
    total += count

    for pattern, validator, marker in (
        (_IBAN, _redact_iban, PII_REDACTION_MARKERS["financial_account"]),
        (_PAYMENT_CARD, _redact_payment_card, PII_REDACTION_MARKERS["payment_card"]),
    ):
        before_markers = text.count(marker)
        text = pattern.sub(validator, text)
        total += text.count(marker) - before_markers

    before_phone_markers = text.count(REDACTED_PHONE)
    text = _PHONE.sub(_redact_phone, text)
    after_phone_markers = text.count(REDACTED_PHONE)
    total += after_phone_markers - before_phone_markers
    return text, total


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-like value (dict / list / tuple / str)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: REDACTED if _normalise_key(k) in SENSITIVE_KEYS else redact_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


def langfuse_mask(*, data: Any, **_: Any) -> Any:
    """Langfuse ``mask`` hook: applied to every input, output and metadata value."""
    try:
        return redact_value(data)
    except Exception:  # noqa: BLE001 — a masking bug must never leak the raw value
        return REDACTED


__all__ = [
    "PII_REDACTION_MARKERS",
    "REDACTED",
    "REDACTED_EMAIL",
    "REDACTED_PHONE",
    "REDACTION_MARKERS",
    "SENSITIVE_KEYS",
    "langfuse_mask",
    "redact_text",
    "redact_text_with_count",
    "redact_value",
]
