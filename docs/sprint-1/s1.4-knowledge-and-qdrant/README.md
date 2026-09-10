# S1.4 — Knowledge corpus, ServiceNow KB, Qdrant hybrid collection

**Owner:** [@kerolos-mohsen](https://github.com/kerolos-mohsen) · **Tracking:** [#11](../../../../issues/11)

## Status

Not started beyond infrastructure. The Docker Compose scaffold for Qdrant is
merged (`docker-compose.yml`); no corpus design, KB publication, or vector
collection exists yet.

## What lands in this folder once work begins

- Corpus design: what articles must contain, what metadata they carry
  (category, service, status, version, security level), and the
  incident-to-article ground-truth mapping that Sprint 2 and Sprint 4 both
  depend on
- Whichever acquisition path the mentor assigns — authored from scratch, or a
  supplied dataset validated and normalised
- Qdrant collection setup: dense and sparse vectors, one collection

## Requirements

Article content must contain real command syntax, error codes, file paths and
version strings — sparse retrieval has nothing to match against generic prose.
