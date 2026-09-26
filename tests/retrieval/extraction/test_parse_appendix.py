from __future__ import annotations

from app.retrieval.extraction.parse_appendix import (
    AppendixERelationships,
    find_appendix_e,
    parse_appendix_e_body,
    relationships_for_section,
)
from app.retrieval.extraction.section_detector import RawSection


class TestParseAppendixEBody:
    def test_always_returns_a_tuple_even_with_no_matching_rows(self):
        result = parse_appendix_e_body("Just some prose with no identifiers.\n")
        assert result is not None
        relationships, report = result
        assert relationships.forward == {}
        assert relationships.reverse == {}
        assert report.rows_parsed == 0

    def test_returns_a_tuple_for_empty_body(self):
        relationships, report = parse_appendix_e_body("")
        assert relationships.forward == {}
        assert report.rows_parsed == 0

    def test_parses_every_row_not_just_the_first(self):
        # Regression test: with the return mis-indented inside the loop,
        # only KB0001's row would ever be parsed.
        body = (
            "IDENTIFIER   SECTIONS\n"
            "KB0001 3.4\n"
            "KB0002 3.4, 6.1\n"
            "INC0010023 3.4\n"
            "CHG0030455 10.3, 10.4\n"
        )
        relationships, report = parse_appendix_e_body(body)
        assert report.rows_parsed == 4
        assert relationships.forward["KB0001"] == ["3.4"]
        assert relationships.forward["KB0002"] == ["3.4", "6.1"]
        assert relationships.forward["INC0010023"] == ["3.4"]
        assert relationships.forward["CHG0030455"] == ["10.3", "10.4"]

    def test_builds_reverse_map_alongside_forward(self):
        body = "KB0001 3.4\nKB0002 3.4\n"
        relationships, _ = parse_appendix_e_body(body)
        assert sorted(relationships.reverse["3.4"]) == ["KB0001", "KB0002"]

    def test_deduplicates_repeated_section_numbers_for_one_identifier(self):
        body = "KB0001 3.4, 3.4, 6.1\n"
        relationships, _ = parse_appendix_e_body(body)
        assert relationships.forward["KB0001"] == ["3.4", "6.1"]

    def test_row_with_no_leading_identifier_is_ignored(self):
        body = "This line has no identifier at the start 3.4\n"
        relationships, report = parse_appendix_e_body(body)
        assert relationships.forward == {}
        assert report.rows_parsed == 0

    def test_row_with_no_trailing_numbers_is_ignored(self):
        body = "KB0001 see appendix\n"
        relationships, report = parse_appendix_e_body(body)
        assert relationships.forward == {}
        assert report.rows_parsed == 0
        assert report.warnings == []

    def test_implausibly_large_number_is_dropped_but_warned_about(self):
        body = "KB0001 3.4, 9999\n"
        relationships, report = parse_appendix_e_body(body)
        assert relationships.forward["KB0001"] == ["3.4"]
        assert any("9999" in w for w in report.warnings)


class TestFindAppendixE:
    def test_finds_by_letter_e(self):
        sections = [
            RawSection(section_number="D", title="Reason codes", body="...", pages=(46,)),
            RawSection(
                section_number="E",
                title="Identifier index",
                body="KB0001 3.4\n",
                pages=(48,),
            ),
        ]
        found = find_appendix_e(sections)
        assert found is not None
        assert found.section_number == "E"

    def test_finds_by_title_hint_when_number_differs(self):
        sections = [
            RawSection(
                section_number="Appendix E",
                title="Full identifier index",
                body="KB0001 3.4\n",
                pages=(48,),
            ),
        ]
        assert find_appendix_e(sections) is not None

    def test_returns_none_when_absent(self):
        sections = [
            RawSection(section_number="1", title="About this manual", body="...", pages=(6,)),
        ]
        assert find_appendix_e(sections) is None


class TestRelationshipsForSection:
    def test_splits_identifiers_by_type(self):
        relationships = AppendixERelationships(
            forward={},
            reverse={
                "3.4": [
                    "KB0001",
                    "INC0010023",
                    "PRB0040018",
                    "KE0000034",
                    "CHG0030455",
                    "MIR-2026-03",
                ]
            },
        )
        result = relationships_for_section(relationships, "3.4")
        assert result["related_article_ids"] == ("KB0001",)
        assert result["related_incident_ids"] == ("INC0010023",)
        assert result["related_problem_ids"] == ("PRB0040018",)
        assert result["related_known_error_ids"] == ("KE0000034",)
        assert result["related_change_ids"] == ("CHG0030455",)
        assert result["related_mir_ids"] == ("MIR-2026-03",)

    def test_unknown_section_returns_empty_tuples(self):
        relationships = AppendixERelationships()
        result = relationships_for_section(relationships, "99.9")
        assert all(v == () for v in result.values())
