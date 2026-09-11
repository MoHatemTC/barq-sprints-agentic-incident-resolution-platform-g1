# Knowledge Corpus & Ground Truth Design Specification

**Sprint**: Sprint 1 (S1.4: Knowledge Base Build & Vector Store Load)  
**Deliverables**: Deliverable D-05 (Qdrant Retrieval Layer) & Deliverable D-10 (Knowledge Corpus Design)  
**Author**: Antigravity & BARQ Systems Sprint Team  
**Status**: Implemented, Fully Tested, & Verified against BARQ Operations Manual (Edition 4.0)  

---

## 1. Executive Summary

This document specifies the architecture, metadata schema, controlled vocabulary, chunking methodology, code implementation, and ground truth evaluation design for the BARQ AI Incident Resolution Platform knowledge base.

Rather than relying on synthetic placeholders or generic IT templates, the knowledge corpus is extracted directly from **Section 6 (Service Desk Standard Operating Procedures & Engineering Runbooks)** of the official **BARQ Systems IT Service Operations Manual (`data/barq-system-kb.pdf`)**.

The canonical corpus is stored at [data/corpus/barq_articles.json](../../../data/corpus/barq_articles.json) (git-ignored — the source manual is marked INTERNAL) and comprises **11 verified records** with realistic technical syntax:
- **Verbatim Error Signatures**: Exact platform error strings carried through unmodified (`RFC_ERROR_COMMUNICATION`, `HTTP 500`, `connection acquisition timed out`, `invalid credentials`).
- **Standard Operating Procedures**: Four canonical sections (`## Symptom`, `## Cause`, `## Resolution`, `## Escalation`) formatted in clean Markdown; resolutions are numbered plain-prose steps. The retired `KB0010-v1.0` additionally carries a `## Warning` section quoting the manual's "Why this revision is dangerous" callout.
- **No Fenced Code Blocks**: The source manual contains no fenced code — its procedures are prose steps, and the extraction preserves them as prose (the chunker's fence-balancing machinery exists for future code-bearing sources).

---

## 2. Corpus Strategy & Scope: Path B (Real Data) vs. Path A (Synthetic)

### 2.1 The Choice of Path B (11 Real Records) Over Path A (25 Synthetic Articles)
Initial project scoping (and baseline partner requirements) proposed authoring at least 25 articles with synthetic technical error codes (designated **Path A**). However, analysis of the official BARQ Systems IT Service Operations Manual (Edition 4.0) revealed Section 6 already contains 10 battle-tested, authoritative runbooks (`KB0001` through `KB0010`), with `KB0010` documented in two distinct historical revisions (`v1.0` retired vs. `v2.0` published).

We selected **Path B (Production Extraction of Real Operational Data)** for the following engineering reasons:

1. **Authenticity & Lexical Precision**: The 11 real records contain genuine error strings (`RFC_ERROR_COMMUNICATION`, `HTTP 500`, `connection acquisition timed out`) and authentic operational context from BARQ Systems (message-server reachability for SAP, spooler behavior, DFS drive mapping, 802.11 roaming), preserved verbatim by a deterministic, zero-loss extraction pipeline.
2. **Evaluation Ground Truth Alignment**: The worked incidents in Section 7 of the manual (`INC0010023`, `INC0010047`, `INC0010064`, `INC0010052`) and the related-record citations inside the articles themselves map directly to these 11 runbooks in [data/coverage_matrix.csv](../../../data/coverage_matrix.csv).
3. **Negative Control Verification**: The corpus includes an authentic out-of-scope incident (`INC0010047`, printer mechanical fault — the manual's own pilot declined it at score 0.31 vs threshold 0.55) to evaluate unanswerable queries without fabricating synthetic topics.
4. **Lifecycle & Version Disambiguation**: The `KB0010-v1.0` (Retired) vs. `KB0010-v2.0` (Published) pair provides the exact near-duplicate version disambiguation required for Sprint 2 evaluation.
5. **No Dilution**: Fabricating additional synthetic articles would dilute the genuine technical vocabulary extracted directly from the BARQ manual and break alignment with the real incident data.

---

## 3. Canonical Article Model & Mandatory Metadata Fields

Every record in `data/corpus/barq_articles.json` and every point payload in Qdrant implements the `Article` and `KnowledgePayload` schemas defined in [src/app/models/knowledge.py](../../../src/app/models/knowledge.py).

### 3.1 The 5 Mandatory Metadata Fields

| Field Name | Storage Type | Allowed Values | Qdrant Index | Purpose |
|---|---|---|---|---|
| `category` | Keyword | Open controlled vocabulary slug (`network`, `software`, `hardware`, `inquiry`) | `KEYWORD` | High-level ServiceNow incident taxonomy partition |
| `service` | Keyword | Open controlled vocabulary slug (`corporate-vpn`, `corporate-email`, `file-services`, `print-services`, `identity`, `endpoint`, `sap-erp`, `corporate-wifi`, `order-processing`) | `KEYWORD` | Configuration item (CI) and service routing |
| `workflow_state` | Keyword | Closed enum: `published`, `draft`, `retired` | `KEYWORD` | Lifecycle governance; excludes decommissioned runbooks from standard resolution |
| `version` | Keyword | Semver string (`1.0`, `2.0`, `3.0`, `4.0`) | `KEYWORD` | Version-specific resolution targeting |
| `security_level` | Keyword | Closed enum: `public`, `internal`, `restricted` | `KEYWORD` | Tiered role-based access control (RBAC) filtering |

### 3.2 Core Article Identity Fields
- `article_number`: Unique KB identifier in the manual's four-digit format `^KB\d{4}$` (`KB0001` through `KB0010`).
- `version`: Two-part version string (`1.0` through `4.0`).
- `unique_key` / `article_id`: Composed canonical key `f"{article_number}-v{version}"` (e.g. `KB0001-v2.0`).
- `title`: Complete title of the standard operating procedure.
- `body`: Canonical Markdown body containing standard sections and fenced code blocks.
- `short_description`: One-line summary (maximum 255 characters, matching the ServiceNow `kb_knowledge.short_description` column limit).
- `owner`: Operational team responsible for the runbook (e.g. `Network Operations`, `Identity & Access`, `Platform Engineering`).
- `author`: Individual author, split from the manual's metadata grid (e.g. `L. Haddad`, `K. Selim`).
- `reviewed_on`: Review date mirrored from the manual (e.g. `11 Apr 2026`).
- `related_records`: Associated problem, incident, change, and major-incident IDs (e.g. `PRB0040012`, `INC0010023`, `CHG0030455`, `MIR-2026-03`).
- `sys_id`: 32-character ServiceNow sys_id seam (`None` prior to publishing; populated upon publishing to ServiceNow).

### 3.2.1 The Two Numbering Worlds

The corpus keys on the manual's article numbers (`KB0001`…). When articles are published to ServiceNow, the platform assigns its **own** KB numbers and `sys_id`s — they will not match. Therefore: the coverage matrix and all internal references key on `article_number` (stable across republishing); the publish script (next round) records the `article_number → sys_id` mapping at first publish and matches by `sys_id` for updates; ServiceNow's assigned number is display-only (citations/permalinks).

### 3.3 Security Level Classification Rules
Because raw PDF tables in the Operations Manual do not feature a native security classification column, security tiers are established systematically during extraction:

1. **`internal` (Standard Service Desk & General Employee Access)**:
   - Covers end-user self-service: desktop connectivity, identity self-service, and collaboration tools.
   - Applied to: `KB0001` (VPN), `KB0002` (Outlook), `KB0003` (File Shares), `KB0005` (Account Lockout), `KB0006` (MFA Device), `KB0009` (Wi-Fi).
2. **`restricted` (Elevated Engineering & Production Infrastructure Access)**:
   - Covers shared print infrastructure, endpoint driver-level diagnostics, core ERP reachability, and production service remediation procedures.
   - Applied to: `KB0004` (Print Services), `KB0007` (Endpoint Performance), `KB0008` (SAP Basis RFC), `KB0010` both versions (Order Service Pool Exhaustion).

The mapping is implemented as the `SECURITY_TIERS` table in [src/app/retrieval/barq_manual.py](../../../src/app/retrieval/barq_manual.py) and asserted per-article by [tests/unit/test_corpus.py](../../../tests/unit/test_corpus.py).

---

## 4. Real Knowledge Base Corpus Inventory (11 Records)

The 11 canonical records in [data/corpus/barq_articles.json](../../../data/corpus/barq_articles.json) extracted from Section 6 of `data/barq-system-kb.pdf`. Titles below are shown in the manual's title case; they are stored as the manual's uppercase banner (e.g. `VPN AUTHENTICATION FAILS AFTER A PASSWORD CHANGE`):

| Article ID | Title | Service | Category | State | Security | Notes |
|---|---|---|---|---|---|---|
| `KB0001-v2.0` | VPN authentication fails after a password change | `corporate-vpn` | `network` | `published` | `internal` | Related: `PRB0040012`, `INC0010023` |
| `KB0002-v3.0` | Outlook shows Disconnected and no mail is delivered | `corporate-email` | `software` | `published` | `internal` | |
| `KB0003-v2.0` | Mapped shared drive is missing after sign-in | `file-services` | `network` | `published` | `internal` | |
| `KB0004-v1.0` | Print jobs queue but nothing prints | `print-services` | `hardware` | `published` | `restricted` | Shared print-infrastructure procedure |
| `KB0005-v4.0` | Account is locked after repeated failed sign-ins | `identity` | `inquiry` | `published` | `internal` | |
| `KB0006-v3.0` | Multi-factor authentication after a lost or replaced device | `identity` | `inquiry` | `published` | `internal` | |
| `KB0007-v2.0` | Laptop performance degrades after a system update | `endpoint` | `hardware` | `published` | `restricted` | Driver-level diagnostics |
| `KB0008-v1.0` | SAP GUI connection times out with RFC_ERROR_COMMUNICATION | `sap-erp` | `software` | `published` | `restricted` | Related: `KE0000034`, `INC0010031` |
| `KB0009-v2.0` | Wi-Fi drops repeatedly on the 5 GHz corporate network | `corporate-wifi` | `network` | `published` | `internal` | |
| `KB0010-v1.0` | Order service connection pool exhaustion | `order-processing` | `software` | `retired` | `restricted` | **RETIRED VARIANT**: restart procedure caused outage `MIR-2026-03`; carries the manual's `## Warning` section. Grid has no Service/Category/Owner cells — service/category defaulted (declared in the extraction report). |
| `KB0010-v2.0` | Order service connection pool exhaustion | `order-processing` | `software` | `published` | `restricted` | **ACTIVE CANONICAL**: change-controlled pool drain via emergency change `CHG0030455`. Related: `MIR-2026-03`, `PRB0040018`, `CHG0030455`, `INC0010052`. |

### Why Exactly 11 Records?
Section 6 of the BARQ Operations Manual contains 10 operational topics (`KB0001` through `KB0010`). For `KB0010`, the manual explicitly documents two distinct revisions:
- The dangerous **Version 1 (Retired)**: Restarted the application server and caused outage `MIR-2026-03`.
- The current **Version 2 (Published)**: Change-controlled connection drain.

Extracting both versions provides the exact real-world version disambiguation and lifecycle filtering benchmark required by Sprint 2.

---

## 5. Markdown Chunking Engine Specification (Section 11.7)

Chunking is performed by [src/app/retrieval/chunking.py](../../../src/app/retrieval/chunking.py) following Section 11.7 specifications:

- **Parameters**: `chunk_size = 700`, `chunk_overlap = 120`.
- **Structural Boundary Splitting**: Split along Markdown headers:
  - `#` ➔ `Title`
  - `##` ➔ `Section` (`Symptom`, `Cause`, `Resolution`, `Escalation`, `Warning`)
  - `###` ➔ `Subsection`
- **Code Block Integrity (`balance_code_fences`)**: Unclosed code fences split across chunk boundaries are automatically detected and closed in the opening chunk and reopened in the continuation chunk.
- **Total Chunks Produced**: The 11 articles produce **exactly 45 retrievable chunks**:
  - 11 records × 4 body sections (`Symptom`, `Cause`, `Resolution`, `Escalation`) = 44 chunks. At the 700-char limit no section exceeds one chunk, so no recursive sub-splitting occurs.
  - `KB0010-v1.0` carries a 5th `Warning` section (the manual's "Why this revision is dangerous" callout) = 1 extra chunk.
  - Total: **45 bound-validated chunks**.
- **Regression Guard**: Verified continuously by [tests/unit/test_chunking.py](../../../tests/unit/test_chunking.py).

---

## 6. Ground Truth & Coverage Matrix Specification

The ground truth file [data/coverage_matrix.csv](../../../data/coverage_matrix.csv) maps incidents to runbooks for retrieval evaluation. It is a **standard machine-readable CSV** (no comment lines — a plain `csv.DictReader`/pandas reader sees exactly the incident rows) with **25 scenarios**: 13 from the manual (`source=manual`), 12 synthetic (`source=synthetic`, grounded in the real corpus).

| Incident ID | Incident Description | Primary Article | Acceptable | Forbidden | Answerable | Rationale |
|---|---|---|---|---|---|---|
| `INC0010023` | VPN authentication failure after a password reset | `KB0001-v2.0` | — | — | `true` | Worked ticket resolved via KB0001 |
| `INC0010047` | Printer hardware mechanical squeaking and grinding noise | — | — | — | **`false`** | **Unanswerable**: hardware fault outside the KB; the manual's own pilot declined it (score 0.31 vs threshold 0.55) |
| `INC0010064` | Account locked after repeated failed sign-ins and MFA reset attempts | `KB0005-v4.0` | `KB0006-v3.0` | — | `true` | Worked ticket; MFA article is an acceptable neighbor |
| `INC0010052` | Order service pool exhausted, HTTP 500 under load | `KB0010-v2.0` | — | **`KB0010-v1.0`** | `true` | **Forbidden disambiguation**: must resolve to published v2.0; surfacing the retired v1.0 *fails* the eval (manual §11.5: retired revisions are "removed before ranking, not ranked low") |
| `INC0009884` | Order service pool exhaustion causing in-flight order loss | `KB0010-v2.0` | — | **`KB0010-v1.0`** | `true` | The 14 Mar 2026 major incident (`MIR-2026-03`); same forbidden rule |
| `INC0010024`–`INC0010033` | One per article: Outlook, drive mapping, printing, lockout, MFA device, laptop, SAP RFC, Wi-Fi | respective article | — | — | `true` | Related-record citations inside each article's metadata grid |

The 12 synthetic scenarios add: **7 multi-article incidents** (`primary_article_ids` with two articles — INC0010091/092/096/097/098/099/0100, meeting the ≥5 multi-article evaluation requirement) and **5 out-of-KB unanswerable cases** (INC0010093/094/095/0101/0102 — facilities and business-system faults with no runbook, testing correct refusal/low-confidence behavior).

Column semantics: `source` distinguishes real manual incidents from authored ones; `forbidden_article_ids` lists articles that must never appear in a correct retrieval — the harness treats surfacing one as a failure, never a pass. `acceptable_article_ids` is **sparse by design**: an entry requires a direct signal in the incident description itself (symptom or explicit cause chain) — currently only INC0010064 → `KB0006-v3.0` ("MFA reset attempts"). Scoring contract for the S4.4 harness: primary hit = 1.0, acceptable hit = 0.5, forbidden hit = failure, unanswerable + refusal = correct. All `primary`, `acceptable`, and `forbidden` references are validated against the corpus, plus the row invariant `forbidden ∩ (primary ∪ acceptable) = ∅`, by:
```bash
uv run python scripts/validate_corpus.py
```

---

## 7. Code Architecture & File Path Mapping

All components align with G1's standard packaging layout (`src/app/`) with root re-export shims (`src/retrieval/`) for cross-compatibility:

```
barq-sprints-agentic-incident-resolution-platform-g1/
├── src/app/
│   ├── models/
│   │   └── knowledge.py         # Article, ArticleChunk, KnowledgePayload schemas
│   ├── retrieval/
│   │   ├── barq_manual.py       # PDF Section 6 extraction and text cleaning logic
│   │   ├── chunking.py          # Markdown header splitter + code fence balancer
│   │   ├── embedding.py         # FastEmbedEngine (bge-small-en-v1.5 + Qdrant/bm25)
│   │   ├── extraction.py        # ServiceNow HTML -> Markdown (publish round-trip, next sprint step)
│   │   ├── ingest.py            # Batch chunking, single-batch BM25 fitting, UUIDv5 upsert
│   │   └── sources.py           # LocalJSONSource loader
│   └── clients/
│       └── qdrant.py            # QdrantClient factory and ensure_collection()
├── src/retrieval/               # Root Re-Export Shims (packaged in the wheel)
│   ├── __init__.py              # Package marker
│   ├── embedding.py             # Re-exports FastEmbedEngine, EmbeddingEngine, EmbeddedText, DENSE_VECTOR_SIZE
│   └── ingest.py                # Re-exports ingest_articles, build_point_id, KB_NAMESPACE
├── data/
│   ├── barq-system-kb.pdf       # Source BARQ Operations Manual (Edition 4.0, git-ignored, INTERNAL)
│   ├── coverage_matrix.csv      # Real incident-to-runbook ground truth mapping (13 scenarios)
│   └── corpus/
│       ├── barq_articles.json   # 11 canonical extracted runbooks (git-ignored, INTERNAL)
│       ├── README.md            # Corpus schema documentation (tracked)
│       └── barq_ingestion_report.md  # Sanitized extraction report: counts + warnings only (tracked)
├── docker-compose.yml           # Persistent Qdrant 1.14.0 (volume: barq_qdrant_data)
├── scripts/
│   ├── extract_barq_kb.py       # CLI: re-extract PDF into data/corpus/barq_articles.json
│   ├── setup_qdrant.py          # CLI: idempotent collection + payload indexes (--force-recreate to rebuild)
│   ├── seed_qdrant.py           # CLI: chunk, embed, seed Qdrant (45 points), verify stored == upserted
│   └── validate_corpus.py       # CLI: validate corpus schema and coverage matrix (primary + acceptable IDs)
└── tests/
    ├── integration/             # Integration tests against live external services
    └── unit/                    # In-memory and mocked unit tests
        ├── test_barq_manual.py  # Extractor unit tests & real-PDF regression
        ├── test_chunking.py     # 45 chunks & code fence balancing
        ├── test_corpus.py       # Corpus schema, tiers, lifecycle, matrix
        ├── test_embedding.py    # FastEmbed engine & Qdrant in-memory client
        ├── test_extraction.py   # ServiceNow HTML -> Markdown round-trip
        ├── test_ingest.py       # Ingestion orchestration (mocked embed, in-memory Qdrant)
        ├── test_schema.py       # Pydantic schemas (Article, KnowledgePayload)
        └── test_smoke.py        # Basic smoke test
```

---

## 8. Technical Approach & Implementation Details

### 8.1 Extraction Approach (`src/app/retrieval/barq_manual.py`)
- Uses `pdftotext -layout` (poppler-utils) via subprocess — no PyMuPDF — to extract Section 6 (pages 17–24) with layout preserved.
- Deterministically strips the repeating page header/footer and TOC dot-leaders before parsing.
- Parses each article's header banner and metadata grid, including the KB0010-v1 Retired variant (`Retired on` / `Reason` rows) and the KB0010-v2 grid whose Owner cell carries `Team · Author` with no separate Author label.
- Converts the four fixed body sections into canonical Markdown (`## Symptom`, `## Cause`, `## Resolution`, `## Escalation`); the `## Warning` section of KB0010-v1.0 comes from the manual's own "Why this revision is dangerous" callout.
- Resolution steps are split **sequence-aware**: a line-start number is only accepted as a step marker when it continues 1, 2, 3…, so wrapped numbers (dates, versions) never become fake steps; a zero-loss guard reassembles and compares the text, raising rather than emitting corrupted Markdown (`assert_zero_loss`).
- Normalizes in the adapter only: version `"2"` → `"2.0"`, `Published` → `published`, `security_level` from the `SECURITY_TIERS` table, `short_description` from the Symptom's first sentence. KB0010's absent Service/Category cells are defaulted and declared in the extraction report.

### 8.2 Chunking & Balancing Approach (`src/app/retrieval/chunking.py`)
- Breaks articles into chunks honoring Section 11.7 parameters (max 700 chars, 120 char overlap). The real corpus needs no recursive sub-splitting at this size — every section fits one chunk.
- `balance_code_fences` scans each chunk: if an unclosed code block fence is found, it appends a closing fence to the current chunk and prepends an opening fence with the detected language tag to the subsequent chunk. (The real corpus contains no fenced code; the mechanism exists for future code-bearing sources.)
- Guarantees zero structural corruption during retrieval.

### 8.3 Ingestion & Embedding Approach (`src/app/retrieval/ingest.py`)
- **Single-Batch BM25 Fitting**: In `ingest_articles()`, all 45 chunk texts across all 11 articles are flattened into a single list and passed to `embedding_engine.embed_documents(all_chunk_texts)` in one batch call. This fits the corpus-wide document frequency $n(t)$ accurately without multi-pass skew.
- **Deterministic UUIDv5 (dedicated namespace)**: `KB_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "barq-g1-kb")`; point IDs are `uuid.uuid5(KB_NAMESPACE, f"{article_id}::chunk::{chunk_index}")`. Running `seed_qdrant.py` multiple times leaves exactly 45 points with byte-identical IDs (verified against the live Qdrant).
- **Verification & failure behavior**: upserts run with `wait=True`; the seed script compares stored vs upserted counts and exits non-zero on mismatch; an empty corpus raises instead of silently seeding nothing.
- **Persistent Storage**: Docker volume `barq_qdrant_data` persists the collection on disk, surviving container restarts without re-indexing.
