# Sprint 2 — Ingestion Latency / Load Report (NFR-01)

Generated: 2026-09-18 13:22 UTC by `tests/load/run_load_test.py`

## Environment

- OS: Windows 11 (AMD64)
- Python: 3.12.13
- App: local uvicorn process, `POST /api/v1/webhook/incident` against PostgreSQL 16 (`barq_s2_1_test`, migrations at head) and Redis 7 via docker compose
- Traffic: 500 requests per depth, 50 concurrent senders, HTTP keep-alive
- Queue depths are measured Redis LLEN values on `barq:incident:events` at the moment traffic was driven

## Results

| Queue depth | Requests | Success | Failures | Min (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Throughput (rps) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 500 | 500 | 0 | 45.8 | 184.0 | 403.7 | 407.5 | 417.1 | 234.8 |
| 1,000 | 500 | 500 | 0 | 37.4 | 190.7 | 269.1 | 275.6 | 279.3 | 249.1 |
| 10,000 | 500 | 500 | 0 | 36.5 | 182.9 | 237.8 | 269.4 | 279.0 | 258.2 |

## Acceptance (NFR-01)

- Budget: p95 < 500 ms at every queue depth, zero failed requests.
- depth 0: p95 = 403.7 ms, failures = 0 — **PASS**
- depth 1,000: p95 = 269.1 ms, failures = 0 — **PASS**
- depth 10,000: p95 = 237.8 ms, failures = 0 — **PASS**

## Conclusion

All depths satisfy p95 < 500 ms. Worst-case p95 was 403.7 ms at depth 0; acknowledgement latency is driven by the O(1) Redis LPUSH and bounded PostgreSQL insert, not by the number of queued work items.

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

Optionally, open-ended exploratory load with Locust:

```bash
uv pip install locust
locust -f tests/load/locustfile.py --host http://127.0.0.1:8431 \
  --users 50 --spawn-rate 10 --run-time 60s
```
