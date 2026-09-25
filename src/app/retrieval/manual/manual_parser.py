from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import structlog

from app.models.manual_section import ManualSection, ManualSectionType
from app.retrieval.barq_manual import (
    check_pdftotext_installed,
    extract_raw_text_from_pdf,
    strip_page_headers_and_footers,
)
from app.retrieval.extraction.layout_extraction import (
    detect_columns,
    extract_text_blocks,
    reconstruct_reading_order,
)
from app.retrieval.extraction.ocr_extraction import (
    extract_ocr_text,
    find_page_image_bboxes,
    ocr_image_regions,
)
from app.retrieval.extraction.parse_appendix import (
    AppendixERelationships,
    find_appendix_e,
    parse_appendix_e_body,
)
from app.retrieval.extraction.section_detector import (
    RawSection,
    build_page_stream,
    split_into_sections,
)
from app.retrieval.extraction.table_extraction import (
    ExtractedTable,
    extract_tables_from_pdf,
    is_plausible_table,
    propagate_merged_headers,
    table_to_flat_text,
)

logger = structlog.get_logger(__name__)

# Below this character count, a page's (header/footer-stripped) pdftotext
# yield is treated as no usable text layer and it is routed to whole-page OCR.
OCR_TEXT_LENGTH_FLOOR = 20

# >=2 clustered columns means the page needs reading-order reconstruction
# rather than a left-to-right read of pdftotext's column-mangled output.
MIN_COLUMNS_FOR_LAYOUT_CLASS = 2

# Embedded images smaller than this (in pt^2) are treated as decorative
# (logos, bullets, rules) rather than content worth OCR'ing.
MIN_EMBEDDED_IMAGE_AREA = 2000.0

OCR_RELIABILITY_FLOOR = 0.60

# Lower than OCR_RELIABILITY_FLOOR: embedded photos (a whiteboard, a phone
# screenshot) are inherently noisier than a scanned text page, so a
# fragment below this is still logged rather than treated as trustworthy.
IMAGE_OCR_CONFIDENCE_FLOOR = 0.40

_ALIGNMENT_WHITESPACE_RE = re.compile(r"[ \t]{2,}")


@dataclass
class PageInfo:
    """What one page turned out to be, and the text already extracted for it."""

    page_number: int
    content_type: ManualSectionType
    text: str
    ocr_confidence: float | None = None


@dataclass
class ParseReport:
    """Sanitized summary of one parse run, for the seed script's --dry-run output."""

    total_pages: int = 0
    total_sections: int = 0
    ocr_pages: list[int] = field(default_factory=list)
    image_ocr_pages: list[int] = field(default_factory=list)
    table_pages: list[int] = field(default_factory=list)
    layout_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _page_count(pdf_path: Path) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def _page_width(pdf_path: Path, page_number: int) -> float:
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page_number - 1].width


def _pdftotext_all_pages(pdf_path: Path, total_pages: int) -> dict[int, str]:
    raw = extract_raw_text_from_pdf(pdf_path, start_page=1, end_page=total_pages)
    pages = raw.split("\f")
    if len(pages) == total_pages + 1 and pages[-1].strip() == "":
        pages = pages[:-1]
    if len(pages) != total_pages:
        raise ValueError(
            f"pdftotext produced {len(pages)} page(s) via form-feed splitting, "
            f"but pdfplumber reports {total_pages}; refusing to guess the alignment."
        )
    return {i + 1: strip_page_headers_and_footers(text) for i, text in enumerate(pages)}


def _collapse_alignment_whitespace(text: str) -> str:
    """Collapse pdftotext's column-alignment padding (runs of 2+ spaces/tabs)
    to a single space, one line at a time. Newlines are left alone so
    paragraph structure survives."""
    return "\n".join(_ALIGNMENT_WHITESPACE_RE.sub(" ", line).rstrip() for line in text.split("\n"))


def _render_tables(tables: list[ExtractedTable]) -> str:
    """Render every plausible table to flat text; implausible detections
    are dropped rather than rendered."""
    parts: list[str] = []
    for table in tables:
        rows = propagate_merged_headers(table.cells)
        if not is_plausible_table(rows):
            continue
        text = table_to_flat_text(rows)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _is_table_of_contents(page_text: str) -> bool:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not lines:
        return False
    dotted_entries = sum(1 for line in lines if re.search(r"\.{3,}\s*\d+\s*$", line))
    return dotted_entries >= 5


