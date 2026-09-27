from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.manual_section import ManualSectionType
from app.retrieval.extraction.ocr_extraction import OcrPageResult
from app.retrieval.extraction.section_detector import RawSection
from app.retrieval.extraction.table_extraction import (
    ExtractedTable,
    _grid_from_raw_rows,
)
from app.retrieval.manual.manual_parser import (
    PageInfo,
    ParseReport,
    _classify_remaining_pages,
    _collapse_alignment_whitespace,
    _content_type_for,
    _is_table_of_contents,
    _render_tables,
    _run_whole_page_ocr,
    build_manual_sections,
)


class TestCollapseAlignmentWhitespace:
    def test_collapses_multi_space_runs_to_a_single_space(self):
        assert _collapse_alignment_whitespace("Name    Priority     Notes") == (
            "Name Priority Notes"
        )

    def test_leaves_single_spaces_alone(self):
        assert _collapse_alignment_whitespace("one two three") == "one two three"

    def test_preserves_newlines_between_lines(self):
        text = "Header1     Header2\nrow1val1   row1val2"
        assert _collapse_alignment_whitespace(text) == "Header1 Header2\nrow1val1 row1val2"

    def test_rstrips_trailing_whitespace_per_line(self):
        result = _collapse_alignment_whitespace("line one   \nline two")
        assert result.split("\n")[0] == "line one"


class TestIsTableOfContents:
    def test_page_with_many_dotted_leaders_is_toc(self):
        text = "\n".join(f"Section {i} .......... {i}" for i in range(1, 8))
        assert _is_table_of_contents(text) is True

    def test_ordinary_prose_page_is_not_toc(self):
        text = "This page describes the service desk operating model in detail."
        assert _is_table_of_contents(text) is False

    def test_blank_page_is_not_toc(self):
        assert _is_table_of_contents("") is False

    def test_a_few_dotted_lines_below_threshold_is_not_toc(self):
        text = "\n".join(f"Section {i} .......... {i}" for i in range(1, 4))
        assert _is_table_of_contents(text) is False


class TestContentTypeFor:
    @staticmethod
    def _pages():
        return {
            1: PageInfo(1, ManualSectionType.PROSE, "prose text"),
            2: PageInfo(2, ManualSectionType.TABLE, "table text"),
            3: PageInfo(3, ManualSectionType.OCR, "ocr text", ocr_confidence=0.7),
            4: PageInfo(4, ManualSectionType.LAYOUT, "layout text"),
        }

    def test_table_wins_over_everything_else(self):
        assert _content_type_for(self._pages(), (1, 2, 3, 4)) == ManualSectionType.TABLE

    def test_ocr_wins_over_layout_and_prose(self):
        assert _content_type_for(self._pages(), (1, 3, 4)) == ManualSectionType.OCR

    def test_layout_wins_over_prose(self):
        assert _content_type_for(self._pages(), (1, 4)) == ManualSectionType.LAYOUT

    def test_defaults_to_prose(self):
        assert _content_type_for(self._pages(), (1,)) == ManualSectionType.PROSE

    def test_missing_pages_are_ignored_not_errors(self):
        assert _content_type_for(self._pages(), (1, 999)) == ManualSectionType.PROSE


class TestRenderTables:
    def test_drops_implausible_tables_and_renders_plausible_ones(self):
        plausible = ExtractedTable(
            page_number=1,
            bbox=(0, 0, 10, 10),
            cells=_grid_from_raw_rows([["Service", "Priority"], ["order-processing", "P1"]]),
        )
        implausible = ExtractedTable(
            page_number=1,
            bbox=(0, 0, 10, 10),
            cells=_grid_from_raw_rows([["", ""], ["ZZZDROPPED", "b"]]),
        )
        rendered = _render_tables([implausible, plausible])
        assert "order-processing" in rendered
        assert "ZZZDROPPED" not in rendered

    def test_empty_input_returns_empty_string(self):
        assert _render_tables([]) == ""


