from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.models.manual_section import ManualSection, ManualSectionType, TableBlock
from app.retrieval.manual.markdown_parser import (
    MarkdownParseReport,
    _classify_heading,
    _extract_page_number,
    _is_noise_line,
    _parent_section_number,
    _parse_blocks_from_body,
    parse_markdown_manual,
)

# ---------------------------------------------------------------------------
# Section heading classification
# ---------------------------------------------------------------------------


class TestClassifyHeading:
    """Tests for _classify_heading: recognises valid section headings and
    rejects callouts, emphasis and diagram labels."""

    def test_numbered_top_level(self) -> None:
        assert _classify_heading("# 1. About this manual") == ("1", "About this manual")

    def test_numbered_subsection(self) -> None:
        assert _classify_heading("# 3.4 Response and resolution targets") == (
            "3.4",
            "Response and resolution targets",
        )

    def test_numbered_two_digit(self) -> None:
        assert _classify_heading("# 10.2 Pre-approved standard changes") == (
            "10.2",
            "Pre-approved standard changes",
        )

    def test_appendix_with_separator(self) -> None:
        assert _classify_heading("# Appendix A · Glossary") == ("A", "Glossary")

    def test_appendix_without_title(self) -> None:
        assert _classify_heading("# Appendix D") == ("D", "Appendix D")

    def test_appendix_subsection(self) -> None:
        assert _classify_heading("# B.1 Escalation handover") == ("B.1", "Escalation handover")

    def test_callout_heading_rejected(self) -> None:
        # Emphasis heading, not a real section.
        assert _classify_heading("# Write the article number, every time") is None

    def test_action_callout_rejected(self) -> None:
        assert _classify_heading("# Action 6 is still open, and it is the one that matters") is None

    def test_non_heading_rejected(self) -> None:
        assert _classify_heading("## BARQ Systems · IT Service Operations Manual") is None

    def test_level2_heading_rejected(self) -> None:
        assert _classify_heading("## INTERNAL DOCUMENT") is None

    def test_toc_heading_rejected(self) -> None:
        assert _classify_heading("# Contents") is None

    def test_resolution_heading_rejected(self) -> None:
        # "# Resolution." appears in KB articles but is not a section number.
        assert _classify_heading("# Resolution.") is None

    def test_front_matter_detected(self) -> None:
        assert _classify_heading("# Document control") == (
            "front-document-control",
            "Document control",
        )


# ---------------------------------------------------------------------------
# Noise line detection
# ---------------------------------------------------------------------------


class TestNoiseLineDetection:
    def test_page_header_barq(self) -> None:
        assert _is_noise_line("## BARQ Systems · IT Service Operations Manual")

    def test_page_header_internal(self) -> None:
        assert _is_noise_line("## INTERNAL DOCUMENT")

    def test_page_number_bold(self) -> None:
        assert _is_noise_line("**5** of 52 ·")

    def test_page_number_plain(self) -> None:
        assert _is_noise_line("22 of 52 ·")

    def test_edition_footer(self) -> None:
        assert _is_noise_line("Edition 4.0")

    def test_logo_line(self) -> None:
        assert _is_noise_line("BARQ")
        assert _is_noise_line("SYSTEMS")

    def test_normal_content_not_noise(self) -> None:
        assert not _is_noise_line("Priority is derived, not chosen.")

    def test_empty_line_not_noise(self) -> None:
        assert not _is_noise_line("")


# ---------------------------------------------------------------------------
# Page number extraction
# ---------------------------------------------------------------------------


class TestPageNumberExtraction:
    def test_bold_page_number(self) -> None:
        assert _extract_page_number("**5** of 52 ·") == 5

    def test_plain_page_number(self) -> None:
        assert _extract_page_number("22 of 52 ·") == 22

    def test_no_page_number(self) -> None:
        assert _extract_page_number("Some regular text") is None


# ---------------------------------------------------------------------------
# Parent section derivation
# ---------------------------------------------------------------------------


class TestParentSection:
    def test_subsection_parent(self) -> None:
        assert _parent_section_number("3.4") == "3"

    def test_sub_subsection_parent(self) -> None:
        assert _parent_section_number("10.1.2") == "10.1"

    def test_appendix_subsection_parent(self) -> None:
        assert _parent_section_number("B.1") == "B"

    def test_top_level_no_parent(self) -> None:
        assert _parent_section_number("3") is None

    def test_appendix_no_parent(self) -> None:
        assert _parent_section_number("A") is None


