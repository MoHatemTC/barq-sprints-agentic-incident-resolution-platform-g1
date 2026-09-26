from __future__ import annotations

import pytest

from app.retrieval.extraction.section_detector import (
    SECTION_HEADING_RE,
    build_page_stream,
    find_section_headings,
    page_for_offset,
    split_into_sections,
)


class TestSectionHeadingRegex:
    def test_matches_simple_numbered_heading(self):
        assert SECTION_HEADING_RE.match("3.4 Response and resolution targets")

    def test_matches_top_level_heading(self):
        assert SECTION_HEADING_RE.match("2 The service desk")

    def test_matches_appendix_letter_heading(self):
        assert SECTION_HEADING_RE.match("E Identifier index")


class TestFindSectionHeadings:
    def test_rejects_numbered_prose_sentence(self):
        stream = (
            "3.3 Emergency change procedure\n"
            "Resolution.\n"
            " 1. Do not restart the application server.\n"
            " 2. Confirm pool saturation from the service metrics "
            "dashboard rather than from the error message alone.\n"
            " 3. Notify the Order Processing service owner.\n"
        )
        headings = find_section_headings(stream)
        assert [h.section_number for h in headings] == ["3.3"]

    def test_rejects_toc_leader_lines(self):
        stream = "3.4 Response and resolution targets ..... 11\n"
        assert find_section_headings(stream) == []

    def test_rejects_footer_fragments(self):
        assert find_section_headings("7 of 52\n") == []

    def test_excludes_headings_on_excluded_pages(self):
        page_1 = "1 About this manual\nSome intro text.\n"
        page_2 = "2 The service desk\nMore text.\n"
        stream = build_page_stream([page_1, page_2])
        headings = find_section_headings(stream, exclude_pages={2})
        assert [h.section_number for h in headings] == ["1"]

    def test_finds_real_short_headings(self):
        stream = (
            "1.3 Conventions\n"
            "Some prose about conventions.\n"
            "2 The service desk\n"
            "2.1 Operating model\n"
            "The desk runs a follow-the-sun pattern.\n"
        )
        headings = find_section_headings(stream)
        assert [h.section_number for h in headings] == ["1.3", "2", "2.1"]


class TestSplitIntoSections:
    def test_splits_body_at_next_heading(self):
        stream = (
            "1 About this manual\n"
            "This manual describes the service desk.\n"
            "1.1 Purpose and audience\n"
            "It is for analysts and their leads.\n"
        )
        sections = split_into_sections(stream)
        assert [s.section_number for s in sections] == ["1", "1.1"]
        assert "This manual describes the service desk." in sections[0].body
        assert "1.1" not in sections[0].body
        assert "It is for analysts and their leads." in sections[1].body

    def test_no_headings_returns_empty_list(self):
        assert split_into_sections("Just some prose, no headings.\n") == []

    def test_last_section_runs_to_end_of_stream(self):
        stream = "9 Major incident report\nEverything after this belongs here.\n"
        sections = split_into_sections(stream)
        assert len(sections) == 1
        assert "Everything after this belongs here." in sections[0].body

    def test_heading_that_shares_a_page_with_a_table_is_not_swallowed(self):
        # Regression test for the table-page text-loss bug: a heading and
        # its short paragraph must survive even when the rest of the page
        # is a rendered table, and the preceding section must not balloon
        # to swallow every following page.
        page_1 = "1.3 Conventions\nField labels appear in Title Case.\n"
        page_2 = (
            "2 The service desk\n"
            "2.1 Operating model\n"
            "The desk runs a follow-the-sun pattern.\n"
            "| column_0 | column_1 |\n"
            "| --- | --- |\n"
            "| Working hours | 08:00-18:00 |\n"
        )
        page_3 = "3 Incident management\nThe lifecycle starts here.\n"
        stream = build_page_stream([page_1, page_2, page_3])
        sections = split_into_sections(stream)
        numbers = [s.section_number for s in sections]
        assert numbers == ["1.3", "2", "2.1", "3"]
        assert "Working hours" not in sections[numbers.index("1.3")].body
        assert "lifecycle" not in sections[numbers.index("1.3")].body
        assert "Working hours" in sections[numbers.index("2.1")].body


class TestPageForOffset:
    def test_first_page_is_one(self):
        stream = build_page_stream(["page one text"])
        assert page_for_offset(stream, 0) == 1

    def test_offset_after_separator_is_next_page(self):
        stream = build_page_stream(["short", "longer page two"])
        idx = stream.index("longer")
        assert page_for_offset(stream, idx) == 2

    def test_out_of_bounds_offset_raises(self):
        stream = build_page_stream(["a"])
        with pytest.raises(ValueError):
            page_for_offset(stream, len(stream) + 1)
