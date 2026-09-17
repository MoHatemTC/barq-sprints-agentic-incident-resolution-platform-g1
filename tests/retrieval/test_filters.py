from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app.clients.qdrant import ensure_collection
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.embedding import EmbeddedText
from app.retrieval.filters import (
    DEFAULT_MAX_SECURITY_LEVEL,
    MetadataFilterBuilder,
    build_metadata_filter,
)
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.ingest import ingest_articles

DENSE_DIM = 384


class ConstantEmbeddingEngine:
    """Returns an identical embedding for every text, including the query.

    Used to prove that filtering -- not semantic distance -- is what keeps an
    excluded document out of the results: if two documents are vector-identical
    to the query, the only thing that can separate them is the metadata filter.
    """

    dense_vector_size = DENSE_DIM

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> EmbeddedText:
        return EmbeddedText(
            dense=[0.2] * DENSE_DIM, sparse_indices=[1, 2], sparse_values=[0.5, 0.5]
        )


def _make_article(**overrides) -> Article:
    defaults = dict(
        article_number="KB9001",
        version="1.0",
        title="Test article",
        short_description="Test article for filter validation",
        category="network",
        service="corporate-vpn",
        workflow_state=WorkflowState.PUBLISHED,
        security_level=SecurityLevel.INTERNAL,
        body="## Symptom\nConnection drops immediately after sign-in.\n\n"
        "## Resolution\nRestart the client.",
    )
    defaults.update(overrides)
    return Article(**defaults)


# ---------------------------------------------------------------------------
# Pure filter-construction unit tests
# ---------------------------------------------------------------------------


def test_default_builder_has_only_mandatory_conditions():
    filt = build_metadata_filter()
    assert filt.must is not None
    assert len(filt.must) == 2
    assert filt.must[0].key == "workflow_state"
    assert filt.must[1].key == "security_level"


def test_default_max_security_level_is_internal():
    assert DEFAULT_MAX_SECURITY_LEVEL == SecurityLevel.INTERNAL
    filt = build_metadata_filter(MetadataFilterBuilder())
    assert getattr(filt.must[1].match, "any", None) == ["public", "internal"]


@pytest.mark.parametrize(
    "level,expected",
    [
        (SecurityLevel.PUBLIC, ["public"]),
        (SecurityLevel.INTERNAL, ["public", "internal"]),
        (SecurityLevel.RESTRICTED, ["public", "internal", "restricted"]),
    ],
)
def test_security_level_is_cumulative(level, expected):
    filt = build_metadata_filter(MetadataFilterBuilder(max_security_level=level))
    assert getattr(filt.must[1].match, "any", None) == expected


def test_category_single_value_uses_match_value():
    filt = build_metadata_filter(MetadataFilterBuilder(category="network"))
    cond = filt.must[2]
    assert cond.key == "category"
    assert isinstance(cond.match, MatchValue)
    assert cond.match.value == "network"


def test_category_list_uses_match_any():
    filt = build_metadata_filter(MetadataFilterBuilder(category=["network", "software"]))
    cond = filt.must[2]
    assert isinstance(cond.match, MatchAny)
    assert cond.match.any == ["network", "software"]


def test_service_and_version_compose_with_category_in_declared_order():
    metadata = MetadataFilterBuilder(category="software", service="sap-erp", version="1.0")
    filt = build_metadata_filter(metadata)
    keys = [c.key for c in filt.must]
    assert keys == ["workflow_state", "security_level", "category", "service", "version"]


def test_empty_list_value_raises_for_category():
    with pytest.raises(ValueError, match="must not be empty"):
        build_metadata_filter(MetadataFilterBuilder(category=[]))


def test_empty_list_value_raises_for_service():
    with pytest.raises(ValueError, match="must not be empty"):
        build_metadata_filter(MetadataFilterBuilder(service=[]))


def test_workflow_state_override_allows_multiple_states():
    metadata = MetadataFilterBuilder(workflow_state=[WorkflowState.PUBLISHED, WorkflowState.DRAFT])
    filt = build_metadata_filter(metadata)
    assert getattr(filt.must[0].match, "any", None) == ["published", "draft"]


