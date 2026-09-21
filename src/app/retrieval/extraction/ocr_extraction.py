from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image

DEFAULT_LANG = "eng"
DEFAULT_DPI = 300

# Technical tokens worth protecting from OCR errors
PATH_RE = re.compile(r"(?:/[\w.\-]+)+")
COMMAND_RE = re.compile(
    r"\b(?:systemctl|nginx|journalctl|sudo|apt-get|ps|grep|tail|curl|ss|netstat)\b[^\n]*"
)
ERROR_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")


@dataclass
class OcrResult:
    """Result of running Tesseract on a single image."""

    text: str
    confidence: float  # mean word confidence, normalized to 0.0-1.0
    page_number: int
    word_confidences: list[float] = field(default_factory=list)


@dataclass
class OcrPageResult:
    """Post-processed OCR output for one PDF page, ready for downstream use."""

    page_number: int
    text: str
    mean_confidence: float


def check_tesseract_installed() -> None:
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "tesseract is not installed or not in PATH. "
            "Please install it (e.g. apt-get install tesseract-ocr)."
        )


def extract_images_from_pdf(
    pdf_path: Path, pages: list[int], dpi: int = DEFAULT_DPI
) -> list[tuple[int, Image.Image]]:
    """Convert specific 1-indexed PDF pages to PIL Images via pdf2image.
    Returns a list of tuples (page_number, image)."""

    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found at {pdf_path}")

    images: list[tuple[int, Image.Image]] = []
    for page_num in pages:
        rendered = convert_from_path(
            str(pdf_path), dpi=dpi, first_page=page_num, last_page=page_num
        )
        if rendered:
            images.append((page_num, rendered[0]))
    return images


def ocr_image(image: Image.Image, lang: str = DEFAULT_LANG, page_number: int = 0) -> OcrResult:
    """Run Tesseract OCR on a single PIL Image and return the result."""
    check_tesseract_installed()
    data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)

    words: list[str] = []
    confidences: list[float] = []
    for word, conf in zip(data["text"], data["conf"], strict=True):
        conf_value = float(conf)

        # Skip empty words with negative confidence
        if conf_value < 0 and not word.strip():
            continue

        words.append(word)
        confidences.append(conf_value)

    text = " ".join(words)
    mean_confidence = (sum(confidences) / len(confidences) / 100) if confidences else 0.0
    return OcrResult(
        text=text,
        confidence=mean_confidence,
        page_number=page_number,
        word_confidences=confidences,
    )


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character spans that character-level repair must not touch outside of."""
    spans: list[tuple[int, int]] = []
    for pattern in (PATH_RE, COMMAND_RE, ERROR_CODE_RE):
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    return spans


def post_process_technical_tokens(text: str) -> str:
    """Post-process OCR text to fix common errors in technical tokens."""
    if not text:
        return text

    spans = _protected_spans(text)
    if not spans:
        return text

    chars = list(text)
    for start, end in spans:
        segment = "".join(chars[start:end])

        # Replace 0 with O and vice versa, but only when surrounded by digits
        repaired = re.sub(r"(?<=\d)O(?=\d)", "0", segment)

        # Replace l with 1 when surrounded by digits or letters and digits
        repaired = re.sub(r"(?<=\d)l(?=\d)", "1", repaired)
        repaired = re.sub(r"(?<=[A-Za-z])l(?=\d)", "1", repaired)
        chars[start:end] = list(repaired)

    return "".join(chars)


def extract_ocr_text(
    pdf_path: Path, pages: list[int], lang: str = DEFAULT_LANG, dpi: int = DEFAULT_DPI
) -> list[OcrPageResult]:
    """Full OCR pipeline: PDF pages -> images -> OCR -> post-processing.

    Each result carries the page number, cleaned text, and mean confidence,
    so a caller can decide whether the page is trustworthy enough to ingest
    as-is or needs a human pass before it becomes part of the corpus.
    """
    check_tesseract_installed()
    images = extract_images_from_pdf(pdf_path, pages, dpi)

    results: list[OcrPageResult] = []
    for page_number, image in images:
        ocr_result = ocr_image(image, lang=lang, page_number=page_number)
        processed_text = post_process_technical_tokens(ocr_result.text)
        results.append(
            OcrPageResult(
                page_number=page_number,
                text=processed_text,
                mean_confidence=ocr_result.confidence,
            )
        )
    return results