class TestClassifyRemainingPagesTableBranch:
    """Covers only the table branch, which returns before ever touching
    pdfplumber -- reached whenever a page is force-classified as a table
    or a plausible table was already found on it."""

    def test_real_page_prose_is_preserved_ahead_of_the_rendered_table(self):
        # Regression test for the table-page text-loss bug: a heading and
        # its short paragraph must survive on a page that also contains a
        # table, not be replaced by the table's rendered text alone.
        report = ParseReport()
        plain_by_page = {8: "2.1 Operating model\nThe desk runs a follow-the-sun pattern."}
        table = ExtractedTable(
            page_number=8,
            bbox=(0, 0, 10, 10),
            cells=_grid_from_raw_rows([["Coverage", "Hours"], ["Working hours", "08:00-18:00"]]),
        )

        pages = _classify_remaining_pages(
            pdf_path=Path("unused.pdf"),
            remaining=[8],
            plain_by_page=plain_by_page,
            plausible_tables_by_page={8: [table]},
            table_pages=set(),
            layout_pages=set(),
            report=report,
        )

        assert 8 in report.table_pages
        assert pages[8].content_type == ManualSectionType.TABLE
        assert "2.1 Operating model" in pages[8].text
        assert "The desk runs a follow-the-sun pattern." in pages[8].text
        assert "Working hours" in pages[8].text

    def test_forced_table_page_with_no_plausible_table_falls_back_to_prose(self):
        report = ParseReport()
        plain_by_page = {7: "Just some page prose."}

        pages = _classify_remaining_pages(
            pdf_path=Path("unused.pdf"),
            remaining=[7],
            plain_by_page=plain_by_page,
            plausible_tables_by_page={},
            table_pages={7},
            layout_pages=set(),
            report=report,
        )

        assert pages[7].text == "Just some page prose."
        assert any("no plausible table" in w for w in report.warnings)


class TestRunWholePageOcr:
    def test_returns_empty_dict_when_no_candidates(self):
        report = ParseReport()
        pages = _run_whole_page_ocr(
            pdf_path=Path("unused.pdf"),
            total_pages=2,
            plain_by_page={
                1: "plenty of real text here",
                2: "also plenty of real text",
            },
            table_pages=set(),
            layout_pages=set(),
            report=report,
        )
        assert pages == {}
        assert report.ocr_pages == []

    def test_ocrs_pages_with_no_usable_text_layer_and_flags_low_confidence(self):
        report = ParseReport()
        plain_by_page = {i: "plenty of real text here for this page" for i in range(1, 9)}
        plain_by_page[9] = ""  # below OCR_TEXT_LENGTH_FLOOR -> OCR candidate
        fake_result = OcrPageResult(page_number=9, text="scanned text", mean_confidence=0.2)

        with (
            patch(
                "app.retrieval.manual.manual_parser.extract_ocr_text",
                return_value=[fake_result],
            ),
            patch(
                "app.retrieval.manual.manual_parser.strip_page_headers_and_footers",
                side_effect=lambda t: t,
            ),
        ):
            pages = _run_whole_page_ocr(
                pdf_path=Path("unused.pdf"),
                total_pages=9,
                plain_by_page=plain_by_page,
                table_pages=set(),
                layout_pages=set(),
                report=report,
            )

        assert 9 in report.ocr_pages
        assert pages[9].content_type == ManualSectionType.OCR
        assert pages[9].text == "scanned text"
        assert pages[9].ocr_confidence == pytest.approx(0.2)
        assert any("below the" in w for w in report.warnings)

    def test_forced_table_pages_are_excluded_from_ocr_candidates(self):
        report = ParseReport()
        pages = _run_whole_page_ocr(
            pdf_path=Path("unused.pdf"),
            total_pages=1,
            plain_by_page={1: ""},  # would be a candidate, but page 1 is a forced table
            table_pages={1},
            layout_pages=set(),
            report=report,
        )
        assert pages == {}


class TestBuildManualSections:
    @staticmethod
    def _pages():
        return {
            6: PageInfo(6, ManualSectionType.PROSE, "prose"),
            41: PageInfo(41, ManualSectionType.PROSE, "prose"),
        }

    def test_normal_section_is_built(self):
        raw = [
            RawSection(
                section_number="1",
                title="About this manual",
                body="Real body text.",
                pages=(6,),
            )
        ]
        sections = build_manual_sections(raw, self._pages())
        assert len(sections) == 1
        assert sections[0].body == "Real body text."

    def test_empty_body_section_is_skipped_not_raised(self):
        raw = [
            RawSection(section_number="1", title="About", body="Real body.", pages=(6,)),
            RawSection(section_number="2", title="Empty", body="   ", pages=(41,)),
        ]
        sections = build_manual_sections(raw, self._pages())
        assert len(sections) == 1
        assert sections[0].section_number == "1"

    def test_ocr_confidence_is_averaged_across_the_sections_pages(self):
        pages = {
            10: PageInfo(10, ManualSectionType.OCR, "a", ocr_confidence=0.9),
            11: PageInfo(11, ManualSectionType.OCR, "b", ocr_confidence=0.7),
        }
        raw = [RawSection(section_number="6", title="Scanned page", body="text", pages=(10, 11))]
        sections = build_manual_sections(raw, pages)
        assert sections[0].ocr_confidence == pytest.approx(0.8)
        assert sections[0].reliability_note is None

    def test_low_ocr_confidence_sets_a_reliability_note(self):
        pages = {18: PageInfo(18, ManualSectionType.OCR, "a", ocr_confidence=0.3)}
        raw = [RawSection(section_number="6.3", title="Archived scan", body="text", pages=(18,))]
        sections = build_manual_sections(raw, pages)
        assert sections[0].reliability_note is not None
        assert "0.30" in sections[0].reliability_note
