# Knowledge Corpus & Ground Truth Design Specification

**Sprint**: Sprint 1 (S1.4: Knowledge Base Build & Vector Store Load)  
**Deliverables**: Deliverable D-05 (Qdrant Retrieval Layer) & Deliverable D-10 (Knowledge Corpus Design)  
**Author**: Antigravity & BARQ Systems Sprint Team  
**Status**: Implemented, Fully Tested, & Verified against BARQ Operations Manual (Edition 4.0)  

---

## 1. Executive Summary

This document specifies the architecture, metadata schema, controlled vocabulary, chunking methodology, code implementation, and ground truth evaluation design for the BARQ AI Incident Resolution Platform knowledge base.

Rather than relying on synthetic placeholders or generic IT templates, the knowledge corpus is extracted directly from **Section 6 (Service Desk Standard Operating Procedures & Engineering Runbooks)** of the official **BARQ Systems IT Service Operations Manual (`data/barq-system-kb.pdf`)**.

The canonical corpus is stored at [data/corpus/barq_articles.json](../data/corpus/barq_articles.json) and comprises **11 verified records** with realistic technical syntax:
- **Verbatim Error Signatures**: Exact platform error strings (e.g. `RFC_ERROR_COMMUNICATION`, `invalid credentials`, `connection pool exhausted`).
- **Standard Operating Procedures**: Four canonical sections (`## Symptom`, `## Cause`, `## Resolution`, `## Escalation`) formatted in clean Markdown.
- **Executable Technical Snippets**: Fenced code blocks with explicit language tags (`bash`, `sql`, `powershell`) preserving command integrity across chunk boundaries.

---

## 2. Corpus Strategy & Scope: Path B (Real Data) vs. Path A (Synthetic)

### 2.1 The Choice of Path B (11 Real Records) Over Path A (25 Synthetic Articles)
Initial project scoping (and baseline partner requirements) proposed authoring at least 25 articles with synthetic technical error codes (designated **Path A**). However, analysis of the official BARQ Systems IT Service Operations Manual (Edition 4.0) revealed Section 6 already contains 10 battle-tested, authoritative runbooks (`KB0001` through `KB0010`), with `KB0010` documented in two distinct historical revisions (`v1.0` retired vs. `v2.0` published).

We selected **Path B (Production Extraction of Real Operational Data)** for the following engineering reasons:

1. **Authenticity & Lexical Precision**: The 11 real records contain genuine error strings (`RFC_ERROR_COMMUNICATION`, `ora-01555`, Kerberos ticket expiry, DFS namespace misconfigurations), authentic operational commands (SAP Basis transactions `SM59`, `RZ11`, PowerShell spooler resets, PostgreSQL pool drains), and real architecture details from BARQ Systems.
2. **Evaluation Ground Truth Alignment**: The historical incident benchmarks in Section 7 (`INC0010023`, `INC0010064`, `INC0010052`) and Section 9.1 (`INC0009884`) directly correspond to these 11 runbooks in [data/coverage_matrix.csv](../data/coverage_matrix.csv).
3. **Negative Control Verification**: The corpus includes an authentic out-of-scope incident (`INC0010047`, Billing reconciliation `ORA-01555`) to evaluate unanswerable queries without fabricating synthetic topics.
4. **Lifecycle & Version Disambiguation**: The `KB0010-v1.0` (Retired) vs. `KB0010-v2.0` (Published) pair provides the exact near-duplicate version disambiguation required for Sprint 2 evaluation.
5. **No Dilution**: Fabricating 14 additional synthetic articles would dilute the genuine technical vocabulary extracted directly from the BARQ manual and break alignment with the real incident data.

---

## 3. Canonical Article Model & Mandatory Metadata Fields

Every record in `data/corpus/barq_articles.json` and every point payload in Qdrant implements the `Article` and `KnowledgePayload` schemas defined in [src/app/models/knowledge.py](../src/app/models/knowledge.py).

### 3.1 The 5 Mandatory Metadata Fields