# ---------------------------------------------------------------------------
# HTML table → TableBlock conversion
# ---------------------------------------------------------------------------


class TestHtmlTableToText:
    def test_simple_table(self) -> None:
        html = (
            "<table>"
            "<tr><th>PRIORITY</th><th>RESPONSE</th></tr>"
            "<tr><td>P1</td><td>15 minutes</td></tr>"
            "<tr><td>P2</td><td>30 minutes</td></tr>"
            "</table>"
        )
        blocks = _parse_blocks_from_body(html)
        assert len(blocks) == 1
        block = blocks[0]
        assert isinstance(block, TableBlock)
        assert block.headers == ["PRIORITY", "RESPONSE"]
        assert block.rows == [["P1", "15 minutes"], ["P2", "30 minutes"]]

        result = block.to_semantic_text()
        assert "PRIORITY: P1" in result
        assert "RESPONSE: 15 minutes" in result
        assert "PRIORITY: P2" in result

    def test_empty_table(self) -> None:
        blocks = _parse_blocks_from_body("<table></table>")
        assert len(blocks) == 0

    def test_table_with_bold(self) -> None:
        html = (
            "<table>"
            "<tr><th>TERM</th><th>MEANING</th></tr>"
            "<tr><td>Bridge</td><td>The <b>conference</b> line</td></tr>"
            "</table>"
        )
        blocks = _parse_blocks_from_body(html)
        block = blocks[0]
        assert isinstance(block, TableBlock)

        result = block.to_semantic_text()
        assert "TERM: Bridge" in result
        assert "conference" in result

    def test_rowspan_table(self) -> None:
        html = (
            "<table>"
            "<tr><th>AREA</th><th>TRIGGER</th></tr>"
            '<tr><td rowspan="2">Network</td><td>VPN</td></tr>'
            "<tr><td>Wi-Fi</td></tr>"
            "</table>"
        )
        blocks = _parse_blocks_from_body(html)
        block = blocks[0]
        assert isinstance(block, TableBlock)

        result = block.to_semantic_text()
        assert "AREA: Network" in result
        assert "TRIGGER: VPN" in result


# ---------------------------------------------------------------------------
# Full parse of the actual parsed-pdf.md
# ---------------------------------------------------------------------------

PARSED_PDF_PATH = Path("data/corpus/parsed-pdf.md")


