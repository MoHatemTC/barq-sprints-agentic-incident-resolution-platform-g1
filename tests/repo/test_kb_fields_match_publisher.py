"""The five kb_knowledge columns must agree across all three places they appear.

#90. These columns existed only as a manual step in servicenow/README.md §2 — nothing
in the repository created them, so a clean PDI could not run KB publishing until
someone recreated them by hand from a table in a README. They are now declared in the
Fluent source and ship with the scoped application.

That leaves three descriptions of the same schema: the Fluent source that creates the
columns, the publisher that writes them, and the README that documents them. This test
is what stops the three drifting, which is how the columns came to exist in only one of
them in the first place.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FLUENT = REPO / "servicenow/ai_incident_orchestrator/sdk-app/src/fluent/kb-knowledge-fields.now.ts"
PAYLOAD = REPO / "src/app/publishing/payload.py"
README = REPO / "servicenow/README.md"

EXPECTED_MAX_LENGTHS = {
    "x_2215032_ai_inc_0_source_id": 40,
    "x_2215032_ai_inc_0_service": 50,
    "x_2215032_ai_inc_0_version": 20,
    "x_2215032_ai_inc_0_security_level": 50,
    "x_2215032_ai_inc_0_article_number": 20,
}


def _fluent_columns() -> dict[str, int]:
    text = FLUENT.read_text(encoding="utf-8")
    return {
        name: int(length)
        for name, length in re.findall(
            r"(x_2215032_ai_inc_0_\w+):\s*StringColumn\(\{[^}]*?maxLength:\s*(\d+)",
            text,
            re.S,
        )
    }


def _publisher_fields() -> set[str]:
    text = PAYLOAD.read_text(encoding="utf-8")
    return set(re.findall(r'^U_\w+FIELD\s*=\s*"(x_2215032_ai_inc_0_\w+)"', text, re.M))


def test_fluent_declares_exactly_the_publisher_fields() -> None:
    """Every column the publisher writes is created by the app, and none is orphaned."""
    fluent = set(_fluent_columns())
    publisher = _publisher_fields()

    assert publisher, "No U_*_FIELD constants found in payload.py — has it been renamed?"
    assert fluent == publisher, (
        f"kb_knowledge columns disagree.\n"
        f"  declared in Fluent but unused by the publisher: {sorted(fluent - publisher)}\n"
        f"  written by the publisher but never created:     {sorted(publisher - fluent)}\n"
        "A column the publisher writes but the app does not create is #90 all over "
        "again: publishing fails on a clean PDI until someone adds it by hand."
    )


def test_fluent_max_lengths_match_the_documented_schema() -> None:
    """A too-short column truncates silently; source_id truncation breaks idempotency.

    The lookup key is `<number>-v<version>`, so a shortened source_id would make two
    different articles collide on the same key and overwrite each other.
    """
    assert _fluent_columns() == EXPECTED_MAX_LENGTHS, (
        f"Fluent max lengths {_fluent_columns()} do not match the schema documented in "
        f"servicenow/README.md §1 {EXPECTED_MAX_LENGTHS}."
    )


def test_readme_still_documents_every_column() -> None:
    """The README table is what a human follows when verifying an instance."""
    text = README.read_text(encoding="utf-8")
    missing = [name for name in EXPECTED_MAX_LENGTHS if name not in text]
    assert not missing, f"servicenow/README.md no longer documents: {missing}"


def test_columns_carry_the_application_prefix() -> None:
    """The prefix is what keeps these out of the Global scope (#48)."""
    for name in _fluent_columns():
        assert name.startswith("x_2215032_ai_inc_0_"), (
            f"{name!r} lacks the application prefix, so it would be created in Global "
            "scope and change the kb_knowledge table for every application."
        )