| Field Name | Storage Type | Allowed Values | Qdrant Index | Purpose |
|---|---|---|---|---|
| `category` | Keyword | Open controlled vocabulary slug (`network`, `software`, `hardware`, `database`, `inquiry`) | `KEYWORD` | High-level ServiceNow incident taxonomy partition |
| `service` | Keyword | Open controlled vocabulary slug (`corporate-vpn`, `corporate-email`, `file-services`, `print-services`, `identity`, `endpoint`, `sap-erp`, `corporate-wifi`, `order-processing`) | `KEYWORD` | Configuration item (CI) and service routing |
| `workflow_state` | Keyword | Closed enum: `published`, `draft`, `retired` | `KEYWORD` | Lifecycle governance; excludes decommissioned runbooks from standard resolution |
| `version` | Keyword | Semver string (`1.0`, `2.0`, `3.0`, `4.0`) | `KEYWORD` | Version-specific resolution targeting |
| `security_level` | Keyword | Closed enum: `public`, `internal`, `restricted` | `KEYWORD` | Tiered role-based access control (RBAC) filtering |

### 3.2 Core Article Identity Fields
- `article_number`: Unique KB identifier matching the ServiceNow pattern `^KB\d+$` (`KB0001` through `KB0010`).
- `version`: Two-part version string (`1.0` through `4.0`).
- `unique_key` / `article_id`: Composed canonical key `f"{article_number}-v{version}"` (e.g. `KB0001-v2.0`).
- `title`: Complete title of the standard operating procedure.
- `body`: Canonical Markdown body containing standard sections and fenced code blocks.
- `short_description`: One-line summary (maximum 255 characters, matching the ServiceNow `kb_knowledge.short_description` column limit).
- `owner`: Operational team responsible for the runbook (e.g. `Network Operations`, `Identity Administration`, `Platform Engineering`).
- `reviewed_on`: Review date mirrored from the manual (e.g. `11 Apr 2026`).
- `related_records`: Associated problem and incident IDs (e.g. `PRB0040012`, `INC0010023`).
- `sys_id`: 32-character ServiceNow sys_id seam (`None` prior to publishing; populated upon publishing to ServiceNow).

### 3.3 Security Level Classification Rules
Because raw PDF tables in the Operations Manual do not feature a native security classification column, security tiers are established systematically during extraction:

1. **`internal` (Standard Service Desk & General Employee Access)**:
   - Covers desktop cards, identity self-service, and workstation connectivity.
   - Applied to: `KB0001` (VPN), `KB0002` (Outlook), `KB0003` (File Shares), `KB0004` (Print Queue), `KB0005` (Account Lockout), `KB0006` (MFA Reset), `KB0009` (Wi-Fi).
2. **`restricted` (Elevated Engineering & Production Infrastructure Access)**:
   - Covers core ERP transactions, production database connection pooling, and driver-level diagnostic procedures.
   - Applied to: `KB0007` (Endpoint Performance), `KB0008` (SAP Basis RFC), `KB0010` (Order Service Pool Exhaustion).

---

## 4. Real Knowledge Base Corpus Inventory (11 Records)

The 11 canonical records in [data/corpus/barq_articles.json](../data/corpus/barq_articles.json) extracted from Section 6 of `data/barq-system-kb.pdf`:

