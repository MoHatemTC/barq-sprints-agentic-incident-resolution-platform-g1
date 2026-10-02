from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import structlog

from app.models.manual_section import ManualSection, ManualSectionType, TableBlock, TextBlock
from app.retrieval.extraction.parse_appendix import AppendixERelationships

logger = structlog.get_logger(__name__)

# Numbered: "3", "3.4", "10.1.2"
_NUMBERED_SECTION_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})(?:\.?\s+|\s*$)")

# Appendix top-level:  "Appendix A · Glossary", "Appendix C · Impact…"
_APPENDIX_RE = re.compile(r"^Appendix\s+([A-Z])(?:\s+[·—–-]\s+(.+))?$")

# Appendix sub-section: "B.1 Escalation handover"
_APPENDIX_SUB_RE = re.compile(r"^([A-Z]\.\d{1,2})\s+(.+)$")

# Document-control headings we include as special front-matter sections.
_FRONT_MATTER_TITLES = {"Document control", "Version history", "Ownership and review"}
_PAGE_HEADER_RE = re.compile(r"^##\s+(?:BARQ\s+Systems|INTERNAL\s+DOCUMENT)", re.IGNORECASE)
_PAGE_NUMBER_RE = re.compile(r"^\s*\*{0,2}\d{1,3}\*{0,2}\s+of\s+\d{1,3}\s*·?\s*$")
_EDITION_RE = re.compile(r"^Edition\s+\d+\.\d+")
_LOGO_LINE_RE = re.compile(r"^(?:BARQ|SYSTEMS)\s*$")
_TOC_ENTRY_RE = re.compile(r"\.{3,}\s*\d+\s*$")


# ---------------------------------------------------------------------------
# HTML-table → structured TableBlock conversion
# ---------------------------------------------------------------------------