def _run_whole_page_ocr(
    pdf_path: Path,
    total_pages: int,
    plain_by_page: dict[int, str],
    table_pages: set[int],
    layout_pages: set[int],
    report: ParseReport,
) -> dict[int, PageInfo]:
    """Whole-page OCR for pages pdftotext found essentially no text on at
    all -- a fully scanned page with no usable text layer."""
    candidates = sorted(
        p
        for p in range(1, total_pages + 1)
        if p not in table_pages
        and p not in layout_pages
        and len(plain_by_page[p].strip()) < OCR_TEXT_LENGTH_FLOOR
    )
    if not candidates:
        return {}

    report.ocr_pages.extend(candidates)
    results_by_page = {r.page_number: r for r in extract_ocr_text(pdf_path, candidates)}

    pages: dict[int, PageInfo] = {}
    for page_number in candidates:
        result = results_by_page.get(page_number)
        if result is None:
            report.warnings.append(
                f"page {page_number}: OCR requested but returned no result; using plain text"
            )
            pages[page_number] = PageInfo(
                page_number, ManualSectionType.OCR, plain_by_page[page_number], 0.0
            )
            continue

        if result.mean_confidence < OCR_RELIABILITY_FLOOR:
            report.warnings.append(
                f"page {page_number}: OCR confidence {result.mean_confidence:.2f} "
                f"below the {OCR_RELIABILITY_FLOOR} review threshold"
            )

        pages[page_number] = PageInfo(
            page_number,
            ManualSectionType.OCR,
            strip_page_headers_and_footers(result.text),
            result.mean_confidence,
        )
    return pages


def _find_plausible_tables(
    pdf_path: Path, page_numbers: list[int]
) -> dict[int, list[ExtractedTable]]:
    """Detect tables on the given pages and keep only the plausible ones
    per page (see is_plausible_table)."""
    if not page_numbers:
        return {}

    found_by_page: dict[int, list[ExtractedTable]] = {}
    for table in extract_tables_from_pdf(pdf_path, page_numbers):
        found_by_page.setdefault(table.page_number, []).append(table)

    plausible_by_page: dict[int, list[ExtractedTable]] = {}
    for page_number, found in found_by_page.items():
        plausible = [t for t in found if is_plausible_table(propagate_merged_headers(t.cells))]
        if plausible:
            plausible_by_page[page_number] = plausible
    return plausible_by_page


def _classify_remaining_pages(
    pdf_path: Path,
    remaining: list[int],
    plain_by_page: dict[int, str],
    plausible_tables_by_page: dict[int, list[ExtractedTable]],
    table_pages: set[int],
    layout_pages: set[int],
    report: ParseReport,
) -> dict[int, PageInfo]:
    """Classify each page as TABLE, LAYOUT or PROSE, in that priority order."""
    pages: dict[int, PageInfo] = {}

    for page_number in remaining:
        plausible_tables = plausible_tables_by_page.get(page_number, [])
        forced_table = page_number in table_pages
        forced_layout = page_number in layout_pages

        if forced_table or plausible_tables:
            report.table_pages.append(page_number)
            table_text = _render_tables(plausible_tables)
            prose = _collapse_alignment_whitespace(plain_by_page[page_number])

            rendered = table_text
            if not rendered:
                report.warnings.append(
                    f"page {page_number}: classified as table (forced) but no "
                    "plausible table was found; using plain text"
                )
                rendered = prose
            elif prose:
                rendered = f"{prose}\n\n{rendered}"

            pages[page_number] = PageInfo(page_number, ManualSectionType.TABLE, rendered)
            continue

        page_width = _page_width(pdf_path, page_number)
        blocks = extract_text_blocks(pdf_path, page_number)
        columns = detect_columns(blocks, page_width)
        if forced_layout or len([c for c in columns if c]) >= MIN_COLUMNS_FOR_LAYOUT_CLASS:
            report.layout_pages.append(page_number)
            rendered = reconstruct_reading_order(columns)
            if not rendered:
                report.warnings.append(
                    f"page {page_number}: classified as layout but reading-order "
                    "reconstruction was empty; using plain text"
                )
                rendered = plain_by_page[page_number]
            pages[page_number] = PageInfo(page_number, ManualSectionType.LAYOUT, rendered)
            continue

        pages[page_number] = PageInfo(
            page_number,
            ManualSectionType.PROSE,
            _collapse_alignment_whitespace(plain_by_page[page_number]),
        )

    return pages


def _overlay_embedded_image_ocr(
    pdf_path: Path, pages: dict[int, PageInfo], report: ParseReport
) -> None:
    """OCR embedded images and append the result to each page's text."""
    for page_number, page_info in list(pages.items()):
        if page_info.content_type == ManualSectionType.OCR:
            continue  # already OCR'd whole-page

        boxes = find_page_image_bboxes(pdf_path, page_number, MIN_EMBEDDED_IMAGE_AREA)
        if not boxes:
            continue

        image_results = ocr_image_regions(pdf_path, page_number, boxes)
        if not image_results:
            continue

        report.image_ocr_pages.append(page_number)
        low_confidence = [r for r in image_results if r.confidence < IMAGE_OCR_CONFIDENCE_FLOOR]
        if low_confidence:
            report.warnings.append(
                f"page {page_number}: {len(low_confidence)} embedded image(s) OCR'd "
                f"below the {IMAGE_OCR_CONFIDENCE_FLOOR} confidence floor; verify "
                "against the source PDF"
            )

        image_text = "\n\n".join(
            _collapse_alignment_whitespace(r.text) for r in image_results if r.text.strip()
        )
        if image_text:
            pages[page_number] = PageInfo(
                page_number,
                page_info.content_type,
                f"{page_info.text}\n\n[Image content]\n{image_text}",
                page_info.ocr_confidence,
            )