@pytest.mark.skipif(
    not PARSED_PDF_PATH.exists(),
    reason="parsed-pdf.md not available in this environment",
)
class TestFullParsedPdf:
    """End-to-end tests against the real parsed-pdf.md file."""

    @pytest.fixture(scope="class")
    def parsed(self) -> tuple[list[ManualSection], object, MarkdownParseReport]:
        return parse_markdown_manual(PARSED_PDF_PATH)

    @pytest.fixture(scope="class")
    def sections(self, parsed: tuple) -> list[ManualSection]:
        return parsed[0]

    @pytest.fixture(scope="class")
    def relationships(self, parsed: tuple) -> object:
        return parsed[1]

    @pytest.fixture(scope="class")
    def report(self, parsed: tuple) -> MarkdownParseReport:
        return parsed[2]

    def test_section_count_reasonable(self, sections: list[ManualSection]) -> None:
        # The manual has ~50+ identifiable sections; must produce at least 40.
        assert len(sections) >= 40, f"Only {len(sections)} sections detected"

    def test_no_empty_bodies(self, sections: list[ManualSection]) -> None:
        for s in sections:
            assert s.body.strip(), f"Section {s.section_number} has empty body"

    def test_eval_expected_sections_present(self, sections: list[ManualSection]) -> None:
        """The barq_rag_eval_dataset.json references these sections as
        expected_sections — they must all be detected by the parser."""
        required = {
            "1.1",
            "1.3",
            "3.1",
            "3.3",
            "3.4",
            "3.5",
            "3.6",
            "4.1",
            "4.2",
            "6.4",
            "6.8",
            "7.4",
        }
        found = {s.section_number for s in sections}
        missing = required - found
        assert not missing, f"Missing eval-required sections: {missing}"

    def test_appendix_c_detected(self, sections: list[ManualSection]) -> None:
        found = {s.section_number for s in sections}
        assert "C" in found, "Appendix C must be detected"

    def test_section_11_subsections(self, sections: list[ManualSection]) -> None:
        s11 = {s.section_number for s in sections if s.section_number.startswith("11.")}
        assert "11.1" in s11
        assert "11.3" in s11

    def test_no_front_matter_sections(self, sections: list[ManualSection]) -> None:
        for s in sections:
            assert not s.section_number.startswith("front-"), (
                f"Front-matter section {s.section_number} should be excluded"
            )

    def test_tables_converted_to_text(self, sections: list[ManualSection]) -> None:
        """No raw HTML <table> tags should remain in section bodies."""
        for s in sections:
            assert "<table>" not in s.body, f"Section {s.section_number} still has raw HTML tables"

    def test_no_page_headers_in_bodies(self, sections: list[ManualSection]) -> None:
        for s in sections:
            assert "## BARQ Systems" not in s.body, (
                f"Section {s.section_number} contains page header noise"
            )
            assert "## INTERNAL DOCUMENT" not in s.body, (
                f"Section {s.section_number} contains page header noise"
            )

    def test_section_64_kb0001_content(self, sections: list[ManualSection]) -> None:
        """Section 6.4 (KB0001) must contain the resolution steps."""
        s64 = next((s for s in sections if s.section_number == "6.4"), None)
        assert s64 is not None, "Section 6.4 not found"
        assert "VPN" in s64.body
        assert "cached credential" in s64.body.lower()
        assert "Resolution" in s64.body

    def test_section_68_kb0005_content(self, sections: list[ManualSection]) -> None:
        """Section 6.8 (KB0005) must contain account lockout content."""
        s68 = next((s for s in sections if s.section_number == "6.8"), None)
        assert s68 is not None, "Section 6.8 not found"
        assert "locked" in s68.body.lower()
        assert "identity" in s68.body.lower()

    def test_appendix_e_relationships_populated(self, relationships: object) -> None:
        from app.retrieval.extraction.parse_appendix import AppendixERelationships

        assert isinstance(relationships, AppendixERelationships)
        assert len(relationships.forward) > 0, "No forward relationships extracted"
        # KB0001 must map to at least sections 6.4, 5.2.
        assert "KB0001" in relationships.forward
        sections_for_kb0001 = relationships.forward["KB0001"]
        assert "6.4" in sections_for_kb0001

    def test_report_noise_stripped(self, report: MarkdownParseReport) -> None:
        assert report.noise_lines_stripped > 0
        assert report.tables_converted > 0

    def test_section_ids_deterministic(self, sections: list[ManualSection]) -> None:
        for s in sections:
            expected_id = ManualSection.build_section_id(s.section_number)
            assert s.section_id == expected_id

    def test_section_bodies_have_no_html_artifacts(self, sections: list[ManualSection]) -> None:
        """Bodies should be clean text with no leftover HTML tags."""
        for s in sections:
            # Allow & in text (e.g. "Identity & Access"), but no HTML entities.
            assert "&amp;" not in s.body, f"Section {s.section_number} has HTML entity &amp;"

    def test_pages_field_populated(self, sections: list[ManualSection]) -> None:
        for s in sections:
            assert len(s.pages) > 0, f"Section {s.section_number} has no page numbers"


# ---------------------------------------------------------------------------
# Chunking integration
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not PARSED_PDF_PATH.exists(),
    reason="parsed-pdf.md not available in this environment",
)
class TestChunkingIntegration:
    """Verify that parsed sections integrate with the existing chunker."""

    def test_sections_chunk_without_errors(self) -> None:
        from app.retrieval.manual.manual_chunking import chunk_sections
        from app.retrieval.manual.markdown_parser import parse_markdown_manual

        sections, relationships, _ = parse_markdown_manual(PARSED_PDF_PATH)
        chunks = chunk_sections(sections, relationships)

        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk.text.strip()
            assert chunk.section_number
            assert chunk.section_title

    def test_chunk_ids_are_deterministic(self) -> None:
        from app.retrieval.manual.manual_chunking import chunk_sections
        from app.retrieval.manual.markdown_parser import parse_markdown_manual

        sections, relationships, _ = parse_markdown_manual(PARSED_PDF_PATH)
        chunks1 = chunk_sections(sections, relationships)
        chunks2 = chunk_sections(sections, relationships)

        ids1 = [c.chunk_id for c in chunks1]
        ids2 = [c.chunk_id for c in chunks2]
        assert ids1 == ids2

    def test_no_chunks_contain_html(self) -> None:
        from app.retrieval.manual.manual_chunking import chunk_sections
        from app.retrieval.manual.markdown_parser import parse_markdown_manual

        sections, relationships, _ = parse_markdown_manual(PARSED_PDF_PATH)
        chunks = chunk_sections(sections, relationships)

        for chunk in chunks:
            assert "<table>" not in chunk.text
            assert "<tr>" not in chunk.text
            assert "<td>" not in chunk.text