class _TableBlockExtractor(HTMLParser):
    """Lossless HTML-table → TableBlock converter.

    Strategy:
    - If the first row contains *only* <th> cells, use it as the header row.
    - Otherwise every row is rendered without any header prefix.
    - Inline formatting tags (<b>, <i>, <strong>, <em>) are ignored; their
      text content is kept.
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[list[str]] = []
        self._row_is_header: list[bool] = []  # True if the whole row was <th>
        self._current_row: list[str] | None = None
        self._current_row_all_th: bool = True  # tracks if current row is all-th
        self._current_cell: list[str] | None = None
        self._in_header_cell = False
        self._depth = 0  # nesting depth for nested tables
        # Track active rowspans: col_index -> (remaining_rows, text)
        self._active_rowspans: dict[int, list[Any]] = {}
        self._current_row_spans: list[tuple[int, int, str]] = []  # (col, span, text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth > 1 and self._current_cell is not None:
                # Add a separator before nested table contents
                self._current_cell.append(" [ ")
        elif self._depth == 1:
            if tag == "tr":
                self._current_row = []
                self._current_row_all_th = True
                self._current_row_spans = []
            elif tag in ("td", "th"):
                self._current_cell = []
                self._in_header_cell = tag == "th"
                if tag == "td":
                    self._current_row_all_th = False
                
                # Check for rowspan/colspan
                rowspan = int(attr_dict.get("rowspan", 1) or 1)
                colspan = int(attr_dict.get("colspan", 1) or 1)
                self._current_cell_spans = (rowspan, colspan)
        elif self._depth > 1:
            if tag in ("td", "th") and self._current_cell is not None:
                self._current_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._depth > 1 and self._current_cell is not None:
                self._current_cell.append(" ] ")
            self._depth -= 1
        elif self._depth == 1:
            if tag in ("td", "th") and self._current_cell is not None:
                text = " ".join("".join(self._current_cell).split())
                rowspan, colspan = getattr(self, "_current_cell_spans", (1, 1))
                if self._current_row is not None:
                    col_idx = len(self._current_row)
                    # Account for previously active rowspans inserting into current row
                    while col_idx in self._active_rowspans:
                        col_idx += 1
                    for _ in range(colspan):
                        self._current_row.append(text)
                    if rowspan > 1:
                        self._current_row_spans.append((col_idx, rowspan - 1, text, colspan))
                self._current_cell = None
            elif tag == "tr" and self._current_row is not None:
                # Insert inherited rowspans from previous rows into current row
                complete_row = []
                cell_iter = iter(self._current_row)
                new_active = {}

                # First, process existing active rowspans
                active_cols_span = sum(s[2] for s in self._active_rowspans.values())
                max_cols = max(len(self._current_row) + active_cols_span, 1)
                for c in range(max_cols):
                    if c in self._active_rowspans:
                        rem, text, cspan = self._active_rowspans[c]
                        complete_row.append(text)
                        if rem > 1:
                            new_active[c] = (rem - 1, text, cspan)
                    else:
                        val = next(cell_iter, None)
                        if val is not None:
                            complete_row.append(val)
                # Any trailing cells
                for val in cell_iter:
                    complete_row.append(val)

                # Register new rowspans for subsequent rows
                for col_idx, rem, text, cspan in self._current_row_spans:
                    for offset in range(cspan):
                        new_active[col_idx + offset] = (rem, text, 1)

                self._active_rowspans = new_active
                self._rows.append(complete_row)
                self._row_is_header.append(self._current_row_all_th)
                self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def get_table_block(self) -> TableBlock | None:
        if not self._rows:
            return None

        # Use first row as header ONLY if it was composed entirely of <th> cells.
        headers: list[str] | None = None
        data_rows = self._rows
        if self._row_is_header and self._row_is_header[0]:
            headers = self._rows[0]
            data_rows = self._rows[1:]

        column_count = len(headers) if headers else (len(data_rows[0]) if data_rows else 0)

        return TableBlock(
            headers=headers,
            rows=data_rows,
            row_count=len(data_rows),
            column_count=column_count,
        )


# ---------------------------------------------------------------------------
# Section heading detection
# ---------------------------------------------------------------------------


@dataclass
class _RawParsedSection:
    """Intermediate section before ManualSection validation."""

    section_number: str
    title: str
    body_lines: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    has_tables: bool = False
    parent_section: str | None = None


def _classify_heading(line: str) -> tuple[str, str] | None:
    """Try to extract (section_number, title) from a ``# …`` heading line.

    Returns None for non-section headings (callouts, emphasis, diagram labels).
    """
    if not line.startswith("# "):
        return None
    text = line[2:].strip()
    if text.lower() == "contents":
        return None

    # Appendix top-level:  "Appendix A · Glossary"
    m = _APPENDIX_RE.match(text)
    if m:
        letter = m.group(1)
        title = m.group(2) or f"Appendix {letter}"
        return letter, title.strip()

    # Appendix sub-section:  "B.1 Escalation handover"
    m = _APPENDIX_SUB_RE.match(text)
    if m:
        return m.group(1), m.group(2).strip()

    # Numbered section:  "3. Incident management" or "3.4 Response…"
    m = _NUMBERED_SECTION_RE.match(text)
    if m:
        num = m.group(1)
        title_part = text[m.end() :].strip().rstrip(".")
        if not title_part:
            title_part = f"Section {num}"
        return num, title_part

    # Front-matter headings.
    if text in _FRONT_MATTER_TITLES:
        return f"front-{text.lower().replace(' ', '-')}", text

    # Everything else is a callout / emphasis heading — not a section.
    return None


def _is_noise_line(line: str) -> bool:
    """True if the line is page furniture that should be stripped entirely."""
    stripped = line.strip()
    if not stripped:
        return False
    if _PAGE_HEADER_RE.match(stripped):
        return True
    if _PAGE_NUMBER_RE.match(stripped):
        return True
    if _EDITION_RE.match(stripped):
        return True
    if _LOGO_LINE_RE.match(stripped):
        return True
    return False


def _extract_page_number(line: str) -> int | None:
    """If the line is a page-number marker, return the page number."""
    stripped = line.strip()
    m = re.search(r"\*{0,2}(\d{1,3})\*{0,2}\s+of\s+\d{1,3}", stripped)
    if m:
        return int(m.group(1))
    return None


def _parent_section_number(section_number: str) -> str | None:
    """Derive the parent section number, e.g. '3.4' → '3', 'B.1' → 'B'."""
    if "." in section_number:
        return section_number.rsplit(".", 1)[0]
    return None


def _find_top_level_tables(text: str) -> list[tuple[int, int]]:
    """Return (start, end) byte spans for every top-level ``<table>`` in text.

    The naive ``<table>.*?</table>`` regex fails when tables are nested because
    `.*?` stops at the *first* ``</table>`` it encounters, which may close an
    inner table rather than the outer one.  This function uses a depth counter
    to find the correct closing tag.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    i = 0
    while i < len(text):
        if text[i : i + 7] == "<table>":
            if depth == 0:
                start = i
            depth += 1
            i += 7
        elif text[i : i + 8] == "</table>":
            depth -= 1
            if depth == 0 and start != -1:
                spans.append((start, i + 8))
                start = -1
            i += 8
        else:
            i += 1
    return spans


