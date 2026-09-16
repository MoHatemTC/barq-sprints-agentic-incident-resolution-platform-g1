"""#70: the S1.1 classification labels and the S1.4 corpus categories must stay in step.

`field-model.md` lets S1.4 refine the AI Classification taxonomy, and S1.4 did — the
corpus calls identity incidents `inquiry`, which is not an S1.1 label. Nothing recorded
the refinement, so Sprint 2 filtering of retrieval by classification would have returned
nothing for every incident the classifier labelled `access`.

These tests make the mapping the single source of truth and fail when either side drifts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.knowledge import (
    CLASSIFICATIONS_WITHOUT_CORPUS_COVERAGE,
    CORPUS_CATEGORY_TO_CLASSIFICATION,
    Classification,
)

CORPUS_PATH = Path("data/corpus/barq_articles.json")


def _corpus_categories() -> set[str]:
    articles = json.loads(CORPUS_PATH.read_text())
    return {article["category"] for article in articles}


@pytest.mark.skipif(not CORPUS_PATH.exists(), reason="real corpus not found")
def test_every_corpus_category_maps_to_a_classification_label() -> None:
    """A category with no mapping is unreachable from a classifier's output."""
    unmapped = _corpus_categories() - set(CORPUS_CATEGORY_TO_CLASSIFICATION)
    assert not unmapped, (
        f"corpus categories with no classification mapping: {sorted(unmapped)}. "
        "Add them to CORPUS_CATEGORY_TO_CLASSIFICATION and document the choice in "
        "field-model.md and sprint1_corpus_design.md."
    )


def test_every_classification_label_is_mapped_or_declared_empty() -> None:
    """A label must either reach corpus content or be listed as knowingly uncovered."""
    covered = set(CORPUS_CATEGORY_TO_CLASSIFICATION.values())
    accounted = covered | CLASSIFICATIONS_WITHOUT_CORPUS_COVERAGE
    missing = set(Classification) - accounted
    assert not missing, (
        f"classification labels neither mapped nor declared uncovered: "
        f"{sorted(label.value for label in missing)}"
    )


def test_declared_empty_labels_really_have_no_corpus_category() -> None:
    """Stops the exemption list rotting once corpus content arrives for a label."""
    covered = set(CORPUS_CATEGORY_TO_CLASSIFICATION.values())
    stale = covered & CLASSIFICATIONS_WITHOUT_CORPUS_COVERAGE
    assert not stale, (
        f"these labels now have corpus coverage and must be removed from "
        f"CLASSIFICATIONS_WITHOUT_CORPUS_COVERAGE: "
        f"{sorted(label.value for label in stale)}"
    )


@pytest.mark.skipif(not CORPUS_PATH.exists(), reason="real corpus not found")
def test_mapping_has_no_entries_for_categories_the_corpus_never_uses() -> None:
    """A mapping entry for a category that does not exist is drift in the other direction."""
    unused = set(CORPUS_CATEGORY_TO_CLASSIFICATION) - _corpus_categories()
    assert not unused, f"mapping entries for categories absent from the corpus: {sorted(unused)}"
