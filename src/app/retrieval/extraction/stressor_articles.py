"""
This module is to produce ordinary Article objects
that satisfy the Article interface, from the raw data extracted from the OCR/Tables/Layouts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pdfplumber
import structlog

from app.models.knowledge import Article, WorkflowState
from app.models.stressor import StressorClass, StressorConfig
from app.retrieval.extraction.layout_extraction import (
    detect_columns,
    extract_text_blocks,
    reconstruct_reading_order,
)
from app.retrieval.extraction.ocr_extraction import extract_ocr_text
from app.retrieval.extraction.stressor_registry import STRESSOR_REGISTRY
from app.retrieval.extraction.table_extraction import (
    extract_tables_from_pdf,
    propagate_merged_headers,
    table_to_kv_text,
)

logger = structlog.get_logger(__name__)

OCR_CONFIDENCE_FLOOR = 0.60
OCR_TEXT_LENGTH_FLOOR = 20
MIN_COLUMNS_FOR_LAYOUT_CLASS = 2


def _pdftotext_yield(pdf_path: Path, page_num: int) -> str:
    """Check if the page already contains extractable text."""
    result = subprocess.run(
        [
            "pdftotext",
            "-layout",
            "-f",
            str(page_num),
            "-l",
            str(page_num),
            str(pdf_path),
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def classify_page(pdf_path: Path, page_num: int) -> StressorClass:
    """
    Auto-detect which stressor class a page belongs to.
    """
    text_yield = _pdftotext_yield(pdf_path, page_num)
    if len(text_yield) < OCR_TEXT_LENGTH_FLOOR:
        return StressorClass.OCR

    tables = extract_tables_from_pdf(pdf_path, [page_num])
    if tables:
        return StressorClass.TABLE

    with pdfplumber.open(pdf_path) as pdf:
        page_width = pdf.pages[page_num - 1].width
    blocks = extract_text_blocks(pdf_path, page_num)
    columns = detect_columns(blocks, page_width)
    if len([c for c in columns if c]) >= MIN_COLUMNS_FOR_LAYOUT_CLASS:
        return StressorClass.MULTI_COLUMN

    return StressorClass.OCR


def extract_stressor_page(
    pdf_path: Path, page_num: int, stressor_class: StressorClass
) -> tuple[str, float | None]:
    """Route to the appropriate extractor. Returns (text, ocr_confidence).

    ocr_confidence is None for non-OCR classes; it is only ever used for
    logging in build_stressor_articles(), never written to the Article or
    Qdrant payload.
    """
    if stressor_class == StressorClass.OCR:
        results = extract_ocr_text(pdf_path, [page_num])
        if not results:
            return "", 0.0
        page_result = results[0]
        return page_result.text, page_result.mean_confidence

    if stressor_class == StressorClass.TABLE:
        tables = extract_tables_from_pdf(pdf_path, [page_num])
        parts = []
        for table in tables:
            rows = propagate_merged_headers(table.cells)
            kv_text = table_to_kv_text(rows)
            if kv_text:
                parts.append(kv_text)
        return "\n\n".join(parts), None

    if stressor_class == StressorClass.MULTI_COLUMN:
        with pdfplumber.open(pdf_path) as pdf:
            page_width = pdf.pages[page_num - 1].width
        blocks = extract_text_blocks(pdf_path, page_num)
        columns = detect_columns(blocks, page_width)
        return reconstruct_reading_order(columns), None

    raise ValueError(f"Unknown stressor class: {stressor_class}")


def _build_body(config: StressorConfig, extracted_text: str) -> str:
    """
    Wrap raw extracted text into the canonical Markdown sections
    """
    lines = [
        "## Symptom",
        "",
        f"Reference material extracted from a {config.stressor_class.value} "
        "source page of the BARQ Operations Manual.",
        "",
        "## Cause",
        "",
        "Not applicable — this article reproduces reference content rather than a diagnosed fault.",
        "",
        "## Resolution",
        "",
        extracted_text.strip(),
        "",
        "## Escalation",
        "",
        f"Escalate per the standing route for {config.service}.",
    ]
    return "\n".join(lines)


def build_stressor_articles(
    pdf_path: Path,
    registry: dict[str, StressorConfig] | None = None,
) -> list[Article]:
    """Main entry point: extract every registered stressor page and return
    Article objects ready for ingest_articles().
    """
    registry = registry if registry is not None else STRESSOR_REGISTRY
    articles: list[Article] = []

    for stressor_id, config in registry.items():
        page_texts: list[str] = []
        confidences: list[float] = []

        for page_num in config.pages:
            text, confidence = extract_stressor_page(pdf_path, page_num, config.stressor_class)
            if not text.strip():
                logger.warning(
                    "stressor_page_empty",
                    stressor_id=stressor_id,
                    page=page_num,
                    stressor_class=config.stressor_class.value,
                )
                continue
            page_texts.append(text)
            if confidence is not None:
                confidences.append(confidence)

        if not page_texts:
            logger.warning("stressor_article_skipped_no_content", stressor_id=stressor_id)
            continue

        if confidences:
            mean_conf = sum(confidences) / len(confidences)
            if mean_conf < OCR_CONFIDENCE_FLOOR:
                logger.warning(
                    "stressor_ocr_low_confidence",
                    stressor_id=stressor_id,
                    article_number=config.article_number,
                    mean_confidence=round(mean_conf, 3),
                )

        body = _build_body(config, "\n\n".join(page_texts))
        short_description = f"{config.title} (auto-extracted, {config.stressor_class.value})"
        if len(short_description) > 255:
            short_description = short_description[:252] + "..."

        article = Article(
            article_number=config.article_number,
            version=config.version,
            title=config.title,
            body=body,
            short_description=short_description,
            category=config.category,
            service=config.service,
            workflow_state=WorkflowState.PUBLISHED,
            security_level=config.security_level,
            owner=config.owner,
            author=config.author,
            reviewed_on=config.reviewed_on,
            related_records=config.related_records or [],
        )
        articles.append(article)
        logger.info(
            "stressor_article_built",
            stressor_id=stressor_id,
            article_number=config.article_number,
            stressor_class=config.stressor_class.value,
            pages=list(config.pages),
        )

    return articles
