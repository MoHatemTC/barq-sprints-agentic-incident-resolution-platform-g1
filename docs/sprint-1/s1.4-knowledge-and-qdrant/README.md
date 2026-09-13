# S1.4 — Knowledge corpus, ServiceNow KB, Qdrant hybrid collection

**Owner:** [@kerolos-mohsen](https://github.com/kerolos-mohsen) · **Tracking:** [#11](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/11)

## Status

Implementation complete. Verified real operational corpus (11 runbooks extracted from the BARQ manual), dual-vector Qdrant hybrid collection (`BAAI/bge-small-en-v1.5` dense + `Qdrant/bm25` sparse), deterministic UUIDv5 chunking pipeline, and incident ground truth coverage matrix.

## Deliverables in this Folder

- [Corpus Design Specification](sprint1_corpus_design.md) (`sprint1_corpus_design.md`): Article schema, metadata validation rules, security tier taxonomy, 11-record inventory, and incident coverage matrix.
- [Index Specification](sprint1_index_spec.md) (`sprint1_index_spec.md`): Hybrid vector configuration, single-batch BM25 IDF fitting rules, payload indexing, deterministic UUIDv5 identities, and persistence architecture.

## Architecture Summary

- **Source Corpus**: 11 service-desk runbooks (`data/corpus/barq_articles.json`) extracted from `BARQ_IT_Service_Desk_Manual_Ed5.1.pdf`, each with Symptom / Cause / Resolution / Escalation sections and numbered resolution steps. They are procedural prose aimed at a service desk — they do **not** contain terminal commands or log paths, and only KB0008 carries an error code (`RFC_ERROR_COMMUNICATION`). (#36)
- **Chunking Pipeline**: `src/app/retrieval/chunking.py` implementing header-aware markdown splitting (Section 11.7 parameters: 700 chars, 120 overlap) producing exactly 45 bounded chunks.
- **Hybrid Vector Store**: Qdrant collection `incident_knowledge_base` with 45 points, indexed payload fields (`category`, `service`, `workflow_state`, `version`, `security_level`, `article_id`, `article_number`), and deterministic idempotency.
- **Ground Truth Evaluation**: `data/coverage_matrix.csv` with 13 benchmark scenarios for retrieval evaluation.
