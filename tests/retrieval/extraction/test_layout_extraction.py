from __future__ import annotations

from app.retrieval.extraction.layout_extraction import (
    TextBlock,
    detect_callout_boxes,
    detect_columns,
    reconstruct_reading_order,
)


def _block(text, x0, y0, x1, y1):
    return TextBlock(text=text, x0=x0, y0=y0, x1=x1, y1=y1)


class TestDetectColumns:
    def test_empty_input_returns_empty_list(self):
        assert detect_columns([], page_width=600) == []

    def test_single_block_is_one_column(self):
        columns = detect_columns([_block("hello", 0, 0, 50, 10)], page_width=600)
        assert len(columns) == 1
        assert columns[0][0].text == "hello"

    def test_blocks_close_together_form_one_column(self):
        blocks = [
            _block("left top", 0, 0, 100, 10),
            _block("left bottom", 5, 20, 105, 30),
        ]
        columns = detect_columns(blocks, page_width=600)
        assert len(columns) == 1
        assert len(columns[0]) == 2

    def test_blocks_far_apart_form_two_columns(self):
        blocks = [
            _block("left column", 0, 0, 100, 10),
            _block("right column", 400, 0, 500, 10),
        ]
        columns = detect_columns(blocks, page_width=600)
        assert len(columns) == 2

    def test_columns_are_sorted_top_to_bottom_within_a_column(self):
        blocks = [
            _block("second line", 0, 20, 100, 30),
            _block("first line", 0, 0, 100, 10),
        ]
        columns = detect_columns(blocks, page_width=600)
        assert [b.text for b in columns[0]] == ["first line", "second line"]


class TestReconstructReadingOrder:
    def test_empty_columns_returns_empty_string(self):
        assert reconstruct_reading_order([]) == ""

    def test_reads_left_column_before_right_column(self):
        left = [
            _block("left first", 0, 0, 100, 10),
            _block("left second", 0, 20, 100, 30),
        ]
        right = [_block("right first", 400, 0, 500, 10)]
        # Pass right column first to confirm the function sorts by x0
        # itself rather than trusting caller order.
        text = reconstruct_reading_order([right, left])
        lines = [line for line in text.split("\n") if line]
        assert lines == ["left first", "left second", "right first"]

    def test_single_column_round_trips(self):
        col = [_block("only line", 0, 0, 100, 10)]
        assert reconstruct_reading_order([col]) == "only line"


class TestDetectCalloutBoxes:
    def test_narrow_edge_block_is_a_callout(self):
        blocks = [_block("Tip: read this", x0=2, y0=0, x1=50, y1=10)]
        main, callouts = detect_callout_boxes(blocks, page_width=600)
        assert main == []
        assert callouts == blocks

    def test_wide_central_block_is_main_content(self):
        blocks = [_block("Ordinary paragraph text", x0=100, y0=0, x1=500, y1=10)]
        main, callouts = detect_callout_boxes(blocks, page_width=600)
        assert main == blocks
        assert callouts == []

    def test_narrow_but_centered_block_is_main_content(self):
        # Narrow enough to qualify by width, but not near an edge.
        blocks = [_block("Narrow centered", x0=280, y0=0, x1=380, y1=10)]
        main, callouts = detect_callout_boxes(blocks, page_width=600)
        assert main == blocks
        assert callouts == []
