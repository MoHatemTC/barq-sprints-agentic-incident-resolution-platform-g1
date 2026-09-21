"""
Table extraction module that reconstructs
row/column structure from pdfplumber's table geometry, then propagates a
spanning cell's value into every row it covers, so each output row carries
its full header context independent of its neighbours
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
    cells: list[list[Cell]]


@dataclass
class NormalizedRow:
    """A single row with all merged headers propagated into it.
    Here is where we move from PDF structure to semantic data"""

    cells: dict[str, str] = field(default_factory=dict)


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
            for table in page.find_tables():
                raw_rows = table.extract()
                if not raw_rows:
                    continue
                cells = _grid_from_raw_rows(raw_rows)
                extracted.append(ExtractedTable(page_number=page_num, cells=cells))
    return extracted


def _grid_from_raw_rows(raw_rows: list[list[str | None]]) -> list[list[Cell]]:
    """Turn pdfplumber's raw string grid into a Cell grid with spans computed."""
    n_rows = len(raw_rows)
    n_cols = max(len(r) for r in raw_rows)
    normalized = [
        [(row[c] or "").strip() if c < len(row) else "" for c in range(n_cols)] for row in raw_rows
    ]

    # Positions already claimed by an earlier cell's span
    claimed = [[False] * n_cols for _ in range(n_rows)]
    grid: list[list[Cell]] = [[] for _ in range(n_rows)]

    for r in range(n_rows):
        for c in range(n_cols):
            if claimed[r][c]:
                continue
            text = normalized[r][c]
            if text == "":
                # Nothing above or to the left to inherit from
                claimed[r][c] = True
                grid[r].append(Cell(text="", row=r, col=c))
                continue

            colspan = 1
            while (
                c + colspan < n_cols
                and normalized[r][c + colspan] == ""
                and not claimed[r][c + colspan]
            ):
                colspan += 1

            rowspan = 1
            while (
                r + rowspan < n_rows
                and all(normalized[r + rowspan][c + k] == "" for k in range(colspan))
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

    # Dense value grid: every cell's span is expanded into every position it
    # covers, so propagation becomes a lookup instead of a search.
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
        row_values = {column_headers.get(c, f"column_{c}"): value_grid[r][c] for c in range(n_cols)}
        if any(v for v in row_values.values()):
            normalized_rows.append(NormalizedRow(cells=row_values))

    return normalized_rows


def table_to_markdown(rows: list[NormalizedRow]) -> str:
    """Render normalized rows as a single Markdown table."""
    if not rows:
        return ""
    headers = list(rows[0].cells.keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row.cells.get(h, "") for h in headers) + " |")
    return "\n".join(lines)


def table_to_kv_text(rows: list[NormalizedRow]) -> str:
    """Render each row as a self-contained key-value line."""
    lines = []
    for row in rows:
        line = " | ".join(f"{key}: {value}" for key, value in row.cells.items() if value)
        if line:
            lines.append(line)
    return "\n".join(lines)
