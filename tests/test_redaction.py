"""Focused tests for deterministic structured-PII redaction."""

from __future__ import annotations

import pytest

from observability.redaction import (
    PII_REDACTION_MARKERS,
    REDACTED_PHONE,
    langfuse_mask,
    redact_text,
    redact_text_with_count,
    redact_value,
)

PAYMENT_CARD_MARKER = PII_REDACTION_MARKERS["payment_card"]
FINANCIAL_ACCOUNT_MARKER = PII_REDACTION_MARKERS["financial_account"]


@pytest.mark.parametrize(
    "pan",
    [
        "4123456789011",
        "41234567890120",
        "412345678901233",
        "4123456789012349",
        "41234567890123458",
        "412345678901234561",
        "4123456789012345677",
    ],
    ids=lambda pan: f"{len(pan)}-digits",
)
def test_redacts_luhn_valid_pan_lengths_from_13_through_19(pan: str) -> None:
    assert redact_text(pan) == PAYMENT_CARD_MARKER


@pytest.mark.parametrize(
    "pan",
    [
        "4111111111111111",
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
    ],
    ids=["contiguous", "spaced", "hyphenated"],
)
def test_redacts_common_pan_formats_before_phone_detection(pan: str) -> None:
    redacted, count = redact_text_with_count(f"Card: {pan}")

    assert redacted == f"Card: {PAYMENT_CARD_MARKER}"
    assert REDACTED_PHONE not in redacted
    assert count == 1


@pytest.mark.parametrize(
    "candidate",
    [
        "4111111111111112",  # invalid Luhn checksum
        "411111111111",  # 12 digits
        "41111111111111111111",  # 20 digits
        "4 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1",  # 20 separated digits
    ],
    ids=["invalid-luhn", "too-short", "too-long", "too-long-spaced"],
)
def test_does_not_classify_invalid_pan_candidates_as_payment_cards(candidate: str) -> None:
    assert PAYMENT_CARD_MARKER not in redact_text(candidate)


@pytest.mark.parametrize(
    "text",
    [
        "x4111111111111111",
        "4111111111111111y",
        "asset_4111111111111111",
        "ref-4111111111111111",
    ],
)
def test_does_not_redact_pan_embedded_in_an_identifier(text: str) -> None:
    assert redact_text(text) == text


def test_pan_redaction_is_idempotent_and_counted_once() -> None:
    once, count = redact_text_with_count("Card 4111-1111-1111-1111")

    assert once == f"Card {PAYMENT_CARD_MARKER}"
    assert count == 1
    assert redact_text(once) == once
    assert redact_text_with_count(once) == (once, 0)


@pytest.mark.parametrize(
    "iban",
    [
        "GB82WEST12345698765432",
        "DE89370400440532013000",
        "NL91ABNA0417164300",
        "MN121234123456789123",
    ],
    ids=["united-kingdom", "germany", "netherlands", "mongolia"],
)
def test_redacts_valid_contiguous_ibans_with_country_specific_lengths(iban: str) -> None:
    assert redact_text(iban) == FINANCIAL_ACCOUNT_MARKER


@pytest.mark.parametrize(
    "iban",
    [
        "GB82 WEST 1234 5698 7654 32",
        "gb82 west 1234 5698 7654 32",
    ],
    ids=["spaced", "lowercase-spaced"],
)
def test_normalises_and_redacts_spaced_ibans(iban: str) -> None:
    redacted, count = redact_text_with_count(f"IBAN: {iban}")

    assert redacted == f"IBAN: {FINANCIAL_ACCOUNT_MARKER}"
    assert count == 1


@pytest.mark.parametrize(
    ("suffix", "expected_suffix"),
    [
        (" is active", " is active"),
        (", verified", ", verified"),
    ],
    ids=["prose", "punctuation"],
)
def test_redacts_valid_iban_without_consuming_following_text(
    suffix: str,
    expected_suffix: str,
) -> None:
    assert redact_text(f"GB82 WEST 1234 5698 7654 32{suffix}") == (
        f"{FINANCIAL_ACCOUNT_MARKER}{expected_suffix}"
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "GB83WEST12345698765432",  # invalid mod-97 checksum
        "GB82WEST1234569876543",  # one character short for GB
        "GB82WEST123456987654321",  # one character long for GB
        "GB82 WEST 1234 5698 7654 32 1",  # one separated character long for GB
        "ZZ82WEST12345698765432",  # unsupported country code
    ],
    ids=[
        "invalid-checksum",
        "too-short",
        "too-long-contiguous",
        "too-long-spaced",
        "unsupported-country",
    ],
)
def test_does_not_classify_invalid_iban_candidates_as_financial_accounts(
    candidate: str,
) -> None:
    assert FINANCIAL_ACCOUNT_MARKER not in redact_text(candidate)


@pytest.mark.parametrize(
    "text",
    [
        "xGB82WEST12345698765432",
        "GB82WEST12345698765432y",
        "asset_GB82WEST12345698765432",
        "ref-GB82WEST12345698765432",
    ],
)
def test_does_not_redact_iban_embedded_in_an_identifier(text: str) -> None:
    assert redact_text(text) == text


def test_iban_redaction_is_idempotent_and_counted_once() -> None:
    once, count = redact_text_with_count("IBAN GB82 WEST 1234 5698 7654 32")

    assert once == f"IBAN {FINANCIAL_ACCOUNT_MARKER}"
    assert count == 1
    assert redact_text(once) == once
    assert redact_text_with_count(once) == (once, 0)


@pytest.mark.parametrize(
    "text",
    [
        "INC0010052",
        "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "10.42.7.19",
        "00:1A:2B:3C:4D:5E",
        "2026-09-08T15:47:00Z",
        "port 8443",
        "db-01.prod.barq.internal",
        "AST-009812",
        "SN-4C8F-19A2",
        "v2.13.19",
        "build-20261001.42",
        "ORD-1234567890123",
        "REF_1234567890123456",
    ],
    ids=[
        "incident-number",
        "sys-id",
        "ip-address",
        "mac-address",
        "timestamp",
        "port",
        "hostname",
        "asset-tag",
        "serial-number",
        "software-version",
        "build-number",
        "order-number",
        "reference-number",
    ],
)
def test_financial_rules_leave_operational_identifiers_unchanged(text: str) -> None:
    assert redact_text(text) == text


def test_ordinary_phone_redaction_is_preserved() -> None:
    assert redact_text("Call +971 50 123 4567") == f"Call {REDACTED_PHONE}"


def test_combined_financial_redaction_preserves_public_contracts() -> None:
    text = "Card 4111111111111111; IBAN GB82WEST12345698765432"
    expected = f"Card {PAYMENT_CARD_MARKER}; IBAN {FINANCIAL_ACCOUNT_MARKER}"

    assert redact_text(text) == expected
    assert redact_text_with_count(text) == (expected, 2)
    assert redact_value({"values": [text, (text,)]}) == {
        "values": [expected, [expected]],
    }
    assert langfuse_mask(data=text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Card 4111 1111 1111 1111",
        "IBAN gb82 west 1234 5698 7654 32",
        "Card 4111111111111111; IBAN DE89370400440532013000",
    ],
)
def test_counting_api_returns_the_same_text_as_redact_text(text: str) -> None:
    assert redact_text_with_count(text)[0] == redact_text(text)
