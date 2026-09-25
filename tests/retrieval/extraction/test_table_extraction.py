from __future__ import annotations

from app.retrieval.extraction.table_extraction import (
    NormalizedRow,
    _dedupe_adjacent,
    _dedupe_overlapping_tables,
    _grid_from_raw_rows,
    is_plausible_table,
    propagate_merged_headers,
    table_to_flat_text,
)


class _FakeTable:
    """Duck-typed stand-in for a pdfplumber Table: has .bbox and .extract()."""

    def __init__(self, bbox, rows):
        self.bbox = bbox
        self._rows = rows

    def extract(self):
        return self._rows


class TestGridFromRawRows:
    def test_simple_grid_with_no_merges(self):
        raw_rows = [["Name", "Priority"], ["order-processing", "P1"]]
        grid = _grid_from_raw_rows(raw_rows)
        assert [c.text for c in grid[0]] == ["Name", "Priority"]
        assert [c.text for c in grid[1]] == ["order-processing", "P1"]
        assert all(c.rowspan == 1 and c.colspan == 1 for row in grid for c in row)

    def test_none_cells_treated_as_empty(self):
        # A blank cell immediately to the right of a non-blank one gets
        # absorbed into that cell's colspan (see the horizontal-merge
        # test below), so it never becomes a standalone Cell -- to
        # observe "None -> empty text" in isolation, use a cell whose own
        # position is blank (not a merge target of a neighbour).
        raw_rows = [[None, "B"], ["x", "y"]]
        grid = _grid_from_raw_rows(raw_rows)
        assert grid[0][0].text == ""
        assert grid[0][1].text == "B"

    def test_internal_newlines_and_whitespace_are_collapsed_to_one_space(self):
        # pdfplumber often returns a wrapped cell as "Line one\nLine two";
        # _clean() must flatten that to single-spaced text so it behaves
        # like any other cell downstream (e.g. in heading detection).
        raw_rows = [["Header"], ["Line one\nLine   two"]]
        grid = _grid_from_raw_rows(raw_rows)
        assert grid[1][0].text == "Line one Line two"

    def test_horizontal_merge_detected_via_blank_continuation_cells(self):
        raw_rows = [["COVERAGE", ""], ["Working hours", "08:00-18:00"]]
        grid = _grid_from_raw_rows(raw_rows)
        header_cell = grid[0][0]
        assert header_cell.text == "COVERAGE"
        assert header_cell.colspan == 2

    def test_vertical_merge_detected_via_blank_continuation_rows(self):
        raw_rows = [["Priority"], [""], ["P1"]]
        grid = _grid_from_raw_rows(raw_rows)
        assert grid[0][0].rowspan == 2
        assert grid[2][0].text == "P1"

    def test_ragged_rows_are_padded_with_empty_cells(self):
        raw_rows = [["a", "b", "c"], ["x"]]
        grid = _grid_from_raw_rows(raw_rows)
        assert len(grid[1]) == 3
        assert grid[1][1].text == ""
        assert grid[1][2].text == ""

    def test_repeated_identical_non_blank_text_is_not_treated_as_a_merge(self):
        # Two positions with the SAME literal text (rather than one real
        # value plus blank continuations) are two separate, unmerged
        # cells -- this is the mechanism behind garbled "COVERAGE |
        # COVERAGE" style rows when a PDF's raw extraction repeats a
        # spanning cell's text instead of leaving the span blank.
        raw_rows = [["COVERAGE", "COVERAGE"]]
        grid = _grid_from_raw_rows(raw_rows)
        assert len(grid[0]) == 2
        assert grid[0][0].colspan == 1
        assert grid[0][1].colspan == 1


class TestPropagateMergedHeaders:
    def test_single_header_row_propagates_to_every_data_row(self):
        raw_rows = [
            ["Service", "Priority"],
            ["order-processing", "P1"],
            ["identity", "P1"],
        ]
        rows = propagate_merged_headers(_grid_from_raw_rows(raw_rows))
        assert len(rows) == 2
        assert rows[0].cells == {"Service": "order-processing", "Priority": "P1"}
        assert rows[1].cells == {"Service": "identity", "Priority": "P1"}

    def test_vertically_spanning_header_skips_the_right_number_of_rows(self):
        raw_rows = [["Priority"], [""], ["P1"], ["P2"]]
        rows = propagate_merged_headers(_grid_from_raw_rows(raw_rows))
        assert [r.cells["Priority"] for r in rows] == ["P1", "P2"]

    def test_empty_table_returns_no_rows(self):
        assert propagate_merged_headers([]) == []

    def test_blank_spacer_row_between_data_rows_is_not_duplicated(self):
        # Regression test: a blank row between two real rows gets absorbed
        # into the row above via rowspan detection. A row that contributed
        # no cell of its own (table[r] is empty) must not be re-emitted as
        # a second, identical copy of the row above it.
        raw_rows = [
            ["Coverage", "Hours"],
            ["Working hours", "08:00-18:00"],
            ["", ""],
            ["Extended hours", "18:00-22:00"],
            ["", ""],
        ]
        rows = propagate_merged_headers(_grid_from_raw_rows(raw_rows))
        assert len(rows) == 2
        assert rows[0].cells == {"Coverage": "Working hours", "Hours": "08:00-18:00"}
        assert rows[1].cells == {"Coverage": "Extended hours", "Hours": "18:00-22:00"}