def _parse_blocks_from_body(body: str) -> list[TextBlock | TableBlock]:
    """Parse body into TextBlocks and TableBlocks.

    Works correctly for arbitrarily nested tables (e.g. the service catalogue
    in section 5.2 which has three levels of table nesting).
    """
    spans = _find_top_level_tables(body)
    if not spans:
        if body.strip():
            return [TextBlock(text=body.strip())]
        return []

    blocks: list[TextBlock | TableBlock] = []
    prev_end = 0
    for start, end in spans:
        text_before = body[prev_end:start].strip()
        if text_before:
            blocks.append(TextBlock(text=text_before))

        table_html = body[start:end]
        parser = _TableBlockExtractor()
        parser.feed(table_html)
        block = parser.get_table_block()
        if block:
            blocks.append(block)

        prev_end = end

    text_after = body[prev_end:].strip()
    if text_after:
        blocks.append(TextBlock(text=text_after))

    return blocks


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


@dataclass
class MarkdownParseReport:
    """Sanitized report of one parse run."""

    total_lines: int = 0
    total_sections: int = 0
    noise_lines_stripped: int = 0
    toc_lines_stripped: int = 0
    tables_converted: int = 0
    front_matter_sections: int = 0
    appendix_sections: int = 0
    empty_sections_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def parse_markdown_manual(
    md_path: Path,
) -> tuple[list[ManualSection], AppendixERelationships, MarkdownParseReport]:
    """Parse ``parsed-pdf.md`` into sections + relationships.

    This is the replacement for ``manual_parser.parse_manual`` that skips the
    entire PDF extraction pipeline and works directly from the Structured.io
    markdown.
    """
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown source not found at {md_path}")

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    report = MarkdownParseReport(total_lines=len(lines))

    # ---- Pass 1: strip noise, detect sections, track page numbers ---------
    raw_sections: list[_RawParsedSection] = []
    current: _RawParsedSection | None = None
    current_page: int = 1
    in_toc = False

    for line in lines:
        # Track page numbers from "**N** of M" markers (before noise check,
        # because the page number line itself IS noise — we just want the number).
        page = _extract_page_number(line)
        if page is not None:
            current_page = page

        # Strip page-furniture noise.
        if _is_noise_line(line):
            report.noise_lines_stripped += 1
            continue

        # Detect and skip TOC region.
        if line.strip().lower() == "# contents":
            in_toc = True
            report.toc_lines_stripped += 1
            continue
        if in_toc:
            if line.startswith("# ") and _classify_heading(line) is not None:
                in_toc = False  # TOC ends at next real section heading.
            else:
                if _TOC_ENTRY_RE.search(line):
                    report.toc_lines_stripped += 1
                    continue
                # Blank lines inside TOC region.
                if not line.strip():
                    report.toc_lines_stripped += 1
                    continue
                # Non-TOC content signals end of TOC region.
                if line.startswith("# "):
                    in_toc = False
                else:
                    report.toc_lines_stripped += 1
                    continue

        # Try to classify as a section heading.
        heading = _classify_heading(line)
        if heading is not None:
            section_number, title = heading
            parent = _parent_section_number(section_number)
            current = _RawParsedSection(
                section_number=section_number,
                title=title,
                parent_section=parent,
            )
            current.pages.append(current_page)
            raw_sections.append(current)
            continue

        # Accumulate body into the current section.
        if current is not None:
            # Track pages spanned.
            if current_page not in current.pages:
                current.pages.append(current_page)
            # Track whether body contains HTML tables.
            if "<table>" in line:
                current.has_tables = True
            current.body_lines.append(line)

    # ---- Pass 2: build ManualSection objects ------------------------------
    sections: list[ManualSection] = []
    appendix_e_body: str | None = None
    front_group: list[_RawParsedSection] = []

    def _flush_front_group() -> None:
        """Emit consecutive front-matter sections (Document control, Version
        history, Ownership and review) as ONE retrievable pseudo-section.

        Dropping them left cover-page metadata — classification, owner,
        edition history — permanently unsearchable, and the Stage A eval
        references this block as "Document control".
        """
        parts = []
        for raw in front_group:
            raw_body = "\n".join(raw.body_lines).strip()
            if raw_body:
                parts.append(f"{raw.title}\n\n{raw_body}")
        combined = "\n\n".join(parts)
        if not combined:
            return
        table_count = sum(
            len(re.findall(r"<table>", "\n".join(raw.body_lines))) for raw in front_group
        )
        if table_count:
            report.tables_converted += table_count
        blocks = _parse_blocks_from_body(combined)
        if not blocks:
            return
        try:
            sections.append(
                ManualSection(
                    section_id=ManualSection.build_section_id("Document control"),
                    section_number="Document control",
                    title="Document control",
                    blocks=blocks,
                    content_type=ManualSectionType.TABLE
                    if any(raw.has_tables for raw in front_group)
                    else ManualSectionType.PROSE,
                    pages=tuple(sorted({p for raw in front_group for p in raw.pages})),
                )
            )
        except Exception as exc:
            report.warnings.append(f"front matter: validation failed: {exc}")

    for raw in raw_sections:
        body = "\n".join(raw.body_lines).strip()

        # Collect front-matter sections (document control, version history);
        # flush them as one pseudo-section before the first numbered section.
        if raw.section_number.startswith("front-"):
            report.front_matter_sections += 1
            front_group.append(raw)
            continue
        if front_group:
            _flush_front_group()
            front_group = []

        # Capture Appendix E body BEFORE block parsing — relationship
        # extraction needs the raw HTML table structure.
        if raw.section_number.upper() == "E":
            appendix_e_body = body

        # Convert HTML tables to TableBlocks.
        table_count = len(re.findall(r"<table>", body))
        if table_count:
            report.tables_converted += table_count

        blocks = _parse_blocks_from_body(body)

        if not blocks:
            logger.debug(
                "empty_section_skipped",
                section_number=raw.section_number,
                title=raw.title,
            )
            report.empty_sections_skipped += 1
            continue

        content_type = ManualSectionType.TABLE if raw.has_tables else ManualSectionType.PROSE

        if raw.section_number.isalpha() and len(raw.section_number) == 1:
            report.appendix_sections += 1

        pages = tuple(sorted(set(raw.pages)))

        try:
            section = ManualSection(
                section_id=ManualSection.build_section_id(raw.section_number),
                section_number=raw.section_number,
                title=raw.title,
                blocks=blocks,
                content_type=content_type,
                pages=pages,
            )
            sections.append(section)
        except Exception as exc:
            report.warnings.append(
                f"section {raw.section_number} ({raw.title!r}): validation failed: {exc}"
            )

    if front_group:
        _flush_front_group()

    report.total_sections = len(sections)

    # ---- Pass 3: extract Appendix E relationships -------------------------
    if appendix_e_body is not None:
        relationships = _parse_appendix_e_html(appendix_e_body)
    else:
        report.warnings.append(
            "Appendix E (identifier index) not found; relationship maps are empty"
        )
        relationships = AppendixERelationships()

    logger.info(
        "markdown_manual_parsed",
        sections=report.total_sections,
        noise_stripped=report.noise_lines_stripped,
        tables_converted=report.tables_converted,
        empty_skipped=report.empty_sections_skipped,
        warnings=len(report.warnings),
    )
    return sections, relationships, report


