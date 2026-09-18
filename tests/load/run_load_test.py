"""NFR-01 load/latency benchmark for the incident ingestion webhook.

Drives sustained concurrent traffic against a running application and measures the
202-acknowledgement latency at multiple Redis queue depths (0, 1,000, 10,000) to
prove p95 < 500 ms independent of queue depth (LPUSH is O(1)).

Usage:
    # terminal 1 - start the app against the isolated test database
    POSTGRES_DB=barq_s2_1_test python -m uvicorn app.main:app --port 8431

    # terminal 2 - run the benchmark (fails clearly if infra is unreachable)
    python tests/load/run_load_test.py --depths 0 1000 10000

Results are written to ``docs/sprint2_latency_report.md``. Exit code 0 only when
every depth satisfies p95 < 500 ms. This is a load tool, not a pytest test; it is
not collected by ``pytest -q``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE = "barq:incident:events"
PREFILL_MARKER = "barq-loadtest-prefill"
P95_BUDGET_MS = 500.0
VALID_SYS_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@dataclass
class DepthResult:
    depth: int
    requests: int
    success: int
    failure: int
    min_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    throughput_rps: float

    @property
    def passed(self) -> bool:
        return self.p95_ms < P95_BUDGET_MS and self.failure == 0


def _percentile(sorted_samples: list[float], pct: float) -> float:
    if not sorted_samples:
        return float("nan")
    index = min(round(pct / 100 * len(sorted_samples)) - 1, len(sorted_samples) - 1)
    return sorted_samples[max(index, 0)]


def _env(key: str, default: str = "") -> str:
    return {**dotenv_values(REPO_ROOT / ".env"), **os.environ}.get(key, default)


async def _prefill_queue(redis, depth: int, queue_name: str = QUEUE) -> int:
    """LPUSH dummy items until the queue holds ``depth`` entries; returns added count."""
    current = await redis.llen(queue_name)
    added = 0
    dummy = json.dumps({"marker": PREFILL_MARKER})
    while current + added < depth:
        batch = min(2000, depth - current - added)
        pipeline = redis.pipeline(transaction=False)
        for _ in range(batch):
            pipeline.lpush(queue_name, dummy)
        await pipeline.execute()
        added += batch
    return added


async def _cleanup_queue(
    redis, prefill_added: int, event_ids: list[str], queue_name: str = QUEUE
) -> None:
    if prefill_added:
        await redis.lrem(queue_name, 0, json.dumps({"marker": PREFILL_MARKER}))
    for event_id in event_ids:
        await redis.lrem(queue_name, 0, event_id)


async def _run_depth(
    base_url: str,
    redis,
    depth: int,
    total_requests: int,
    concurrency: int,
    queue_name: str = QUEUE,
) -> DepthResult:
    await redis.delete(queue_name)
    if depth > 0:
        await _prefill_queue(redis, depth, queue_name)
    observed_depth = await redis.llen(queue_name)

    payload_template = {
        "sys_id": VALID_SYS_ID,
        "number": "INC0042567",
        "event_type": "incident.created",
        "contract_version": "v1",
    }
    headers = {"Authorization": f"Bearer {_env('WEBHOOK_AUTH_TOKEN')}"}
    conn_pool_size = max(100, concurrency * 2)
    limits = httpx.Limits(max_connections=conn_pool_size, max_keepalive_connections=conn_pool_size)

    async with httpx.AsyncClient(base_url=base_url, timeout=15.0, limits=limits) as client:
        for _ in range(concurrency):
            await client.post(
                "/api/v1/webhook/incident",
                json={**payload_template, "event_id": str(uuid.uuid4())},
                headers=headers,
            )

        semaphore = asyncio.Semaphore(concurrency)
        latencies: list[float] = []
        failures = 0
        pushed_event_ids: list[str] = []

        async def one_request() -> None:
            nonlocal failures
            event_id = str(uuid.uuid4())
            payload = {**payload_template, "event_id": event_id}
            async with semaphore:
                start = time.perf_counter()
                try:
                    resp = await client.post(
                        "/api/v1/webhook/incident",
                        json=payload,
                        headers=headers,
                    )
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    if resp.status_code == 202:
                        latencies.append(elapsed_ms)
                        pushed_event_ids.append(event_id)
                    else:
                        failures += 1
                        msg = resp.text[:120]
                        print(f"    ! depth={depth}: unexpected {resp.status_code}: {msg}")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"    ! depth={depth}: request failed: {exc}")

        wall_start = time.perf_counter()
        await asyncio.gather(*(one_request() for _ in range(total_requests)))
        wall_s = time.perf_counter() - wall_start

    sorted_lat = sorted(latencies)
    result = DepthResult(
        depth=observed_depth,
        requests=total_requests,
        success=len(latencies),
        failure=failures,
        min_ms=sorted_lat[0] if sorted_lat else float("nan"),
        p50_ms=_percentile(sorted_lat, 50),
        p95_ms=_percentile(sorted_lat, 95),
        p99_ms=_percentile(sorted_lat, 99),
        max_ms=sorted_lat[-1] if sorted_lat else float("nan"),
        throughput_rps=total_requests / wall_s if wall_s else float("nan"),
    )

    await redis.delete(queue_name)
    return result


async def _redis_client():
    import redis.asyncio as aioredis

    password = _env("REDIS_PASSWORD") or None
    client = aioredis.Redis(
        host=_env("REDIS_HOST", "localhost"),
        port=int(_env("REDIS_PORT", "6379")),
        password=password,
        decode_responses=True,
    )
    await client.ping()
    return client


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8431")
    parser.add_argument("--depths", nargs="+", type=int, default=[0, 1000, 10000])
    parser.add_argument("--requests-per-depth", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument(
        "--queue", default=QUEUE, help="Target Redis queue key (default: barq:incident:events)"
    )
    parser.add_argument(
        "--allow-non-local",
        action="store_true",
        help="Explicitly permit running destructive benchmark against non-localhost target",
    )
    parser.add_argument(
        "--report",
        default=str(REPO_ROOT / "docs" / "sprint-2" / "sprint-2.1" / "sprint2_latency_report.md"),
    )
    args = parser.parse_args()

    # Safety guard: prevent accidental execution against production or shared remote targets
    parsed_target = urllib.parse.urlparse(args.base_url)
    is_local_target = parsed_target.hostname in (
        "localhost", "127.0.0.1", "::1", "0.0.0.0", "testserver"
    )
    if not is_local_target and not args.allow_non_local:
        print(
            f"LOAD TEST SAFETY ERROR: Target '{args.base_url}' is not a local test environment.\n"
            "This benchmark clears and mutates the Redis queue. To prevent discarding queued "
            "production/shared incidents, execution is refused on remote targets. "
            "Pass --allow-non-local to override."
        )
        return 2

    # Fail clearly when infrastructure is unavailable (never silently pass).
    try:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0) as probe:
            ready = await probe.get("/ready")
            ready.raise_for_status()
    except Exception as exc:
        print(
            f"LOAD TEST FAILURE: application not ready at {args.base_url} ({exc}).\n"
            "Ensure the app, PostgreSQL, and Redis are running."
        )
        return 2

    try:
        redis = await _redis_client()
    except Exception as exc:
        print(f"LOAD TEST FAILURE: Redis not reachable ({exc}). Start docker compose redis.")
        return 2

    print(f"Load target: {args.base_url}  queue={args.queue}  concurrency={args.concurrency}  "
          f"requests/depth={args.requests_per_depth} (warmed up)")
    results: list[DepthResult] = []
    for depth in args.depths:
        print(f"  depth={depth:>6} ... running")
        results.append(
            await _run_depth(
                args.base_url,
                redis,
                depth,
                args.requests_per_depth,
                args.concurrency,
                queue_name=args.queue,
            )
        )
        r = results[-1]
        print(
            f"  depth={r.depth:>6}  ok={r.success}/{r.requests}  fail={r.failure}  "
            f"p50={r.p50_ms:.1f}ms p95={r.p95_ms:.1f}ms p99={r.p99_ms:.1f}ms "
            f"max={r.max_ms:.1f}ms  {r.throughput_rps:.1f} rps"
        )
    await redis.aclose()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(args, results), encoding="utf-8")
    print(f"Report written to {report_path}")

    failed = [r for r in results if not r.passed]
    for r in failed:
        print(
            f"ACCEPTANCE FAILURE at depth {r.depth}: p95={r.p95_ms:.1f}ms "
            f"(budget {P95_BUDGET_MS:.0f}ms), failures={r.failure}"
        )
    return 1 if failed else 0


def _render_report(args: argparse.Namespace, results: list[DepthResult]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Sprint 2 — Ingestion Latency / Load Report (NFR-01)",
        "",
        f"Generated: {now} by `tests/load/run_load_test.py`",
        "",
        "## Environment",
        "",
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"- Python: {platform.python_version()}",
        "- App: local uvicorn process, `POST /api/v1/webhook/incident` against "
        "PostgreSQL 16 (`barq_s2_1_test`, migrations at head) and Redis 7 via docker compose",
        f"- Traffic: {args.requests_per_depth} requests per depth, "
        f"{args.concurrency} concurrent senders, HTTP keep-alive",
        "- Queue depths are measured Redis LLEN values on `barq:incident:events` "
        "at the moment traffic was driven",
        "",
        "## Results",
        "",
        "| Queue depth | Requests | Success | Failures | Min (ms) | p50 (ms) | p95 (ms) "
        "| p99 (ms) | Max (ms) | Throughput (rps) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.depth:,} | {r.requests} | {r.success} | {r.failure} | {r.min_ms:.1f} "
            f"| {r.p50_ms:.1f} | {r.p95_ms:.1f} | {r.p99_ms:.1f} | {r.max_ms:.1f} "
            f"| {r.throughput_rps:.1f} |"
        )
    lines += [
        "",
        "## Acceptance (NFR-01)",
        "",
        f"- Budget: p95 < {P95_BUDGET_MS:.0f} ms at every queue depth, zero failed requests.",
    ]
    for r in results:
        verdict = "PASS" if r.passed else "FAIL"
        lines.append(
            f"- depth {r.depth:,}: p95 = {r.p95_ms:.1f} ms, failures = {r.failure} — **{verdict}**"
        )
    all_pass = all(r.passed for r in results)
    lines += [
        "",
        "## Conclusion",
        "",
    ]
    if all_pass:
        worst = max(results, key=lambda r: r.p95_ms)
        lines.append(
            f"All depths satisfy p95 < {P95_BUDGET_MS:.0f} ms. Worst-case p95 was "
            f"{worst.p95_ms:.1f} ms at depth {worst.depth:,}; acknowledgement latency is "
            "driven by the O(1) Redis LPUSH and bounded PostgreSQL insert, not by the "
            "number of queued work items."
        )
    else:
        lines.append(
            "One or more queue depths exceeded the p95 budget or recorded failures — "
            "see the acceptance lines above."
        )
    lines += [
        "",
        "## Reproducing",
        "",
        "```bash",
        "# 1. Infrastructure (PostgreSQL + Redis)",
        "docker compose up -d postgres redis",
        "",
        "# 2. Isolated benchmark database with migrations applied",
        "BARQ_DATABASE_URL=postgresql+asyncpg://postgres:<pw>@localhost:5434/barq_s2_1_test \\",
        "  alembic upgrade head",
        "",
        "# 3. Application under test",
        "POSTGRES_DB=barq_s2_1_test python -m uvicorn app.main:app --port 8431",
        "",
        "# 4. Benchmark",
        "python tests/load/run_load_test.py --depths 0 1000 10000 \\",
        "  --requests-per-depth 500 --concurrency 50",
        "```",
        "",
        "Optionally, open-ended exploratory load with Locust:",
        "",
        "```bash",
        "uv pip install locust",
        "locust -f tests/load/locustfile.py --host http://127.0.0.1:8431 \\",
        "  --users 50 --spawn-rate 10 --run-time 60s",
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