class TestIsPlausibleTable:
    def test_true_when_at_least_one_real_header(self):
        rows = [NormalizedRow(cells={"Service": "x", "column_1": "y"})]
        assert is_plausible_table(rows) is True

    def test_false_when_all_headers_are_generic_fallback(self):
        rows = [NormalizedRow(cells={"column_0": "x", "column_1": "y"})]
        assert is_plausible_table(rows) is False

    def test_false_for_empty_rows(self):
        assert is_plausible_table([]) is False

    def test_false_when_the_single_row_has_no_cells_at_all(self):
        rows = [NormalizedRow(cells={})]
        assert is_plausible_table(rows) is False


class TestDedupeAdjacent:
    def test_collapses_consecutive_identical_values(self):
        assert _dedupe_adjacent(["A", "A", "A", "B"]) == ["A", "B"]

    def test_non_adjacent_duplicates_are_both_kept(self):
        assert _dedupe_adjacent(["A", "B", "A"]) == ["A", "B", "A"]

    def test_empty_strings_are_dropped(self):
        assert _dedupe_adjacent(["A", "", "B"]) == ["A", "B"]

    def test_empty_list_returns_empty_list(self):
        assert _dedupe_adjacent([]) == []


class TestTableToFlatText:
    def test_empty_rows_returns_empty_string(self):
        assert table_to_flat_text([]) == ""

    def test_renders_headers_then_each_row_as_space_joined_text(self):
        rows = [NormalizedRow(cells={"Service": "order-processing", "Priority": "P1"})]
        text = table_to_flat_text(rows)
        assert "Service" in text and "Priority" in text
        assert "order-processing" in text and "P1" in text

    def test_dedupes_adjacent_repeated_values_from_span_propagation(self):
        # Reproduces the real-world artifact: a spanning header cell
        # propagated across several generic columns produces the same
        # value repeated adjacently, which should collapse to one.
        rows = [
            NormalizedRow(
                cells={
                    "column_0": "Working hours",
                    "column_1": "Working hours",
                    "column_2": "08:00-18:00",
                    "column_3": "08:00-18:00",
                    "column_4": "08:00-18:00",
                }
            )
        ]
        text = table_to_flat_text(rows)
        assert text.count("Working hours") == 1
        assert text.count("08:00-18:00") == 1


class TestDedupeOverlappingTables:
    def test_keeps_higher_scoring_table_when_bboxes_fully_overlap(self):
        good = _FakeTable((0, 0, 100, 100), [["A", "B"], ["1", "2"]])
        bad = _FakeTable((0, 0, 100, 100), [["A", "A"], ["A", "A"]])
        kept = _dedupe_overlapping_tables([bad, good])
        assert len(kept) == 1
        assert kept[0][0] is good

    def test_non_overlapping_tables_are_both_kept(self):
        a = _FakeTable((0, 0, 50, 50), [["A", "B"], ["1", "2"]])
        b = _FakeTable((200, 200, 250, 250), [["C", "D"], ["3", "4"]])
        kept = _dedupe_overlapping_tables([a, b])
        assert len(kept) == 2

    def test_fully_empty_detection_is_dropped(self):
        empty = _FakeTable((0, 0, 10, 10), [["", None], ["", ""]])
        real = _FakeTable((200, 200, 300, 300), [["A", "B"], ["1", "2"]])
        kept = _dedupe_overlapping_tables([empty, real])
        assert len(kept) == 1
        assert kept[0][0] is real

    def test_result_is_sorted_in_top_to_bottom_reading_order(self):
        lower = _FakeTable((0, 300, 100, 400), [["A", "B"], ["1", "2"]])
        upper = _FakeTable((0, 0, 100, 100), [["C", "D"], ["3", "4"]])
        kept = _dedupe_overlapping_tables([lower, upper])
        assert [t for t, _ in kept] == [upper, lower]
