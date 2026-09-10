"""Unit and regression tests for the BARQ Operations Manual PDF extraction pipeline."""

from pathlib import Path
from textwrap import dedent

import pytest

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.barq_manual import (
    SECURITY_TIERS,
    extract_barq_manual_articles,
    parse_article_block,
    strip_page_headers_and_footers,
)

PDF_PATH = Path("data/barq-system-kb.pdf")

SYNTHETIC_PUBLISHED_BLOCK = dedent(
    """\
             KB0001         VPN AUTHENTICATION FAILS AFTER A PASSWORD CHANGE

     State                  Published                          Version                  2

     Service                corporate-vpn                      Category                 network

     Owner                  Network Operations                 Author                   L. Haddad

     Reviewed               11 Apr 2026                        Related PRB0040012, INC0010023




    Symptom. The user can reach the internet but the VPN client reports an
    authentication failure. It began after a password reset.

    Cause. The VPN client caches the previous credential in the operating system credential store.

    Resolution.
     1. Confirm password was changed within 24 hours.
     2. Ask user to sign out of the VPN client completely.
     3. Clear the cached credential.

    Escalation. If the account is not locked, escalate to Network Ops.
    """
)

SYNTHETIC_RETIRED_BLOCK = dedent(
    """\
    Version 1 — retired 02 April 2026

             KB0010         ORDER SERVICE CONNECTION POOL EXHAUSTION · **RETIRED**

     State                  Retired                      Version            1

     Retired on             02 Apr 2026                  Reason             MIR-2026-03 outage.




    Symptom. The order service returns HTTP 500 and database pool is exhausted.

    Cause. Connections are held under load.

    Resolution.
     1. Restart the order service application server.

    Escalation. Escalate to Platform Engineering.

       Why this revision is dangerous, not merely outdated
       The restart in step 1 dropped in-flight orders.
    """
)


def test_strip_page_headers_and_footers() -> None:
    raw = (
        "BARQ Systems · IT Service Operations Manual       INTERNAL DOCUMENT\n"
        "Some actual content line\n"
        "Edition 4.0                                        18 of 52 ·\n"
        "Another valid line"
    )
    cleaned = strip_page_headers_and_footers(raw)
    assert "BARQ Systems" not in cleaned
    assert "Edition 4.0" not in cleaned
    assert "Some actual content line" in cleaned
    assert "Another valid line" in cleaned


def test_parse_published_article_block() -> None:
    art = parse_article_block(SYNTHETIC_PUBLISHED_BLOCK)
    assert isinstance(art, Article)
    assert art.article_number == "KB0001"
    assert art.version == "2.0"
    assert art.unique_key == "KB0001-v2.0"
    assert art.workflow_state == WorkflowState.PUBLISHED
    assert art.security_level == SecurityLevel.INTERNAL
    assert art.service == "corporate-vpn"
    assert art.category == "network"
    assert art.owner == "Network Operations"
    assert "PRB0040012" in art.related_records
    assert "INC0010023" in art.related_records
    assert "## Symptom" in art.body
    assert "## Cause" in art.body
    assert "## Resolution" in art.body
    assert "## Escalation" in art.body


def test_parse_retired_variant_block() -> None:
    art = parse_article_block(SYNTHETIC_RETIRED_BLOCK)
    assert art.article_number == "KB0010"
    assert art.version == "1.0"
    assert art.unique_key == "KB0010-v1.0"
    assert art.workflow_state == WorkflowState.RETIRED
    assert art.security_level == SecurityLevel.RESTRICTED
    assert art.service == "order-processing"
    assert art.category == "software"
    assert "## Warning" in art.body
    assert "MIR-2026-03" in art.related_records


def test_incomplete_block_is_rejected() -> None:
    """A truncated block missing a required section must fail loudly, not emit a 'valid' article."""
    incomplete = SYNTHETIC_PUBLISHED_BLOCK.replace(
        "Escalation. If the account is not locked, escalate to Network Ops.\n", ""
    )
    with pytest.raises(ValueError, match="missing sections: Escalation"):
        parse_article_block(incomplete)


