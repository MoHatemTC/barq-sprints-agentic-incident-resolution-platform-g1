"""
This module is to produce ordinary Article objects
that satisfy the Article interface, from the raw data extracted from the OCR/Tables/Layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

from app.models.knowledge import SecurityLevel

# from app.retrieval.extraction.layout_extraction import (
#     detect_columns,
#     extract_text_blocks,
#     reconstruct_reading_order,
# )

logger = structlog.get_logger(__name__)

OCR_CONFIDENCE_FLOOR = 0.60
OCR_TEXT_LENGTH_FLOOR = 20
MIN_COLUMNS_FOR_LAYOUT_CLASS = 2


class StressorClass(StrEnum):
    OCR = "ocr"
    TABLE = "table"
    MULTI_COLUMN = "layout"


@dataclass
class StressorConfig:
    """Metadata needed to build one Article from an extracted stressor page."""

    article_number: str
    version: str
    title: str
    category: str
    service: str
    security_level: SecurityLevel
    owner: str | None = None
    author: str | None = None
    reviewed_on: str | None = None
    related_records: list[str] | None = None
    pages: tuple[int, ...] = ()
    stressor_class: StressorClass = StressorClass.OCR