def test_explicit_empty_workflow_state_list_falls_back_to_default():
    """Documents CURRENT behavior: `workflow_state=[]` is falsy in Python, so
    `states = metadata.workflow_state or [WorkflowState.PUBLISHED]` silently
    substitutes the default instead of raising -- the `if not states: raise`
    guard a few lines later is unreachable given this pattern. If an explicit
    empty list should instead be a caller error, change the `or` to an
    `is None` check and this test's expectation."""
    metadata = MetadataFilterBuilder(workflow_state=[])
    filt = build_metadata_filter(metadata)
    assert getattr(filt.must[0].match, "any", None) == ["published"]


def test_extra_filter_is_appended_after_mandatory_and_optional_conditions():
    extra = Filter(must=[FieldCondition(key="article_number", match=MatchValue(value="KB0001"))])
    metadata = MetadataFilterBuilder(category="network")
    filt = build_metadata_filter(metadata, extra=extra)
    assert filt.must[-1] == extra
    assert len(filt.must) == 4  # workflow_state, security_level, category, extra


def test_extra_filter_alone_still_carries_both_mandatory_conditions():
    extra = Filter(must=[FieldCondition(key="service", match=MatchValue(value="sap-erp"))])
    filt = build_metadata_filter(extra=extra)
    assert len(filt.must) == 3
    assert filt.must[0].key == "workflow_state"
    assert filt.must[1].key == "security_level"
    assert filt.must[2] == extra


# ---------------------------------------------------------------------------
# Planted excluded-document acceptance test (Scope of Work requirement)
# ---------------------------------------------------------------------------


@pytest.fixture
def planted_exclusion_qdrant() -> tuple[QdrantClient, str]:
    """One included and one excluded article, vector-identical, ingested into
    a fresh in-memory collection."""
    client = QdrantClient(":memory:")
    collection = "planted_exclusion_test"
    ensure_collection(client, collection, dense_vector_size=DENSE_DIM)

    body = (
        "## Symptom\nVPN handshake fails after password rotation.\n\n"
        "## Resolution\nRe-authenticate."
    )
    included = _make_article(
        article_number="KB9001", category="network", title="Included article", body=body
    )
    excluded = _make_article(
        article_number="KB9002",
        category="hardware",  # deliberately outside the filter under test
        title="Excluded article",
        body=body,
    )

    engine = ConstantEmbeddingEngine()
    ingest_articles([included, excluded], client, collection, engine)
    return client, collection


def test_planted_excluded_document_never_returned_despite_identical_relevance(
    planted_exclusion_qdrant: tuple[QdrantClient, str],
) -> None:
    client, collection = planted_exclusion_qdrant
    engine = ConstantEmbeddingEngine()

    metadata = MetadataFilterBuilder(category="network")
    hits = hybrid_search(
        client,
        "vpn handshake fails after password rotation",
        collection_name=collection,
        limit=10,
        metadata=metadata,
        engine=engine,
    )

    assert hits, "expected the included article to be returned"
    returned_numbers = {h.article_number for h in hits}
    assert "KB9002" not in returned_numbers, (
        "planted excluded document (category=hardware) leaked past the filter "
        "even though it is vector-identical to the included document"
    )
    assert returned_numbers == {"KB9001"}


def test_planted_excluded_document_is_reachable_once_filter_widens(
    planted_exclusion_qdrant: tuple[QdrantClient, str],
) -> None:
    """Sanity check: the excluded document is real and indexed -- it's absent
    from the previous test because of the filter, not because ingestion
    silently dropped it."""
    client, collection = planted_exclusion_qdrant
    engine = ConstantEmbeddingEngine()

    metadata = MetadataFilterBuilder(category=["network", "hardware"])
    hits = hybrid_search(
        client,
        "vpn handshake fails after password rotation",
        collection_name=collection,
        limit=10,
        metadata=metadata,
        engine=engine,
    )
    returned_numbers = {h.article_number for h in hits}
    assert returned_numbers == {"KB9001", "KB9002"}
