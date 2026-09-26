"""
Table extraction module that reconstructs row/column structure from
pdfplumber's table geometry, propagates merged-cell values into every
cell they cover, and flattens the result to plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber


@dataclass
class Cell:
    """A single table cell, possibly spanning multiple rows/columns."""

    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1


@dataclass
class ExtractedTable:
    """One table extracted from a PDF page, as a grid of Cells."""

    page_number: int
    bbox: tuple[float, float, float, float]
    cells: list[list[Cell]]


@dataclass
class NormalizedRow:
    """A single row with all merged headers propagated into it."""

    cells: dict[str, str] = field(default_factory=dict)


_DUPLICATE_TABLE_OVERLAP_THRESHOLD = 0.75


def _clean(text: str | None) -> str:
    """Strip a cell down to single-spaced text: collapses pdfplumber's
    embedded line-wrap newlines and any run of whitespace to one space."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _bbox_overlap_ratio(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """Fraction of the smaller bbox's area that the two bboxes share."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    smaller = min(area_a, area_b)
    return intersection / smaller if smaller > 0 else 0.0


def _dedupe_overlapping_tables(raw_tables: list) -> list:
    """Collapse pdfplumber Table detections that cover the same region,
    keeping the most complete, least self-duplicating parse in each
    overlapping cluster, in top-to-bottom reading order."""
    sized: list[tuple[Any, list[list[str | None]], int]] = []
    for table in raw_tables:
        rows = table.extract() or []
        non_empty = sum(1 for row in rows for v in row if v and v.strip())
        if non_empty == 0:
            continue

        dupes = 0
        for row in rows:
            prev = None
            for value in row:
                text = (value or "").strip()
                if text and text == prev:
                    dupes += 1
                if text:
                    prev = text
        sized.append((table, rows, non_empty - 2 * dupes))

    sized.sort(key=lambda item: item[2], reverse=True)

    kept: list[tuple[Any, list[list[str | None]]]] = []
    for table, rows, _score in sized:
        if any(
            _bbox_overlap_ratio(table.bbox, k.bbox) >= _DUPLICATE_TABLE_OVERLAP_THRESHOLD
            for k, _ in kept
        ):
            continue
        kept.append((table, rows))

    kept.sort(key=lambda item: (item[0].bbox[1], item[0].bbox[0]))
    return kept


def extract_tables_from_pdf(pdf_path: Path, pages: list[int]) -> list[ExtractedTable]:
    """Extract tables (with merged-cell geometry) per page."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found at {pdf_path}")

    extracted: list[ExtractedTable] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in pages:
            if page_num < 1 or page_num > len(pdf.pages):
                continue
            page = pdf.pages[page_num - 1]
            for _table, raw_rows in _dedupe_overlapping_tables(page.find_tables()):
                if raw_rows:
                    extracted.append(
                        ExtractedTable(
                            page_number=page_num,
                            bbox=_table.bbox,
                            cells=_grid_from_raw_rows(raw_rows),
                        )
                    )
    return extracted


def _grid_from_raw_rows(raw_rows: list[list[str | None]]) -> list[list[Cell]]:
    """Turn pdfplumber's raw string grid into a Cell grid with spans computed."""
    n_rows = len(raw_rows)
    n_cols = max(len(r) for r in raw_rows)
    normalized = [
        [_clean(row[c]) if c < len(row) else "" for c in range(n_cols)] for row in raw_rows
    ]

    claimed = [[False] * n_cols for _ in range(n_rows)]
    grid: list[list[Cell]] = [[] for _ in range(n_rows)]

    for r in range(n_rows):
        for c in range(n_cols):
            if claimed[r][c]:
                continue
            text = normalized[r][c]
            if text == "":
                claimed[r][c] = True
                grid[r].append(Cell(text="", row=r, col=c))
                continue

            colspan = 1
            while (
                c + colspan < len(raw_rows[r])
                and normalized[r][c + colspan] == ""
                and not claimed[r][c + colspan]
            ):
                colspan += 1

            rowspan = 1
            while (
                r + rowspan < n_rows
                and all(
                    c + k < len(raw_rows[r + rowspan]) and normalized[r + rowspan][c + k] == ""
                    for k in range(colspan)
                )
                and not any(claimed[r + rowspan][c + k] for k in range(colspan))
            ):
                rowspan += 1

            for dr in range(rowspan):
                for dc in range(colspan):
                    claimed[r + dr][c + dc] = True
            grid[r].append(Cell(text=text, row=r, col=c, rowspan=rowspan, colspan=colspan))

    return grid


def propagate_merged_headers(table: list[list[Cell]]) -> list[NormalizedRow]:
    """Propagate every spanning cell's value into each row/col it covers."""
    if not table:
        return []

    n_rows = len(table)
    n_cols = max((cell.col + cell.colspan for row in table for cell in row), default=0)

    column_headers: dict[int, str] = {}
    header_rowspan_max = 0
    for cell in table[0]:
        for dc in range(cell.colspan):
            column_headers[cell.col + dc] = cell.text or f"column_{cell.col + dc}"
        header_rowspan_max = max(header_rowspan_max, cell.rowspan)

    value_grid: list[list[str]] = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for row in table:
        for cell in row:
            for dr in range(cell.rowspan):
                for dc in range(cell.colspan):
                    r, c = cell.row + dr, cell.col + dc
                    if r < n_rows and c < n_cols:
                        value_grid[r][c] = cell.text

    data_start = max(header_rowspan_max, 1)
    normalized_rows: list[NormalizedRow] = []
    for r in range(data_start, n_rows):
        if not table[r]:
            continue
        row_values = {column_headers.get(c, f"column_{c}"): value_grid[r][c] for c in range(n_cols)}
        if any(row_values.values()):
            normalized_rows.append(NormalizedRow(cells=row_values))

    return normalized_rows


def is_plausible_table(rows: list[NormalizedRow]) -> bool:
    """Check if a table is plausible based on its headers."""
    if not rows:
        return False
    headers = list(rows[0].cells.keys())
    if not headers:
        return False
    return any(not h.startswith("column_") for h in headers)


def _dedupe_adjacent(items: list[str]) -> list[str]:
    """Collapse consecutive identical values (from rowspan/colspan propagation)."""
    out: list[str] = []
    prev: str | None = None
    for item in items:
        if item and item != prev:
            out.append(item)
        prev = item
    return out


def table_to_flat_text(rows: list[NormalizedRow]) -> str:
    if not rows:
        return ""

    headers = list(rows[0].cells.keys())
    parts = [" ".join(_dedupe_adjacent(headers))]
    for row in rows:
        line = " ".join(_dedupe_adjacent([row.cells.get(h, "") for h in headers]))
        if line:
            parts.append(line)

    return " ".join(p for p in parts if p)