# ---------------------------------------------------------------------------
# Appendix E HTML-table relationship extraction
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"((?:KB|INC|PRB|KE|CHG|RITM)\d+(?:\s+v\d+)?|MIR-\d{4}-\d+)")
_SECTION_LIST_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2}){0,3})")


def _parse_appendix_e_html(body: str) -> AppendixERelationships:
    """Extract identifier → sections mappings from the Appendix E HTML tables."""
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = {}

    # Find all <table>…</table> blocks in the Appendix E body.
    tables = re.findall(r"<table>.*?</table>", body, re.DOTALL)
    for table_html in tables:
        rows = re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL)
        for row_html in rows:
            cells = re.findall(r"<t[dh](?:\s[^>]*)?>(.*?)</t[dh]>", row_html, re.DOTALL)
            if not cells:
                # Fallback without space-after-<
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL)
            if len(cells) < 3:
                continue

            # First cell: identifier, last cell: section list.
            id_cell = cells[0].strip()
            sections_cell = cells[-1].strip()

            # Extract the identifier.
            id_match = _IDENTIFIER_RE.match(id_cell)
            if not id_match:
                continue
            raw_id = id_match.group(1).replace(" ", "")
            # Also extract base identifier (e.g. KB0010 from KB0010v1 or KB0010 v1)
            base_id_match = re.match(r"^((?:KB|INC|PRB|KE|CHG|RITM)\d+|MIR-\d{4}-\d+)", raw_id)
            base_id = base_id_match.group(1) if base_id_match else raw_id

            # Extract section numbers.
            section_nums = _SECTION_LIST_RE.findall(sections_cell)
            if not section_nums:
                continue

            # Map both raw_id (e.g. KB0010v1) and base_id (e.g. KB0010)
            id_keys = [raw_id] if raw_id == base_id else [raw_id, base_id]
            for identifier in id_keys:
                forward.setdefault(identifier, [])
                for num in section_nums:
                    if num not in forward[identifier]:
                        forward[identifier].append(num)
                    reverse.setdefault(num, [])
                    if identifier not in reverse[num]:
                        reverse[num].append(identifier)

    return AppendixERelationships(forward=forward, reverse=reverse)