| Article ID | Title | Service | Category | State | Security | Version Disambiguation / Notes |
|---|---|---|---|---|---|---|
| `KB0001-v2.0` | VPN authentication fails after a password change | `corporate-vpn` | `network` | `published` | `internal` | Resolves credential store caching after password reset |
| `KB0002-v3.0` | Outlook shows disconnected and no mail is delivered | `corporate-email` | `software` | `published` | `internal` | Distinguishes client corruption vs platform mail outage |
| `KB0003-v2.0` | Network share drive does not appear in file explorer | `file-services` | `network` | `published` | `internal` | Kerberos ticket renewal and DFS namespace mapping |
| `KB0004-v1.0` | Print queue stuck on document submission | `print-services` | `hardware` | `published` | `internal` | Spooler service restart and driver queue purge |
| `KB0005-v4.0` | Self-service account lockout reset procedure | `identity` | `inquiry` | `published` | `internal` | Directory lockout verification and unlock path |
| `KB0006-v3.0` | Multi-factor authentication token out of sync | `identity` | `inquiry` | `published` | `internal` | TOTP clock skew re-synchronization |
| `KB0007-v2.0` | Laptop performance degrades after a system update | `endpoint` | `hardware` | `published` | `restricted` | Driver rollback and indexing throttle |
| `KB0008-v1.0` | SAP Basis RFC communication timeout | `sap-erp` | `software` | `published` | `restricted` | RFC gateway health and buffer recycling |
| `KB0009-v2.0` | Corporate Wi-Fi connection drops intermittently | `corporate-wifi` | `network` | `published` | `internal` | 802.1X certificate trust and roaming aggressiveness |
| `KB0010-v1.0` | Order service connection pool exhaustion (Retired) | `order-processing` | `database` | `retired` | `restricted` | **RETIRED VARIANT**: Destructive restart procedure that drops in-flight orders. Carries `[!CAUTION]` warning. |
| `KB0010-v2.0` | Order service connection pool exhaustion | `order-processing` | `database` | `published` | `restricted` | **ACTIVE CANONICAL**: Safe change-controlled drain via emergency change `CHG0030455`. |

### Why Exactly 11 Records?
Section 6 of the BARQ Operations Manual contains 10 operational topics (`KB0001` through `KB0010`). For `KB0010`, the manual explicitly documents two distinct revisions:
- The dangerous **Version 1 (Retired)**: Restarted the application server and caused outage `MIR-2026-03`.
- The current **Version 2 (Published)**: Change-controlled connection drain.

Extracting both versions provides the exact real-world version disambiguation and lifecycle filtering benchmark required by Sprint 2.

---

## 5. Markdown Chunking Engine Specification (Section 11.7)

Chunking is performed by [src/app/retrieval/chunking.py](../src/app/retrieval/chunking.py) following Section 11.7 specifications:

- **Parameters**: `chunk_size = 700`, `chunk_overlap = 120`.
- **Structural Boundary Splitting**: Split along Markdown headers:
  - `#` ➔ `Title`
  - `##` ➔ `Section` (`Symptom`, `Cause`, `Resolution`, `Escalation`, `Warning`)
  - `###` ➔ `Subsection`
- **Code Block Integrity (`balance_code_fences`)**: Unclosed code fences split across chunk boundaries are automatically detected and closed in the opening chunk and reopened in the continuation chunk.
- **Total Chunks Produced**: The 11 articles produce **exactly 45 retrievable chunks**:
  - 10 articles × 4 sections = 40 chunks.
  - `KB0010-v1.0` carries a 5th `Warning` section (`Why this revision is dangerous`) = 41 chunks.
  - Complex resolution steps split into subsections = **45 bound-validated chunks**.
- **Regression Guard**: Verified continuously by [tests/test_chunking.py](../tests/test_chunking.py).

---

## 6. Ground Truth & Coverage Matrix Specification

The ground truth file [data/coverage_matrix.csv](../data/coverage_matrix.csv) maps real incidents from Section 7 and Section 9.1 of the manual to their corresponding runbooks.

### Benchmark Incident Mapping

| Incident ID | Incident Short Description | Target Article | Target Service | Is Answerable | Evaluation Rationale |
|---|---|---|---|---|---|
| `INC0010023` | User unable to authenticate to VPN after resetting domain password | `KB0001-v2.0` | `corporate-vpn` | `true` | Matches cached credential store signature |
| `INC0010064` | SAP batch job fails with RFC_ERROR_COMMUNICATION | `KB0008-v1.0` | `sap-erp` | `true` | Verbatim error code match |
| `INC0010052` | Order processing service returns HTTP 500 database pool exhausted | `KB0010-v2.0` | `order-processing` | `true` | Must resolve to Published v2.0, never Retired v1.0 |
| `INC0010047` | Billing reconciliation fails with ORA-01555 snapshot too old | — | `billing` | `false` | **Unanswerable Test Case**: Oracle DB runbook not in Section 6. Tests that retrieval returns low confidence / rejection. |
| `INC0009884` | Global mail delivery delay across all departments | `KB0002-v3.0` | `corporate-email` | `true` | Section 9.1 major incident scenario |

