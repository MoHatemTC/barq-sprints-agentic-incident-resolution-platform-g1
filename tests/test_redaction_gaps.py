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


@pytest.mark.parametrize(
    "prose",
    [
        "the build pass is expected tomorrow",
        "the token is expired, ask the user to sign in again",
        "the pin is stuck in the card reader",
    ],
)
def test_weak_keywords_followed_by_is_need_a_digit_to_count(prose: str) -> None:
    assert redact_text(prose) == prose


def test_weak_keyword_with_a_digit_value_is_still_redacted() -> None:
    assert "4821" not in redact_text("the pin is 4821")
    assert "tok3n9x" not in redact_text("token is tok3n9x")


def test_redaction_is_linear_on_long_separated_text() -> None:
    # A pasted log line or a list of ids: an open-ended repetition in a pattern made this
    # quadratic (11 s at 32 KB). One second is generous for a few milliseconds of work.
    import time

    started = time.perf_counter()
    redact_text("a-" * 50_000)
    redact_text("x_" * 50_000 + "password=" + SECRET)
    assert time.perf_counter() - started < 1.0


def test_text_with_nothing_to_redact_keeps_its_original_characters() -> None:
    text = "会议：地址，ＩＰ是１０．０．０．５"
    assert redact_text(text) == text
    assert redact_text_with_count(text) == (text, 0)


def test_a_full_width_secret_is_still_found() -> None:
    disguised = "ｐａｓｓｗｏｒｄ＝" + SECRET
    assert SECRET not in redact_text(disguised)
