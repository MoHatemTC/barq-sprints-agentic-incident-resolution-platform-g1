# Milestone 3: KB answer cache verification

Implemented an opt-in Redis answer cache after screening and reference resolution.
Exact lookup precedes bounded semantic lookup. Successful, published, context-free KB
answers can be cached; personal/redacted questions, follow-ups, refusals, failed turns
and incident-specific requests bypass the cache.

Cache partitions include operator/access scope, corpus revision, model/prompt identity,
embedding models, Qdrant endpoint/collection and retrieval settings. Every hit rereads
cited payloads and checks content fingerprints, lifecycle and permissions. Semantic
reuse also checks identifiers, quantities, negation, modal words, actions and recognized
service qualifiers. Similarity 0.95 is an initial setting requiring E2E calibration.

Standard ingestion marks the revision pending before mutation and activates a new
revision after success. Cache-enabled writers serialize through a Redis lock. Partial
ingestion disables cache admission/reuse; a successful repair re-enables it. Feature-off
ingestion does not contact Redis. Cache outages fall back to retrieval; budget outages
still block paid processing.

## Verification

- Focused cache/graph/retrieval/service/wiring/ingestion tests: **65 passed**.
- Final `just check`: lockfile, lint, formatting and types passed; pytest reported
  **1,904 passed, 44 skipped, 29 integration tests deselected**, six existing warnings,
  in 88.16 seconds.
- Warm-hit regression: four model calls on a miss versus three on an exact hit,
  with lower mocked token cost. Screening, routing and budget admission remain active.
- Tests cover source changes/removal/retirement, access/model partitions, corpus
  revisions, partial-ingestion races, unsafe qualifiers, threshold misses, bounded
  entries, missing expired values, outages and refusal of a concurrent writer.

Models/Redis were mocked; ingestion tests used in-memory Qdrant and fake embeddings.
No paid calls, live ServiceNow writes or production migrations were run. Real Redis
TTL/concurrency, browser behavior and actual proxy costs remain E2E acceptance work.

Enable `CHAT_CACHE_ENABLED=true` in the API and ingestion environments and follow the
cache E2E steps in `admin_chatbot.md`. The default is off. M4 live incident reads and
M5 confirmed work notes are not implemented in this change set.
