from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

# A horizontal gap between word clusters wider than this (in points) is
# treated as a column boundary rather than ordinary word/sentence spacing.
COLUMN_GAP_THRESHOLD = 20.0

# Words within this vertical distance (points) are clustered onto the same
# visual line.
LINE_Y_TOLERANCE = 3.0

# A block narrower than this fraction of the page width, and offset toward
# an edge, is treated as a candidate callout rather than a main column.
CALLOUT_WIDTH_RATIO = 0.35
CALLOUT_EDGE_RATIO = 0.10


@dataclass
class TextBlock:
    """A cluster of words sharing a visual line, with its bounding box."""

    text: str
    x0: float  # left edge
    y0: float  # top edge
    x1: float  # right edge
    y1: float  # bottom edge


def extract_text_blocks(pdf_path: Path, page_num: int) -> list[TextBlock]:
    """Extract word-level boxes from one page and cluster them into lines.

    Words are clustered by vertical position first (same visual line), then
    concatenated left-to-right. This is what lets `detect_columns` reason
    about whole lines rather than individual words.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found at {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        if page_num < 1 or page_num > len(pdf.pages):
            return []
        page = pdf.pages[page_num - 1]
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)

    if not words:
        return []

    words.sort(key=lambda w: (round(w["top"] / LINE_Y_TOLERANCE), w["x0"]))

    lines: list[list[dict]] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][-1]["top"]) <= LINE_Y_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])

    blocks: list[TextBlock] = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line)
        blocks.append(
            TextBlock(
                text=text,
                x0=min(w["x0"] for w in line),
                y0=min(w["top"] for w in line),
                x1=max(w["x1"] for w in line),
                y1=max(w["bottom"] for w in line),
            )
        )
    return blocks


def detect_columns(blocks: list[TextBlock], page_width: float) -> list[list[TextBlock]]:
    """
    Group text blocks into columns by their horizontal position.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b.x0)
    columns: list[list[TextBlock]] = [[sorted_blocks[0]]]
    for block in sorted_blocks[1:]:
        current_column = columns[-1]
        rightmost = max(b.x1 for b in current_column)
        if block.x0 - rightmost > COLUMN_GAP_THRESHOLD:
            columns.append([block])
        else:
            current_column.append(block)

    return [sorted(column, key=lambda b: b.y0) for column in columns]


def reconstruct_reading_order(columns: list[list[TextBlock]]) -> str:
    """
    Read each column top-to-bottom, left column first.
    """
    columns_sorted = sorted(columns, key=lambda col: min(b.x0 for b in col) if col else 0)
    lines: list[str] = []
    for column in columns_sorted:
        for block in column:
            lines.append(block.text)
        lines.append("")  # blank line between columns for readability
    return "\n".join(lines).strip()


def detect_callout_boxes(
    blocks: list[TextBlock], page_width: float
) -> tuple[list[TextBlock], list[TextBlock]]:
    """
    Split blocks into (main_content, callouts) by width and edge offset.
    """
    main_content: list[TextBlock] = []
    callouts: list[TextBlock] = []
    callout_max_width = page_width * CALLOUT_WIDTH_RATIO
    edge_margin = page_width * CALLOUT_EDGE_RATIO

    for block in blocks:
        width = block.x1 - block.x0
        near_edge = block.x0 < edge_margin or (page_width - block.x1) < edge_margin
        if width <= callout_max_width and near_edge:
            callouts.append(block)
        else:
            main_content.append(block)

    return main_content, callouts