def test_hyphen_join_preservation() -> None:
    block_with_hyphen = SYNTHETIC_PUBLISHED_BLOCK.replace(
        "credential store.", "credential\nstore with re-\nchecked token."
    )
    art = parse_article_block(block_with_hyphen)
    assert "re-checked" in art.body


def test_security_tier_mapping_consistency() -> None:
    for kb, level in SECURITY_TIERS.items():
        if kb in {"KB0001", "KB0002", "KB0003", "KB0005", "KB0006", "KB0009"}:
            assert level == SecurityLevel.INTERNAL
        else:
            assert level == SecurityLevel.RESTRICTED


def test_short_description_derived_from_first_sentence() -> None:
    art = parse_article_block(SYNTHETIC_PUBLISHED_BLOCK)
    assert art.short_description == (
        "The user can reach the internet but the VPN client reports an authentication failure."
    )
    assert len(art.short_description) <= 255


def test_wrapped_number_never_becomes_fake_step() -> None:
    """A year wrapping to a line start must stay inside its step (KB0010-v2 bug)."""
    block = SYNTHETIC_PUBLISHED_BLOCK.replace(
        "3. Clear the cached credential.",
        "3. Do not restart the server. A restart caused a 40-minute outage on 14 March\n"
        "    2026.\n"
        " 4. Clear the cached credential.",
    )
    art = parse_article_block(block)
    resolution = art.body.split("## Resolution")[1].split("##")[0]
    assert "14 March 2026." in resolution, "year must stay glued to its step"
    assert "2026. 2." not in art.body and "2026. 4." not in art.body
    assert "\n4. Clear the cached credential." in resolution, "step 4 must be its own line"
    # Zero text loss: every source word survives
    for token in ("outage", "credential", "2026.", "cached"):
        assert token in resolution


def test_zero_loss_guard_rejects_lossy_rebuild() -> None:
    """If a future splitter drops text, the guard must raise, not emit corruption."""
    from app.retrieval.barq_manual import assert_zero_loss

    source = "1. Restart the server. 2. Confirm recovery."
    with pytest.raises(ValueError, match="lost or duplicated"):
        assert_zero_loss(source, "1. Restart the server.")  # step 2 dropped
    # Whitespace-only differences are fine
    assert_zero_loss(source, "1. Restart the server.\n  2. Confirm recovery.")


def test_kb0010_v2_grid_separates_owner_and_author() -> None:
    """The KB0010-v2 grid has no Author label: 'Owner  Team · Name'."""
    block = dedent(
        """\
                 KB0010         ORDER SERVICE CONNECTION POOL EXHAUSTION

         State                  Published                    Version            2

         Service              order-processing         Owner              Platform Eng · K. Selim

         Reviewed               02 Apr 2026                  Related            MIR-2026-03

        Symptom. The order service returns HTTP 500 under load.

        Cause. Connections are held beyond their intended lifetime.

        Resolution.
         1. Confirm pool saturation.
        Escalation. Escalate to Platform Engineering.
        """
    )
    art = parse_article_block(block)
    assert art.owner == "Platform Eng"
    assert art.author == "K. Selim"


@pytest.mark.skipif(not PDF_PATH.exists(), reason="Real PDF not present on disk")
def test_extract_barq_manual_from_real_pdf() -> None:
    articles, report = extract_barq_manual_articles(PDF_PATH)

    assert report.total_records == 11
    assert report.published_count == 10
    assert report.retired_count == 1
    assert len(articles) == 11

    by_key = {a.unique_key: a for a in articles}
    assert "KB0001-v2.0" in by_key
    assert "KB0010-v1.0" in by_key
    assert "KB0010-v2.0" in by_key

    # Check KB0010-v1.0 is retired
    assert by_key["KB0010-v1.0"].workflow_state == WorkflowState.RETIRED
    assert by_key["KB0010-v2.0"].workflow_state == WorkflowState.PUBLISHED

    # All articles have required body sections
    for art in articles:
        assert len(art.body) >= 50
        assert "## Symptom" in art.body
        assert "## Cause" in art.body
        assert "## Resolution" in art.body
        assert "## Escalation" in art.body
        assert len(art.short_description) <= 255