# ---------------------------------------------------------------------------
# Qdrant payload generation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not PARSED_PDF_PATH.exists(),
    reason="parsed-pdf.md not available in this environment",
)
class TestPayloadGeneration:
    """Verify that parsed chunks produce valid Qdrant payloads."""

    def test_payload_schema_valid(self) -> None:
        from app.models.manual_section import ManualSectionPayload
        from app.retrieval.manual.manual_chunking import chunk_sections
        from app.retrieval.manual.markdown_parser import parse_markdown_manual

        sections, relationships, _ = parse_markdown_manual(PARSED_PDF_PATH)
        chunks = chunk_sections(sections, relationships)

        for chunk in chunks[:10]:  # Spot-check first 10.
            payload = ManualSectionPayload.from_chunk(chunk)
            d = payload.to_qdrant_payload()
            assert "section_id" in d
            assert "section_number" in d
            assert "chunk_text" in d
            assert d["doc_type"] == "manual_section"


# ---------------------------------------------------------------------------
# Inline markdown parse (synthetic)
# ---------------------------------------------------------------------------


class TestSyntheticMarkdownParse:
    """Parse a small synthetic markdown to test edge cases without the full file."""

    def _write_and_parse(self, tmp_path: Path, content: str):
        md = tmp_path / "test.md"
        md.write_text(textwrap.dedent(content), encoding="utf-8")
        return parse_markdown_manual(md)

    def test_basic_two_sections(self, tmp_path: Path) -> None:
        content = """\
        # 1. Introduction

        This is the introduction.

        # 1.1 Purpose

        This describes the purpose.
        """
        sections, _, report = self._write_and_parse(tmp_path, content)
        assert len(sections) == 2
        assert sections[0].section_number == "1"
        assert sections[1].section_number == "1.1"
        assert "introduction" in sections[0].body.lower()
        assert "purpose" in sections[1].body.lower()

    def test_page_furniture_stripped(self, tmp_path: Path) -> None:
        content = """\
        ## BARQ Systems · IT Service Operations Manual

        ## INTERNAL DOCUMENT

        # 1.1 Purpose

        Content here.

        Edition 4.0

        **6** of 52 ·
        """
        sections, _, report = self._write_and_parse(tmp_path, content)
        assert len(sections) == 1
        assert "BARQ Systems" not in sections[0].body
        assert "INTERNAL DOCUMENT" not in sections[0].body
        assert "Edition 4.0" not in sections[0].body
        assert report.noise_lines_stripped >= 3

    def test_appendix_detection(self, tmp_path: Path) -> None:
        content = """\
        # Appendix A · Glossary

        Bridge: a conference line.

        # B.1 Escalation handover

        Template content.
        """
        sections, _, _ = self._write_and_parse(tmp_path, content)
        assert len(sections) == 2
        assert sections[0].section_number == "A"
        assert sections[0].title == "Glossary"
        assert sections[1].section_number == "B.1"

    def test_html_table_converted(self, tmp_path: Path) -> None:
        content = """\
        # 3.4 Targets

        <table><tr><th>PRIORITY</th><th>RESPONSE</th></tr><tr><td>P1
        </td><td>15 min</td></tr></table>
        """
        sections, _, report = self._write_and_parse(tmp_path, content)
        assert len(sections) == 1
        assert "<table>" not in sections[0].body
        assert "PRIORITY: P1" in sections[0].body
        assert sections[0].content_type == ManualSectionType.TABLE
        assert report.tables_converted == 1

    def test_callout_heading_not_section(self, tmp_path: Path) -> None:
        content = """\
        # 3.6 Journalling

        The journal is the record.

        # Write the article number, every time

        This is a callout, not a new section.
        """
        sections, _, _ = self._write_and_parse(tmp_path, content)
        # The callout should be part of section 3.6's body.
        assert len(sections) == 1
        assert sections[0].section_number == "3.6"
        assert "Write the article number" in sections[0].body

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_markdown_manual(tmp_path / "nonexistent.md")
