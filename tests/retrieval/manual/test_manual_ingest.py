from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.retrieval.manual.manual_ingest import (
    _delete_section_points,
    _purge_unknown_section_points,
    build_section_point_id,
    embedding_text_for_chunk,
    ingest_manual_sections,
)


def _fake_chunk(
    section_id="section-1.3",
    chunk_index=0,
    section_number="1.3",
    section_title="Conventions",
    text="Body text",
):
    return SimpleNamespace(
        section_id=section_id,
        chunk_index=chunk_index,
        section_number=section_number,
        section_title=section_title,
        text=text,
    )


class TestBuildSectionPointId:
    def test_deterministic_for_the_same_inputs(self):
        a = build_section_point_id("section-1.3", 0)
        b = build_section_point_id("section-1.3", 0)
        assert a == b

    def test_differs_by_chunk_index(self):
        a = build_section_point_id("section-1.3", 0)
        b = build_section_point_id("section-1.3", 1)
        assert a != b

    def test_differs_by_section_id(self):
        a = build_section_point_id("section-1.3", 0)
        b = build_section_point_id("section-2.1", 0)
        assert a != b

    def test_is_a_valid_uuid_string(self):
        point_id = build_section_point_id("section-1.3", 0)
        uuid.UUID(point_id)  # raises ValueError if malformed


class TestEmbeddingTextForChunk:
    def test_prepends_section_number_and_title_ahead_of_the_body(self):
        chunk = _fake_chunk(
            section_number="6.4",
            section_title="KB0001 -- VPN fails",
            text="Symptom text here.",
        )
        result = embedding_text_for_chunk(chunk)
        assert result.startswith("6.4 KB0001 -- VPN fails")
        assert "Symptom text here." in result


class TestIngestManualSectionsGuards:
    def test_raises_on_empty_chunk_list(self):
        with pytest.raises(ValueError):
            ingest_manual_sections([], client=MagicMock())


class TestDeleteSectionPoints:
    def test_no_op_when_no_section_ids(self):
        client = MagicMock()
        _delete_section_points(client, "manual_sections", [])
        client.delete.assert_not_called()

    def test_deletes_with_a_section_id_filter(self):
        client = MagicMock()
        _delete_section_points(client, "manual_sections", ["section-1.3", "section-2.1"])
        client.delete.assert_called_once()
        _, kwargs = client.delete.call_args
        assert kwargs["collection_name"] == "manual_sections"


class TestPurgeUnknownSectionPoints:
    def test_removes_points_whose_section_id_is_no_longer_known(self):
        client = MagicMock()
        kept_point = SimpleNamespace(payload={"section_id": "section-1"})
        stale_point = SimpleNamespace(payload={"section_id": "section-stale"})
        client.scroll.return_value = ([kept_point, stale_point], None)

        removed = _purge_unknown_section_points(client, "manual_sections", {"section-1"})

        assert removed == 1
        client.delete.assert_called_once()

    def test_returns_zero_and_skips_delete_when_nothing_is_stale(self):
        client = MagicMock()
        client.scroll.return_value = (
            [SimpleNamespace(payload={"section_id": "section-1"})],
            None,
        )

        removed = _purge_unknown_section_points(client, "manual_sections", {"section-1"})

        assert removed == 0
        client.delete.assert_not_called()

    def test_paginates_through_every_scroll_offset(self):
        client = MagicMock()
        client.scroll.side_effect = [
            ([SimpleNamespace(payload={"section_id": "section-1"})], "cursor-2"),
            ([SimpleNamespace(payload={"section_id": "section-2"})], None),
        ]
        removed = _purge_unknown_section_points(
            client, "manual_sections", {"section-1", "section-2"}
        )
        assert removed == 0
        assert client.scroll.call_count == 2

    def test_points_with_no_section_id_payload_are_ignored(self):
        client = MagicMock()
        client.scroll.return_value = ([SimpleNamespace(payload={})], None)
        removed = _purge_unknown_section_points(client, "manual_sections", {"section-1"})
        assert removed == 0
        client.delete.assert_not_called()


class TestIngestManualSectionsHappyPath:
    def test_deletes_first_then_upserts_in_batches(self):
        chunks = [_fake_chunk(chunk_index=i) for i in range(5)]
        client = MagicMock()
        fake_engine = SimpleNamespace(
            dense_vector_size=8,
            embed_documents=lambda texts: [
                SimpleNamespace(dense=[0.0] * 8, sparse_indices=[], sparse_values=[]) for _ in texts
            ],
        )
        fake_payload = SimpleNamespace(to_qdrant_payload=lambda: {"section_id": "section-1.3"})

        with (
            patch("app.retrieval.manual.manual_ingest.ensure_manual_collection"),
            patch(
                "app.models.manual_section.ManualSectionPayload.from_chunk",
                return_value=fake_payload,
            ),
        ):
            total = ingest_manual_sections(
                chunks, client=client, embedding_engine=fake_engine, batch_size=2
            )

        assert total == 5
        client.delete.assert_called_once()
        assert client.upsert.call_count == 3  # batches of 2, 2, 1

    def test_all_points_are_deleted_before_any_upsert(self):
        chunks = [_fake_chunk(chunk_index=i) for i in range(2)]
        client = MagicMock()
        call_order: list[str] = []
        client.delete.side_effect = lambda *a, **k: call_order.append("delete")
        client.upsert.side_effect = lambda *a, **k: call_order.append("upsert")
        fake_engine = SimpleNamespace(
            dense_vector_size=8,
            embed_documents=lambda texts: [
                SimpleNamespace(dense=[0.0] * 8, sparse_indices=[], sparse_values=[]) for _ in texts
            ],
        )
        fake_payload = SimpleNamespace(to_qdrant_payload=lambda: {"section_id": "section-1.3"})

        with (
            patch("app.retrieval.manual.manual_ingest.ensure_manual_collection"),
            patch(
                "app.models.manual_section.ManualSectionPayload.from_chunk",
                return_value=fake_payload,
            ),
        ):
            ingest_manual_sections(
                chunks, client=client, embedding_engine=fake_engine, batch_size=10
            )

        assert call_order == ["delete", "upsert"]
