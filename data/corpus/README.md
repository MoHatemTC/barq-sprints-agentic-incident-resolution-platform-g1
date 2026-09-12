# BARQ Operations Knowledge Base Corpus (`data/corpus/barq_articles.json`)

## Overview
This directory contains the canonical technical knowledge base corpus extracted directly from Section 6 (pp. 17–24) of the **BARQ Systems IT Service Operations Manual (Edition 4.0)**. It contains **10 article numbers and 11 distinct records** (`KB0001` through `KB0010`), including versioned lifecycle entries.

All articles strictly adhere to the `app.models.knowledge.Article` Pydantic domain model and are validated before vector ingestion into Qdrant.

---

## Article Schema Specification

Each article in `barq_articles.json` is a JSON object with the following fields:

| Field | Type | Description | Example |
|---|---|---|---|
| `article_number` | `str` | Article identifier (`^KB\d{4}$`) | `KB0001` |
| `version` | `str` | Semantic major.minor version (`^\d+\.\d+$`) | `2.0` |
| `title` | `str` | Descriptive technical title (5-200 chars) | `VPN AUTHENTICATION FAILS AFTER A PASSWORD CHANGE` |
| `short_description` | `str` | One-line summary (max 255 chars, ServiceNow limit) | `Resolve VPN authentication failure following domain password update.` |
| `category` | `str` (slug) | Controlled vocabulary category (`network`, `software`, `hardware`, `inquiry`) | `network` |
| `service` | `str` (slug) | Controlled vocabulary service | `corporate-vpn` |
| `workflow_state` | `str` | Lifecycle state (`published`, `draft`, `retired`) | `published` |
| `security_level` | `str` | Audience visibility (`public`, `internal`, `restricted`) | `internal` |
| `body` | `str` | Canonical Markdown body (`## Symptom`, `## Cause`, `## Resolution`, `## Escalation`) | `## Symptom\n\nUser cannot authenticate...` |
| `owner` | `str \| null` | Team or department owning the article | `Collaboration Services` |
| `author` | `str \| null` | Author name | `O. Sabry` |
| `reviewed_on` | `str \| null` | Date of last operational review | `02 Feb 2026` |
| `related_records` | `list[str]` | Associated incidents, changes, problems, or known errors | `["INC0010023"]` |
| `sys_id` | `str \| null` | ServiceNow sys_id populated post-publish seam | `null` |

---

## Knowledge Base Inventory & Distribution

The 11 extracted records span 4 core ITIL categories across 9 enterprise services:

| Number | Version | State | Security | Service | Category | Title |
|---|---|---|---|---|---|---|
| `KB0001` | `2.0` | `published` | `internal` | `corporate-vpn` | `network` | VPN AUTHENTICATION FAILS AFTER A PASSWORD CHANGE |
| `KB0002` | `3.0` | `published` | `internal` | `corporate-email` | `software` | OUTLOOK SHOWS DISCONNECTED AND NO MAIL IS DELIVERED |
| `KB0003` | `2.0` | `published` | `internal` | `file-services` | `network` | MAPPED SHARED DRIVE IS MISSING AFTER SIGN-IN |
| `KB0004` | `1.0` | `published` | `restricted` | `print-services` | `hardware` | PRINT JOBS QUEUE BUT NOTHING PRINTS |
| `KB0005` | `4.0` | `published` | `internal` | `identity` | `inquiry` | ACCOUNT IS LOCKED AFTER REPEATED FAILED SIGN-INS |
| `KB0006` | `3.0` | `published` | `internal` | `identity` | `inquiry` | MULTI-FACTOR AUTHENTICATION AFTER A LOST OR REPLACED DEVICE |
| `KB0007` | `2.0` | `published` | `restricted` | `endpoint` | `hardware` | LAPTOP PERFORMANCE DEGRADES AFTER A SYSTEM UPDATE |
| `KB0008` | `1.0` | `published` | `restricted` | `sap-erp` | `software` | SAP GUI CONNECTION TIMES OUT WITH RFC_ERROR_COMMUNICATION |
| `KB0009` | `2.0` | `published` | `internal` | `corporate-wifi` | `network` | WI-FI DROPS REPEATEDLY ON THE 5 GHZ CORPORATE NETWORK |
| `KB0010` | `1.0` | `retired` | `restricted` | `order-processing` | `software` | ORDER SERVICE CONNECTION POOL EXHAUSTION (Historical outage) |
| `KB0010` | `2.0` | `published` | `restricted` | `order-processing` | `software` | ORDER SERVICE CONNECTION POOL EXHAUSTION (Emergency change procedure) |

---

## Lifecycle Disambiguation (`KB0010`)

`KB0010` tests version filtering and disambiguation:
- **`KB0010-v1.0` (Retired)**: Recommended restarting the application server upon connection pool exhaustion. On 14 March 2026, applying this advice dropped in-flight orders and triggered Major Incident `INC0009884` (a 40-minute Tier 1 outage).
- **`KB0010-v2.0` (Published)**: The active operational standard. Strictly warns *"Do not restart the application server"* and mandates emergency change `CHG0030455` for connection pool draining.

Vector retrieval applies a mandatory published-only filter during incident response:
```python
Filter(must=[FieldCondition(key="workflow_state", match=MatchValue(value="published"))])
```

---

## Ground Truth Coverage Matrix

`data/coverage_matrix.csv` maps incidents to articles for retrieval evaluation. It is a **standard machine-readable CSV** — no comment lines; any plain `csv.DictReader`/pandas reader sees exactly the incident rows (25 scenarios: 13 from the manual, 12 synthetic).

Columns beyond the basics:

| Column | Meaning |
|---|---|
| `source` | `manual` (real BARQ incidents) or `synthetic` (authored scenarios grounded in the real corpus) |
| `forbidden_article_ids` | Articles that must **not** appear in a correct retrieval for this incident. Surfacing one fails the eval — e.g. the retired `KB0010-v1.0` is forbidden on the pool-exhaustion incidents (manual §11.5: retired revisions are *removed before ranking, not ranked low*) |

---

## Validation & Verification

To validate that every article in `barq_articles.json` conforms to the Pydantic schema and ground-truth coverage matrix:

```bash
pytest tests/retrieval/test_corpus.py -v
uv run python scripts/validate_corpus.py
```
