# S1.4 — Knowledge corpus, ServiceNow KB, Qdrant hybrid collection

**Owner:** [@kerolos-mohsen](https://github.com/kerolos-mohsen) · **Tracking:** [#11](../../../../issues/11)

## Status

Implementation complete. Verified real operational corpus (11 runbooks extracted from the BARQ manual), dual-vector Qdrant hybrid collection (`BAAI/bge-small-en-v1.5` dense + `Qdrant/bm25` sparse), deterministic UUIDv5 chunking pipeline, and incident ground truth coverage matrix.

## Deliverables in this Folder

- [Corpus Design Specification](sprint1_corpus_design.md) (`sprint1_corpus_design.md`): Article schema, metadata validation rules, security tier taxonomy, 11-record inventory, and incident coverage matrix.
- [Index Specification](sprint1_index_spec.md) (`sprint1_index_spec.md`): Hybrid vector configuration, single-batch BM25 IDF fitting rules, payload indexing, deterministic UUIDv5 identities, and persistence architecture.

## Architecture Summary

- **Source Corpus**: 11 operational runbooks (`data/corpus/barq_articles.json`) with realistic terminal commands, log paths, error codes, and version-specific mitigations.
- **Chunking Pipeline**: `src/app/retrieval/chunking.py` implementing header-aware markdown splitting (Section 11.7 parameters: 700 chars, 120 overlap) producing exactly 45 bounded chunks.
- **Hybrid Vector Store**: Qdrant collection `incident_knowledge_base` with 45 points, indexed payload fields (`service`, `category`, `lifecycle_state`, `security_tier`, `version`), and deterministic idempotency.
- **Ground Truth Evaluation**: `data/coverage_matrix.csv` with 13 benchmark scenarios for retrieval evaluation.