def classify_and_extract(
    pdf_path: Path,
    total_pages: int,
    table_pages: set[int],
    layout_pages: set[int],
    report: ParseReport,
) -> dict[int, PageInfo]:
    """
    Classify every page and extract its final text:
      1. Whole-page OCR for pages with no real text layer.
      2. Table detection on every remaining page.
      3. Classify each remaining page as TABLE, LAYOUT or PROSE.
      4. Overlay OCR for embedded images on any page not already OCR'd.
    """
    plain_by_page = _pdftotext_all_pages(pdf_path, total_pages)

    pages = _run_whole_page_ocr(
        pdf_path, total_pages, plain_by_page, table_pages, layout_pages, report
    )

    remaining = [p for p in range(1, total_pages + 1) if p not in pages]
    table_candidates = [p for p in remaining if p not in layout_pages]
    plausible_tables_by_page = _find_plausible_tables(pdf_path, table_candidates)

    pages.update(
        _classify_remaining_pages(
            pdf_path,
            remaining,
            plain_by_page,
            plausible_tables_by_page,
            table_pages,
            layout_pages,
            report,
        )
    )

    _overlay_embedded_image_ocr(pdf_path, pages, report)

    return pages


def _content_type_for(
    pages: dict[int, PageInfo], section_pages: tuple[int, ...]
) -> ManualSectionType:
    """A section's content type is the most specific extractor touching any of its pages."""
    touched = [pages[p].content_type for p in section_pages if p in pages]
    if ManualSectionType.TABLE in touched:
        return ManualSectionType.TABLE
    if ManualSectionType.OCR in touched:
        return ManualSectionType.OCR
    if ManualSectionType.LAYOUT in touched:
        return ManualSectionType.LAYOUT
    return ManualSectionType.PROSE


def build_manual_sections(
    raw_sections: list[RawSection], pages: dict[int, PageInfo]
) -> list[ManualSection]:
    """Turn detected raw sections into validated ManualSection models."""
    sections: list[ManualSection] = []

    for raw in raw_sections:
        content_type = _content_type_for(pages, raw.pages)

        ocr_confidence: float | None = None
        reliability_note: str | None = None
        if content_type == ManualSectionType.OCR:
            confidences: list[float] = []
            for p in raw.pages:
                if p in pages:
                    c = pages[p].ocr_confidence
                    if c is not None:
                        confidences.append(c)
            if confidences:
                ocr_confidence = sum(confidences) / len(confidences)
                if ocr_confidence < OCR_RELIABILITY_FLOOR:
                    reliability_note = (
                        f"OCR confidence {ocr_confidence:.2f} is below the "
                        f"{OCR_RELIABILITY_FLOOR} review threshold; verify this section's "
                        "text against the source PDF before trusting it downstream."
                    )

        if not raw.body.strip():
            logger.warning(
                "empty_manual_section",
                section_number=raw.section_number,
                title=raw.title,
                pages=raw.pages,
            )
            continue

        sections.append(
            ManualSection(
                section_id=ManualSection.build_section_id(raw.section_number),
                section_number=raw.section_number,
                title=raw.title,
                body=raw.body,
                content_type=content_type,
                pages=raw.pages,
                ocr_confidence=ocr_confidence,
                reliability_note=reliability_note,
            )
        )
    return sections


def parse_manual(
    pdf_path: Path,
    table_pages: set[int] | None = None,
    layout_pages: set[int] | None = None,
) -> tuple[list[ManualSection], AppendixERelationships, ParseReport]:
    """
    PDF -> classified/extracted pages -> sections -> Appendix E relationships.
    """
    check_pdftotext_installed()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found at {pdf_path}")

    report = ParseReport()
    total_pages = _page_count(pdf_path)
    report.total_pages = total_pages

    pages = classify_and_extract(
        pdf_path, total_pages, table_pages or set(), layout_pages or set(), report
    )
    toc_pages = {
        page_number
        for page_number, page_info in pages.items()
        if _is_table_of_contents(page_info.text)
    }

    ordered_texts = [pages[p].text for p in range(1, total_pages + 1)]
    stream = build_page_stream(ordered_texts)
    raw_sections = split_into_sections(stream, exclude_pages=toc_pages)
    sections = build_manual_sections(raw_sections, pages)
    report.total_sections = len(sections)

    appendix_e = find_appendix_e(raw_sections)
    if appendix_e is None:
        report.warnings.append(
            "Appendix E (identifier index) not detected; relationship maps are empty"
        )
        relationships = AppendixERelationships()
    else:
        relationships, appendix_report = parse_appendix_e_body(appendix_e.body)
        report.warnings.extend(appendix_report.warnings)

    logger.info(
        "manual_parsed",
        pages=report.total_pages,
        sections=report.total_sections,
        ocr_pages=len(report.ocr_pages),
        image_ocr_pages=len(report.image_ocr_pages),
        table_pages=len(report.table_pages),
        layout_pages=len(report.layout_pages),
        warnings=len(report.warnings),
    )
    return sections, relationships, report
