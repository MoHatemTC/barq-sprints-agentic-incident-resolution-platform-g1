"""Credential and personal-data redaction shared by tracing, logging and prompts.

Two layers, applied recursively to any JSON-like value:

1. **Key-based**: a mapping value whose key names a secret (``password``,
   ``client_secret``, ``authorization``…) is replaced outright, whatever it holds.
2. **Pattern-based**: free text is scanned for credential shapes (bearer tokens,
   JWTs, provider API keys, ``user:pass@`` URLs, ``password=…`` pairs) and for
   personal data (e-mail addresses, phone numbers).

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
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),
    # Provider keys: Anthropic, Langfuse / OpenAI-style, GitHub, AWS, Slack
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
# International or local phone numbers with at least 8 digits; avoids matching
# incident numbers (INC0010052), sys_ids (hex) and ISO timestamps.
_PHONE = re.compile(r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{3,4}(?![\w-])")
_DIGITS = re.compile(r"\d")


def _normalise_key(key: object) -> str:
    return str(key).lower().replace("-", "").replace("_", "")


def _redact_phone(match: re.Match[str]) -> str:
    text = match.group(0)
    return REDACTED_PHONE if len(_DIGITS.findall(text)) >= 8 else text


def redact_text(text: str) -> str:
    """Return ``text`` with credentials and personal data replaced by markers."""
    if not text:
        return text
    for pattern, replacement in _CREDENTIAL_RULES:
        text = pattern.sub(replacement, text)
    text = _EMAIL.sub(REDACTED_EMAIL, text)
    return _PHONE.sub(_redact_phone, text)


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
    "REDACTED",
    "REDACTED_EMAIL",
    "REDACTED_PHONE",
    "SENSITIVE_KEYS",
    "langfuse_mask",
    "redact_text",
    "redact_value",
]
