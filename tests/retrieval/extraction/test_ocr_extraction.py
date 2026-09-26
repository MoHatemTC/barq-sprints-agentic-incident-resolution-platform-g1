from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.retrieval.extraction.ocr_extraction import (
    check_tesseract_installed,
    find_page_image_bboxes,
    ocr_image,
    ocr_image_regions,
    post_process_technical_tokens,
)


class _FakePDF:
    """Minimal stand-in for a pdfplumber.PDF context manager."""

    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_data(words, confs, blocks, lines):
    return {"text": words, "conf": confs, "block_num": blocks, "line_num": lines}


class TestCheckTesseractInstalled:
    def test_raises_when_missing(self):
        with patch("app.retrieval.extraction.ocr_extraction.shutil.which", return_value=None):
            with pytest.raises(RuntimeError):
                check_tesseract_installed()

    def test_ok_when_present(self):
        with patch(
            "app.retrieval.extraction.ocr_extraction.shutil.which",
            return_value="/usr/bin/tesseract",
        ):
            check_tesseract_installed()  # should not raise


class TestOcrImage:
    def test_joins_same_line_words_with_a_single_space(self):
        fake_data = _fake_data(["Hello", "world"], [90, 85], [1, 1], [1, 1])
        with (
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
            patch(
                "app.retrieval.extraction.ocr_extraction.pytesseract.image_to_data",
                return_value=fake_data,
            ),
        ):
            result = ocr_image(image=object(), page_number=3)
        assert result.text == "Hello world"
        assert result.confidence == pytest.approx(0.875)
        assert result.page_number == 3

    def test_inserts_single_newline_between_lines_in_the_same_block(self):
        fake_data = _fake_data(["Hello", "world", "Foo"], [90, 85, 80], [1, 1, 1], [1, 1, 2])
        with (
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
            patch(
                "app.retrieval.extraction.ocr_extraction.pytesseract.image_to_data",
                return_value=fake_data,
            ),
        ):
            result = ocr_image(image=object())
        assert result.text == "Hello world\nFoo"

    def test_inserts_double_newline_between_blocks(self):
        fake_data = _fake_data(
            ["Hello", "world", "Foo", "Bar"],
            [90, 85, 80, 95],
            [1, 1, 1, 2],
            [1, 1, 2, 1],
        )
        with (
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
            patch(
                "app.retrieval.extraction.ocr_extraction.pytesseract.image_to_data",
                return_value=fake_data,
            ),
        ):
            result = ocr_image(image=object())
        assert result.text == "Hello world\nFoo\n\nBar"

    def test_skips_structural_entries_with_negative_confidence_and_blank_text(self):
        # Tesseract emits page/block/para/line-level rows with conf=-1 and
        # empty text alongside the real word rows; these must be dropped
        # entirely rather than treated as a word or counted in the mean.
        fake_data = _fake_data(
            ["", "Hello", "", "world", ""],
            [-1, 90, -1, 85, -1],
            [1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1],
        )
        with (
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
            patch(
                "app.retrieval.extraction.ocr_extraction.pytesseract.image_to_data",
                return_value=fake_data,
            ),
        ):
            result = ocr_image(image=object())
        assert result.text == "Hello world"
        assert result.confidence == pytest.approx(0.875)

    def test_no_words_gives_zero_confidence_and_empty_text(self):
        fake_data = _fake_data([], [], [], [])
        with (
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
            patch(
                "app.retrieval.extraction.ocr_extraction.pytesseract.image_to_data",
                return_value=fake_data,
            ),
        ):
            result = ocr_image(image=object())
        assert result.confidence == 0.0
        assert result.text == ""


class TestFindPageImageBboxes:
    def test_returns_boxes_at_or_above_min_area(self):
        page = SimpleNamespace(
            images=[
                {"x0": 0, "top": 0, "x1": 100, "bottom": 100},  # area 10000
                {"x0": 0, "top": 0, "x1": 5, "bottom": 5},  # area 25, tiny
            ]
        )
        with patch(
            "app.retrieval.extraction.ocr_extraction.pdfplumber.open",
            return_value=_FakePDF([page]),
        ):
            boxes = find_page_image_bboxes(Path("fake.pdf"), 1, min_area=2000.0)
        assert boxes == [(0, 0, 100, 100)]

    def test_returns_empty_list_when_no_images(self):
        page = SimpleNamespace(images=[])
        with patch(
            "app.retrieval.extraction.ocr_extraction.pdfplumber.open",
            return_value=_FakePDF([page]),
        ):
            boxes = find_page_image_bboxes(Path("fake.pdf"), 1)
        assert boxes == []


class TestOcrImageRegions:
    def test_ocrs_each_box_and_drops_blank_results(self):
        fake_page = SimpleNamespace(
            crop=lambda box: SimpleNamespace(
                to_image=lambda resolution: SimpleNamespace(original=object())
            )
        )
        nonblank = _fake_data(["Error"], [90], [1], [1])
        blank = _fake_data([""], [-1], [1], [1])

        with (
            patch(
                "app.retrieval.extraction.ocr_extraction.pdfplumber.open",
                return_value=_FakePDF([fake_page]),
            ),
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
            patch(
                "app.retrieval.extraction.ocr_extraction.pytesseract.image_to_data",
                side_effect=[nonblank, blank],
            ),
        ):
            results = ocr_image_regions(Path("fake.pdf"), 1, [(0, 0, 10, 10), (10, 10, 20, 20)])

        assert len(results) == 1
        assert results[0].text == "Error"

    def test_no_boxes_returns_no_results(self):
        fake_page = SimpleNamespace(crop=lambda box: None)
        with (
            patch(
                "app.retrieval.extraction.ocr_extraction.pdfplumber.open",
                return_value=_FakePDF([fake_page]),
            ),
            patch("app.retrieval.extraction.ocr_extraction.check_tesseract_installed"),
        ):
            results = ocr_image_regions(Path("fake.pdf"), 1, [])
        assert results == []


class TestPostProcessTechnicalTokens:
    def test_empty_string_is_returned_unchanged(self):
        assert post_process_technical_tokens("") == ""

    def test_text_without_technical_tokens_is_unchanged(self):
        text = "Please contact the service desk for help."
        assert post_process_technical_tokens(text) == text

    def test_repairs_O_to_zero_inside_a_path(self):
        text = "check /var/log/app1O2.log now"
        repaired = post_process_technical_tokens(text)
        assert "/var/log/app102.log" in repaired
        assert "now" in repaired

    def test_repairs_lowercase_l_to_one_inside_a_path(self):
        text = "check /var/log/serverl2.log now"
        repaired = post_process_technical_tokens(text)
        assert "/var/log/server12.log" in repaired
