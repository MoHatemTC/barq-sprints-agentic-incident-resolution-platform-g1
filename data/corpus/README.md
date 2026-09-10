# Knowledge Base Corpus (`data/corpus/articles.json`)

## Overview
This directory contains the foundational technical knowledge base corpus for Sprint 1 (S1.4: Knowledge Retrieval Foundation). It contains **27 authored technical articles** covering realistic enterprise production incidents, error signatures, root causes, and verified resolution procedures.

All articles strictly adhere to the `app.models.knowledge.Article` Pydantic domain model and are validated before ingestion.

---

## Article Schema Specification

Each article in `articles.json` is a JSON object with the following fields:

| Field | Type | Description | Example |
|---|---|---|---|
| `base_id` | `str` | Version-less article identifier family | `KB-DB-001` |
| `version` | `str` | Semantic major.minor version (`^\d+\.\d+$`) | `2.0` |
| `article_id` | `str` | Unique compound ID (`{base_id}-v{version}`) | `KB-DB-001-v2.0` |
| `title` | `str` | Descriptive technical title (5-200 chars) | `PostgreSQL 16 max_connections Exhausted Under Pooling` |
| `short_description` | `str` | One-line summary (max 255 chars, ServiceNow limit) | `Resolve FATAL 53300 too many connections on PostgreSQL 16 pools.` |
| `category` | `str` (slug) | Controlled vocabulary category | `database` |
| `service` | `str` (slug) | Controlled vocabulary service | `postgresql` |
| `workflow_state` | `str` | Lifecycle state (`published`, `draft`, `retired`) | `published` |
| `security_level` | `str` | Audience visibility (`public`, `internal`, `restricted`) | `internal` |
| `content` | `str` | Canonical Markdown body with fenced code blocks | `## Symptom\n\nApplication logs show...` |

---

## Controlled Vocabulary & Distribution

The 27 articles span 7 core infrastructure categories:

1. **`database`** (5 articles):
   - `KB-DB-001-v1.0`: PostgreSQL 14 Connection Limit Exhaustion
   - `KB-DB-001-v2.0`: PostgreSQL 16 max_connections Under Pooling
   - `KB-DB-002-v1.0`: PostgreSQL Transaction ID Wraparound & Autovacuum Starvation
   - `KB-DB-003-v1.0`: PostgreSQL Deadlock Detected (40P01) on Batch Inserts
   - `KB-DB-004-v1.0`: PostgreSQL WAL Disk Space Starvation (PANIC checkpoint)
2. **`caching`** (5 articles):
   - `KB-CACHE-001-v1.0`: Redis OOM Command Not Allowed (Missing TTL)
   - `KB-CACHE-001-v1.1`: Redis Memory Fragmentation Evicting Celery Keys (`draft`)
   - `KB-CACHE-002-v1.0`: Redis Sentinel Split-Brain Following Network Partition
   - `KB-CACHE-003-v1.0`: Redis Replication Client Output Buffer Overflow
   - `KB-CACHE-004-v1.0`: Redis Cluster Slot Migration Hang on Large Hash Keys (`draft`)
3. **`queue`** (3 articles):
   - `KB-QUEUE-001-v1.0`: Celery Worker Task Starvation from Unbounded Prefetch
   - `KB-QUEUE-002-v1.0`: Celery Worker Lost (Exit code 137 / SIGKILL) Under Memory Pressure
   - `KB-QUEUE-003-v1.0`: RabbitMQ High Memory Watermark Alarm Blocking Publishers
4. **`networking`** (4 articles):
   - `KB-NET-001-v1.0`: NGINX 502 Bad Gateway (Socket Backlog Overflow)
   - `KB-NET-001-v2.0`: NGINX 504 Gateway Timeout on Slow Upstream Reporting
   - `KB-NET-002-v1.0`: NGINX SSL Handshake Failure (Expired Certificate Chain)
   - `KB-NET-003-v1.0`: Legacy Apache KeepAlive Worker Exhaustion (`retired`)
5. **`compute`** (6 articles):
   - `KB-API-001-v1.0`: FastAPI Async Event Loop Blocked by Synchronous I/O
   - `KB-API-002-v1.0`: Uvicorn Worker Process Killed on Gunicorn 30s Timeout
   - `KB-COMP-001-v1.0`: Docker Container Terminated with Exit Code 137 (OOMKilled)
   - `KB-COMP-002-v1.0`: Docker Daemon Inotify Watch Limit Exhausted on Linux Host
   - `KB-K8S-001-v1.0`: Kubernetes Pod CrashLoopBackOff Due to Failed Readiness Probe
   - `KB-K8S-002-v1.0`: Kubernetes Node DiskPressure Evicting Application Pods
6. **`storage`** (2 articles):
   - `KB-STOR-001-v1.0`: Linux Filesystem Inode Exhaustion with Free Disk Space (`df -i`)
   - `KB-STOR-002-v1.0`: NFS Mount Stale File Handle Error (ESTALE)
7. **`security`** (2 articles):
   - `KB-SEC-001-v1.0`: ServiceNow OAuth2 Token Expiry Buffer Failure (401 Unauthorized)
   - `KB-SEC-002-v1.0`: Internal TLS CA Certificate Verification Failure Across Microservices

---

## Disambiguation & Lifecycle Pairs

To test Sprint 2's hybrid retrieval and metadata filtering capabilities:
- **Version Disambiguation Pairs**:
  - `KB-DB-001-v1.0` (Postgres 14) vs `KB-DB-001-v2.0` (Postgres 16)
  - `KB-NET-001-v1.0` (NGINX 502) vs `KB-NET-001-v2.0` (NGINX 504)
  - `KB-CACHE-001-v1.0` (Redis OOM) vs `KB-CACHE-001-v1.1` (Redis Fragmentation)
- **Workflow State Testing**:
  - 24 `published` articles (live production answers)
  - 2 `draft` articles (`KB-CACHE-001-v1.1`, `KB-CACHE-004-v1.0`)
  - 1 `retired` article (`KB-NET-003-v1.0`)

---

## Validation & Verification

To validate that every article in `articles.json` conforms to the Pydantic schema:

```bash
uv run pytest tests/test_corpus.py -v
```
Or rebuild programmatically via:
```bash
PYTHONPATH=src uv run python scripts/build_corpus.py
```
