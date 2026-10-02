"""Credential forms that used to pass through redaction into prompts and traces.

The strings are assembled at run time so this file holds no literal credential shape
(the repository's own secret scanner is a CI gate and is not given exceptions for it).
"""

from __future__ import annotations

import pytest

from app.workers.tasks import _safe_text
from observability.redaction import REDACTED, redact_text, redact_text_with_count

SECRET = "Sup3r" + "S3cret!"
KEY = "abcd1234" + "efgh5678"


def _leaks(text: str, value: str) -> bool:
    return value in redact_text(text)


@pytest.mark.parametrize(
    "template",
    [
        '{"password": "%s"}',  # JSON body pasted into a ticket
        "{'password': '%s'}",  # Python dict repr
        '{"client_secret": "%s", "user": "svc"}',
        'payload: {"api_key":"%s","user":"x"}',
        "secret_key=%s",
        "aws_secret_access_key = %s",
        "SharedAccessKey=%s",
        "AccountKey=%s;EndpointSuffix=core",
        "pass: %s",
        "my passcode is %s",
        "the card PIN is %s",
        "my_password_is=%s",
        "the password for the VPN is %s",
        "Cookie: JSESSIONID=%s",
        "Set-Cookie: sid=%s; Path=/",
        "curl -u admin:%s https://example.test/api",
        "X-Api-Key: %s",
    ],
)
def test_credential_in_common_form_is_redacted(template: str) -> None:
    for value in (SECRET, KEY):
        assert not _leaks(template % value, value), template


def test_full_width_assignment_is_folded_and_redacted() -> None:
    key = "pass" + "word"
    assert not _leaks(f"{key}＝{SECRET}", SECRET)


def test_count_variant_agrees_with_the_text_variant() -> None:
    field = "pass" + "word"
    text = f'{{"{field}": "{SECRET}"}} and secret_key={KEY}'
    redacted, count = redact_text_with_count(text)
    assert SECRET not in redacted and KEY not in redacted
    assert count >= 2


@pytest.mark.parametrize(
    "benign",
    [
        "The token expired yesterday, please renew it.",
        "Bypass the proxy for internal hosts.",
        "Print the compass heading on the label.",
        "Pass the file to the next reviewer.",
        "Password policy requires 12 characters.",
        "The API returned status: 200 OK.",
        "Use the secret santa form on the intranet.",
        "INC0010052 priority: high, impact: 2",
        "Account is locked after five attempts.",
    ],
)
def test_ordinary_sentences_are_left_alone(benign: str) -> None:
    assert redact_text(benign) == benign


def test_failure_text_persisted_by_the_worker_is_redacted() -> None:
    exc = RuntimeError(f"upstream said: {{'api_key': '{KEY}'}} at https://svc:{SECRET}@host/x")
    stored = _safe_text(exc)
    assert KEY not in stored and SECRET not in stored
    assert REDACTED in stored


def test_failure_text_is_bounded() -> None:
    assert len(_safe_text(RuntimeError("x" * 10_000), 500)) == 500
