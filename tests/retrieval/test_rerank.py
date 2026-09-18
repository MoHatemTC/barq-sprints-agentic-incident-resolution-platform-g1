"""Unit tests for cross-encoder reranking (FR-14 scope item)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from app.retrieval.hybrid_search import RetrievalHit
from app.retrieval.rerank import CrossEncoderReranker, get_default_reranker


def _make_hit(
    article_id: str, chunk_index: int, score: float, text: str = "chunk text"
) -> RetrievalHit:
    return RetrievalHit(
        score=score,
        article_id=article_id,
        article_number=article_id.split("-v")[0],
        version="1.0",
        title="Title",
        section="Resolution",
        chunk_index=chunk_index,
        chunk_text=text,
        workflow_state="published",
        security_level="internal",
        category="software",
        service="app",
    )


class _FakeTextCrossEncoder:
    """Stand-in for fastembed's TextCrossEncoder. Records calls and returns
    caller-supplied scores in order."""

    instances: list[_FakeTextCrossEncoder] = []
    next_scores: list[float] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.rerank_calls: list[tuple[str, list[str]]] = []
        _FakeTextCrossEncoder.instances.append(self)

    def rerank(self, query: str, documents: list[str]):
        self.rerank_calls.append((query, documents))
        return _FakeTextCrossEncoder.next_scores


@pytest.fixture(autouse=True)
def _reset_fake_encoder():
    _FakeTextCrossEncoder.instances = []
    _FakeTextCrossEncoder.next_scores = []
    yield


@pytest.fixture(autouse=True)
def _fake_fastembed_module(monkeypatch: pytest.MonkeyPatch):
    """Inject a fake `fastembed.rerank.cross_encoder` module so `_load()`'s
    local import succeeds without requiring the real model download."""
    fake_pkg = types.ModuleType("fastembed")
    fake_rerank_pkg = types.ModuleType("fastembed.rerank")
    fake_cross_encoder_mod = types.ModuleType("fastembed.rerank.cross_encoder")
    fake_cross_encoder_mod.TextCrossEncoder = _FakeTextCrossEncoder
    fake_pkg.rerank = fake_rerank_pkg
    fake_rerank_pkg.cross_encoder = fake_cross_encoder_mod

    monkeypatch.setitem(sys.modules, "fastembed", fake_pkg)
    monkeypatch.setitem(sys.modules, "fastembed.rerank", fake_rerank_pkg)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake_cross_encoder_mod)
    yield


@pytest.fixture(autouse=True)
def _clear_default_reranker_cache():
    get_default_reranker.cache_clear()
    yield
    get_default_reranker.cache_clear()


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch: pytest.MonkeyPatch):
    settings = MagicMock()
    settings.rerank_model = "test/cross-encoder-model"
    monkeypatch.setattr("app.retrieval.rerank.get_retrieval_settings", lambda: settings)
    yield settings


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------


def test_empty_hits_returns_empty_list_without_loading_model():
    reranker = CrossEncoderReranker()
    result = reranker.rerank("query", [], top_n=5)
    assert result == []
    assert _FakeTextCrossEncoder.instances == []  # model never loaded


def test_top_n_zero_raises():
    reranker = CrossEncoderReranker()
    hits = [_make_hit("KB0001-v1.0", 0, 0.5)]
    with pytest.raises(ValueError, match="top_n must be a positive integer"):
        reranker.rerank("query", hits, top_n=0)


def test_top_n_negative_raises():
    reranker = CrossEncoderReranker()
    hits = [_make_hit("KB0001-v1.0", 0, 0.5)]
    with pytest.raises(ValueError, match="top_n must be a positive integer"):
        reranker.rerank("query", hits, top_n=-1)


def test_rerank_normalizes_scores_and_sorts_descending():
    hits = [
        _make_hit("KB0001-v1.0", 0, 0.9, "irrelevant text"),
        _make_hit("KB0002-v1.0", 0, 0.1, "highly relevant text"),
    ]
    _FakeTextCrossEncoder.next_scores = [0.1, 0.9]  # cross-encoder disagrees with fused rank

    reranker = CrossEncoderReranker()
    result = reranker.rerank("query", hits, top_n=2)

    assert [h.article_id for h in result] == ["KB0002-v1.0", "KB0001-v1.0"]
    assert result[0].score == pytest.approx(0.7109495026)
    assert result[1].score == pytest.approx(0.5249791875)


def test_rerank_truncates_to_top_n():
    hits = [_make_hit(f"KB000{i}-v1.0", 0, 0.5) for i in range(5)]
    _FakeTextCrossEncoder.next_scores = [0.1, 0.9, 0.5, 0.3, 0.7]

    reranker = CrossEncoderReranker()
    result = reranker.rerank("query", hits, top_n=2)

    assert len(result) == 2
    assert result[0].score == pytest.approx(0.7109495026)
    assert result[1].score == pytest.approx(0.6681877722)


def test_rerank_normalizes_cross_encoder_logits():
    hits = [
        _make_hit("KB0001-v1.0", 0, 0.5),
        _make_hit("KB0002-v1.0", 0, 0.5),
    ]
    _FakeTextCrossEncoder.next_scores = [-10.03, 10.03]

    reranker = CrossEncoderReranker()
    result = reranker.rerank("query", hits, top_n=2)

    assert result[0].article_id == "KB0002-v1.0"
    assert result[0].score == pytest.approx(0.999956)

    assert result[1].article_id == "KB0001-v1.0"
    assert result[1].score == pytest.approx(0.000044, abs=1e-7)


def test_rerank_tie_break_uses_article_id_then_chunk_index():
    hits = [
        _make_hit("KB0002-v1.0", 1, 0.5),
        _make_hit("KB0001-v1.0", 0, 0.5),
        _make_hit("KB0001-v1.0", 2, 0.5),
    ]
    _FakeTextCrossEncoder.next_scores = [0.7, 0.7, 0.7]  # all tied

    reranker = CrossEncoderReranker()
    result = reranker.rerank("query", hits, top_n=3)

    assert [(h.article_id, h.chunk_index) for h in result] == [
        ("KB0001-v1.0", 0),
        ("KB0001-v1.0", 2),
        ("KB0002-v1.0", 1),
    ]


def test_rerank_passes_chunk_text_as_documents_in_order():
    hits = [
        _make_hit("KB0001-v1.0", 0, 0.5, "first chunk"),
        _make_hit("KB0002-v1.0", 0, 0.5, "second chunk"),
    ]
    _FakeTextCrossEncoder.next_scores = [0.5, 0.5]

    reranker = CrossEncoderReranker()
    reranker.rerank("my query", hits, top_n=2)

    encoder = _FakeTextCrossEncoder.instances[0]
    assert encoder.rerank_calls == [("my query", ["first chunk", "second chunk"])]


def test_mismatched_score_count_raises():
    """`zip(..., strict=True)` must fail loud if the encoder returns the
    wrong number of scores rather than silently misaligning hits and scores."""
    hits = [_make_hit("KB0001-v1.0", 0, 0.5), _make_hit("KB0002-v1.0", 0, 0.5)]
    _FakeTextCrossEncoder.next_scores = [0.9]  # only one score for two hits

    reranker = CrossEncoderReranker()
    with pytest.raises(ValueError):
        reranker.rerank("query", hits, top_n=2)


# ---------------------------------------------------------------------------
# Lazy loading & model configuration
# ---------------------------------------------------------------------------


def test_model_is_not_loaded_until_first_rerank_call():
    reranker = CrossEncoderReranker()
    assert reranker._model is None
    assert _FakeTextCrossEncoder.instances == []


def test_model_loaded_once_and_reused_across_calls():
    hits = [_make_hit("KB0001-v1.0", 0, 0.5)]
    _FakeTextCrossEncoder.next_scores = [0.5]

    reranker = CrossEncoderReranker()
    reranker.rerank("q1", hits, top_n=1)
    reranker.rerank("q2", hits, top_n=1)

    assert len(_FakeTextCrossEncoder.instances) == 1


def test_explicit_model_name_overrides_settings():
    reranker = CrossEncoderReranker(model_name="custom/model")
    assert reranker.model_name == "custom/model"

    hits = [_make_hit("KB0001-v1.0", 0, 0.5)]
    _FakeTextCrossEncoder.next_scores = [0.5]
    reranker.rerank("q", hits, top_n=1)

    assert _FakeTextCrossEncoder.instances[0].model_name == "custom/model"


def test_default_model_name_comes_from_settings(_mock_settings):
    reranker = CrossEncoderReranker()
    assert reranker.model_name == "test/cross-encoder-model"


# ---------------------------------------------------------------------------
# Process-wide cache
# ---------------------------------------------------------------------------


def test_get_default_reranker_returns_same_instance():
    first = get_default_reranker()
    second = get_default_reranker()
    assert first is second


def test_get_default_reranker_is_a_cross_encoder_reranker():
    reranker = get_default_reranker()
    assert isinstance(reranker, CrossEncoderReranker)
