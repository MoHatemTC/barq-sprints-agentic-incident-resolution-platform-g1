from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.knowledge import SecurityLevel


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
