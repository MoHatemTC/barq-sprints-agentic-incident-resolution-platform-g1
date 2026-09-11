# Vector Store Index Specification

**Sprint**: Sprint 1 (S1.4: Knowledge Base Build & Vector Store Load)  
**Deliverable**: Deliverable D-05 (Qdrant Hybrid Retrieval Layer)  
**Author**: Antigravity & BARQ Systems Sprint Team  
**Status**: Implemented, Fully Tested, & Verified against Qdrant v1.14.0  

---

## 1. Executive Summary

This document specifies the technical architecture, vector configurations, payload schemas, query patterns, and persistence guarantees of the Qdrant vector database (`incident_knowledge_base`). The vector store is configured for **hybrid search**, combining dense semantic vectors (`BAAI/bge-small-en-v1.5`) with sparse keyword vectors (`Qdrant/bm25`), augmented by pre-filtered metadata payload indexes to support role-based access control and lifecycle filtering in Sprint 2.

The index contains **45 vector points** derived from the 11 real operational runbooks in [data/corpus/barq_articles.json](../../../data/corpus/barq_articles.json).

---

## 2. Collection Configuration

- **Collection Name**: `incident_knowledge_base` (configurable via `QDRANT_COLLECTION_NAME` in `.env`)
- **Docker Persistent Storage**: Persistent volume `barq_qdrant_data:/qdrant/storage` mapped to container `barq-qdrant` on port `6333` (HTTP) and `6334` (gRPC).
- **Client Configuration**: Initialized via [src/app/clients/qdrant.py](../../../src/app/clients/qdrant.py) using `RetrievalSettings`.

### 2.1 Named Vectors Configuration

```json
{
  "vectors": {
    "dense": {
      "size": 384,
      "distance": "Cosine"
    }
  },
  "sparse_vectors": {
    "sparse": {
      "index": {
        "on_disk": false
      },
      "modifier": null
    }
  }
}
```

HNSW parameters are left at Qdrant defaults (m=16, ef_construct=100) — the collection code does not override them. `modifier: null` means no server-side IDF (see §3).

| Vector Name | Type | Model / Algorithm | Dimensions | Distance | Purpose |
|---|---|---|---|---|---|
| **`dense`** | Dense Vector | `BAAI/bge-small-en-v1.5` | 384 | Cosine | Captures semantic intent, conceptual synonyms, and incident symptom patterns. |
| **`sparse`** | Sparse Vector | `Qdrant/bm25` (FastEmbed) | Dynamic (lexical tokens) | Dot product | Verbatim keyword matching for error codes (`RFC_ERROR_COMMUNICATION`), KB numbers, and commands. |

---

## 3. Sparse BM25 IDF Fitting Protocol

FastEmbed computes term weights using standard BM25:

$$IDF(t) = \ln\left(1 + \frac{N - n(t) + 0.5}{n(t) + 0.5}\right)$$

> [!IMPORTANT]
> **Corpus Batch Fitting Rule**: In [src/app/retrieval/ingest.py](../../../src/app/retrieval/ingest.py), all article chunk texts are accumulated into a single sequence and passed to `embedding_engine.embed_documents(all_chunk_texts)` in one batch call. This fits document frequency $n(t)$ across the entire corpus rather than per-article or per-chunk. Server-side `Modifier.IDF` is left disabled in Qdrant to prevent double-scaling of IDF weights.

### 3.1 Chunking Parameters

Chunking happens in [src/app/retrieval/chunking.py](../../../src/app/retrieval/chunking.py) (module defaults 1500/150 are for generic markdown); the ingest layer overrides them to honor the manual's own pilot configuration (manual §11.7):

| Parameter | Value | Source |
|---|---|---|
| `chunk_size` | 700 chars | Pilot indexing config, manual §11.7 |
| `chunk_overlap` | 120 chars | Pilot indexing config, manual §11.7 |
| split_on | heading | Pilot indexing config; implemented via header-aware markdown splitting |

---

## 4. Query vs. Document Embedding Protocol

- **Document Embedding**: Chunks are embedded during ingestion using `engine.embed_documents(texts)` without query instruction prefixes.
- **Query Embedding**: At search time, user queries are embedded using `engine.query_embed(query_text)`. `BAAI/bge-small-en-v1.5` does **not** require a query-side prompt prefix (its model card marks prefixes "not so necessary"), and FastEmbed's `query_embed()` adds none for it. The query/document paths are nevertheless kept separate at the engine boundary (`EmbeddingEngine` protocol) so that swapping in a prefix-dependent embedding model later is a one-line change (NFR-09), with no change to the retrieval graph.

---

## 5. Payload Schema & Indexing

Every vector point represents a single markdown chunk derived from an `Article`.

### 5.1 Keyword Payload Indexes (Fast Filtering)

The following 7 fields are indexed as `PayloadSchemaType.KEYWORD` during collection setup:

| Field Name | Type | Allowed / Sample Values | Index Type | Downstream Usage |
|---|---|---|---|---|
| `article_number` | String | `KB0001` through `KB0010` | `keyword` | Base article lookup across versions |
| `article_id` | String | `KB0001-v2.0`, `KB0010-v2.0` | `keyword` | Exact versioned point resolution |
| `category` | String | `network`, `software`, `hardware`, `inquiry` | `keyword` | ServiceNow incident taxonomy filtering |
| `service` | String | `corporate-vpn`, `sap-erp`, `order-processing` | `keyword` | Configuration item / service filtering |
| `workflow_state` | String | `published`, `draft`, `retired` | `keyword` | Excludes draft and decommissioned runbooks |
| `version` | String | `1.0`, `2.0`, `3.0`, `4.0` | `keyword` | Version disambiguation |
| `security_level` | String | `public`, `internal`, `restricted` | `keyword` | Tiered role-based retrieval (desk cards vs platform) |

### 5.2 Non-Indexed Retrievable Payload Fields

| Field Name | Type | Description |
|---|---|---|
| `title` | String | Title of the runbook |
| `section` | String | Chunk section breadcrumb (`Resolution`, `Symptom`, `Cause`, `Escalation`, `Warning`) |
| `chunk_index` | Integer | 0-indexed position within the parent article |
| `total_chunks` | Integer | Total count of chunks belonging to the parent article |
| `chunk_text` | String | Canonical markdown body of the chunk |
| `owner` | String \| null | Operational owner team |
| `author` | String \| null | Article author (split from the Owner cell in the KB0010-v2 grid) |
| `related_records` | List[String] | Associated problem and incident IDs |
| `sys_id` | String \| null | ServiceNow `kb_knowledge` sys_id (populated once published) |

---

## 6. Point ID Generation Scheme & Idempotency

Point IDs are deterministic UUIDv5 strings derived from a dedicated knowledge-base namespace (master plan §4.2), not the raw DNS namespace:

```python
KB_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "barq-g1-kb")
point_id = str(uuid.uuid5(KB_NAMESPACE, f"{article_id}::chunk::{chunk_index}"))
```

where `article_id` is the composed per-record key (`{article_number}-v{version}`, e.g. `KB0010-v2.0`). Chunk identities follow the same convention everywhere (`KB0010-v2.0::chunk::0`).

### Idempotency Guarantee
Ingestion is **replace-per-article**: before writing, `ingest_articles()` deletes every point belonging to the articles in the call (filtered on the `article_id` payload index — keyed per version, so `KB0010-v1.0` and `KB0010-v2.0` coexist), then upserts the fresh points. This guarantees that re-ingesting an **edited** article never leaves stale trailing chunks searchable in the index — the stale-procedure failure mode the manual's MIR-2026-03 narrative warns about. Incremental library calls that omit an article leave its points untouched; the seed-script path additionally passes `purge_unknown_articles=True`, which removes points whose `article_id` is no longer part of the corpus (deleted articles).

Because UUIDv5 is pure and deterministic, re-running `seed_qdrant.py` results in exactly 45 points with byte-identical point IDs (verified on the live server and by [tests/unit/test_ingest.py](../../../tests/unit/test_ingest.py)). If a collection ever holds points that cannot be reconciled with the corpus, `setup_qdrant.py --force-recreate` rebuilds it from scratch.

---

## 7. Docker Persistence Guarantee

The vector store is hosted in Docker via `docker-compose.yml`:
```yaml
  qdrant:
    image: qdrant/qdrant:v1.14.0
    container_name: barq-qdrant
    restart: unless-stopped
    ports:
      - "${QDRANT_BIND_IP:-127.0.0.1}:${QDRANT_PORT:-6333}:6333"
      - "${QDRANT_BIND_IP:-127.0.0.1}:${QDRANT_GRPC_PORT:-6334}:6334"
    volumes:
      - barq_qdrant_data:/qdrant/storage
```

Because `/qdrant/storage` is mounted to the named Docker volume `barq_qdrant_data`, shutting down the container (`docker compose down`) and restarting it (`docker compose up -d`) preserves all 45 indexed points and their payload indexes without requiring re-indexing.

---

## 8. Hybrid Search Querying Pattern (Sprint 2 Preview)

In Sprint 2, retrieval combines dense and sparse scores using Reciprocal Rank Fusion (RRF):

```python
from qdrant_client.models import Prefetch, Query, Fusion

# Hybrid query with metadata pre-filtering
results = client.query_points(
    collection_name="incident_knowledge_base",
    prefetch=[
        Prefetch(
            query=dense_vector,
            using="dense",
            limit=20,
            filter=role_and_state_filter,
        ),
        Prefetch(
            query=sparse_vector,
            using="sparse",
            limit=20,
            filter=role_and_state_filter,
        ),
    ],
    query=Query.fusion(Fusion.RRF),
    limit=5,
)
```

---

## 9. Operational CLI Commands & Verification

- **Initialize Collection & Indexes** (idempotent — re-running never deletes points; destructive rebuild only via `--force-recreate`):
  ```bash
  uv run python scripts/setup_qdrant.py
  ```
- **Seed Knowledge Articles (45 Chunks)** — single idempotent command: ensures the collection, embeds in one corpus pass, upserts deterministically, and verifies stored == upserted (exit 1 on mismatch):
  ```bash
  uv run python scripts/seed_qdrant.py
  ```
- **Automated Ingestion Test Suite (8 Tests)**:
  ```bash
  uv run pytest tests/unit/test_ingest.py -v
  ```
- **Chunking Regression Test Suite**:
  ```bash
  uv run pytest tests/unit/test_chunking.py -v
  ```
- **Validate Real Incident Coverage Matrix**:
  ```bash
  uv run python scripts/validate_corpus.py
  ```