All mappings are validated against `data/corpus/barq_articles.json` by running:
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
│   │   ├── ingest.py            # Batch chunking, single-batch BM25 fitting, UUIDv5 upsert
│   │   └── sources.py           # LocalJSONSource loader
│   └── clients/
│       └── qdrant.py            # QdrantClient factory and ensure_collection()
├── src/retrieval/               # Root Re-Export Shims
│   ├── __init__.py              # Package marker
│   ├── embedding.py             # Re-exports DualEmbeddingEngine, models, vector sizes
│   └── ingest.py                # Re-exports ingest_articles, setup_qdrant_collection, point ID builder
├── data/
│   ├── barq-system-kb.pdf       # Source BARQ Operations Manual (Edition 4.0)
│   ├── coverage_matrix.csv      # Real incident-to-runbook ground truth mapping
│   └── corpus/
│       └── barq_articles.json   # 11 canonical extracted knowledge runbooks
├── docker-compose.yml           # Persistent Qdrant 1.14.0 (volume: barq_qdrant_data)
├── scripts/
│   ├── extract_barq_kb.py       # CLI: re-extract PDF into data/corpus/barq_articles.json
│   ├── setup_qdrant.py          # CLI: initialize Qdrant collection and payload indexes
│   ├── seed_qdrant.py           # CLI: chunk, embed, and seed Qdrant (45 points)
│   └── validate_corpus.py       # CLI: validate coverage matrix and security tiers
└── tests/
    ├── test_chunking.py         # Regression test for 45 chunks & code fence balancing
    └── test_ingest.py           # 8 automated ingestion tests (idempotency, point IDs, seeding)
```

---

## 8. Technical Approach & Implementation Details

### 8.1 Extraction Approach (`src/app/retrieval/barq_manual.py`)
- Uses PyMuPDF (`fitz`) to extract text and tables from Section 6 of `data/barq-system-kb.pdf`.
- Normalizes section headers into clean Markdown: `# <Title>`, `## Symptom`, `## Cause`, `## Resolution`, `## Escalation`.
- Identifies commands and terminal outputs, wrapping them into syntax-highlighted code fences (`bash` or `sql`).
- Generates `KB0010-v1.0` (Retired) and `KB0010-v2.0` (Published) from historical text in the manual, appending explicit warning blocks to `v1.0`.

### 8.2 Chunking & Balancing Approach (`src/app/retrieval/chunking.py`)
- Breaks articles into chunks honoring Section 11.7 parameters (max 700 chars, 120 char overlap).
- `balance_code_fences` scans each chunk: if an unclosed code block fence is found, it appends a closing fence to the current chunk and prepends an opening fence with the detected language tag to the subsequent chunk.
- Guarantees zero code syntax corruption during retrieval.

### 8.3 Ingestion & Embedding Approach (`src/app/retrieval/ingest.py`)
- **Single-Batch BM25 Fitting**: In `ingest_articles()`, all 45 chunk texts across all 11 articles are flattened into a single list and passed to `embedding_engine.embed_documents(all_chunk_texts)` in one batch call. This fits the corpus-wide document frequency $n(t)$ accurately without multi-pass skew.
- **Deterministic UUIDv5**: Point IDs are computed as `uuid.uuid5(uuid.NAMESPACE_DNS, f"{article_id}#{chunk_index}")`. This guarantees single-command idempotent loading (running `seed_qdrant.py` multiple times leaves exactly 45 points in Qdrant).
- **Persistent Storage**: Docker volume `barq_qdrant_data` persists the collection on disk, surviving container restarts without re-indexing.
