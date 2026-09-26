# Sprint 2 — Ingestion Latency / Load Report (NFR-01)

Generated: 2026-09-18 07:42 UTC by `tests/load/run_load_test.py`

> [!NOTE]
> **This is one run of a benchmark that was executed several times with different
> results.** The sweep below (500 requests per depth, 3 depths, concurrency 50) was run
> repeatedly and each run produced its own numbers. The other recorded runs are
> [`docs/sprint2_latency_report.md`](../../sprint2_latency_report.md),
> [`docs/sprint-2/sprint-2.1/verification_evidence.md`](verification_evidence.md),
> [`docs/sprint-2/sprint-2.1/sprint2_latency_report.md`](sprint2_latency_report.md) and
> [`docs/sprint2_ingestion_design.md`](../../sprint2_ingestion_design.md). **No run is
> designated authoritative** and nothing records which one supersedes which, so do not
> present this file's figures as *the* latency and never splice numbers from two runs into
> one table. This is the fastest of the five; the slowest recorded 403.7 ms at depth 0.
>
> Captured on **Windows 11 (AMD64)** with Python 3.12.13. Reproduction steps are POSIX;
> the closing "higher concurrency or depth" block is PowerShell and its POSIX equivalent
> is:
>
> ```bash
> uv run python tests/load/run_load_test.py --base-url http://127.0.0.1:8000 \
>   --depths 0 1000 10000 --requests-per-depth 1000 --concurrency 100
> ```

## Environment

- OS: Windows 11 (AMD64)
- Python: 3.12.13
- App: local uvicorn process, `POST /api/v1/webhook/incident` against PostgreSQL 16 (`barq_s2_1_test`, migrations at head) and Redis 7 via docker compose
- Traffic: 500 requests per depth, 50 concurrent senders, HTTP keep-alive
- Queue depths are measured Redis LLEN values on `barq:incident:events` at the moment traffic was driven

## Results

| Queue depth | Requests | Success | Failures | Min (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Throughput (rps) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 500 | 500 | 0 | 40.8 | 181.6 | 237.5 | 269.9 | 274.1 | 262.2 |
| 1,000 | 500 | 500 | 0 | 27.0 | 186.2 | 229.6 | 269.0 | 279.5 | 255.2 |
| 10,000 | 500 | 500 | 0 | 27.7 | 199.7 | 253.0 | 271.7 | 276.1 | 240.7 |

## Acceptance (NFR-01)

- Budget: p95 < 500 ms at every queue depth, zero failed requests.
- depth 0: p95 = 237.5 ms, failures = 0 — **PASS**
- depth 1,000: p95 = 229.6 ms, failures = 0 — **PASS**
- depth 10,000: p95 = 253.0 ms, failures = 0 — **PASS**

## Conclusion

All depths satisfy p95 < 500 ms. Worst-case p95 was 253.0 ms at depth 10,000; acknowledgement latency is driven by the O(1) Redis LPUSH and bounded PostgreSQL insert, not by the number of queued work items.

## Reproducing

```bash
# 1. Infrastructure (PostgreSQL + Redis)
docker compose up -d postgres redis

# 2. Isolated benchmark database with migrations applied
BARQ_DATABASE_URL=postgresql+asyncpg://postgres:<pw>@localhost:5434/barq_s2_1_test \
  alembic upgrade head

# 3. Application under test
POSTGRES_DB=barq_s2_1_test python -m uvicorn app.main:app --port 8431

# 4. Benchmark
python tests/load/run_load_test.py --depths 0 1000 10000 \
  --requests-per-depth 500 --concurrency 50
```

Optionally, run with higher concurrency or depth:

```powershell
uv run python tests/load/run_load_test.py --base-url http://127.0.0.1:8000 `
  --depths 0 1000 10000 --requests-per-depth 1000 --concurrency 100
```
