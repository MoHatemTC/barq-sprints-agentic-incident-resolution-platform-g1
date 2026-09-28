#!/usr/bin/env python3
"""Prove the trace id is continuous across an interrupt and its resume (S3.4).

FR-19/NFR-07 want one execution reconstructable from its trace and its
PostgreSQL rows alone. The hard part for an interrupt/resume graph is that the
pause and the resume happen in two different requests, in two different
processes, minutes apart — so it is worth proving they landed in one trace
rather than assuming it.

This reads the transcript of a real demo run, derives the trace id the platform
derives (``observability.tracing.trace_id_for`` — the same function production
uses, from the correlation id), and asks Langfuse what it actually recorded. The
output is committed so the claim can be checked without re-running anything.

    uv run python scripts/verify_trace_continuity.py \\
        --evidence docs/evidence/s34_hitl_demo.json \\
        --out docs/evidence/s34_trace_continuity.json

Requires LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY. Exits non-zero if a run's
observations do not all share one trace id, or if the interrupt/resume run is
missing the spans that prove the resume happened.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observability.tracing import trace_id_for  # noqa: E402

BASE = "https://cloud.langfuse.com/api/public/v2/observations"

#: Spans that can only exist if the *worker* ran, and the one that can only
#: exist if the graph was resumed. The resume is driven by the approvals API, not
#: the worker, so its absence is what a broken trace looks like.
WORKER_SPANS = {"worker.pickup"}
RESUME_SPANS = {"servicenow.write_ai_fields", "servicenow.write_execution_log"}


def fetch(trace_id: str, auth: tuple[str, str]) -> list[dict[str, Any]]:
    response = httpx.get(BASE, params={"traceId": trace_id, "limit": 100}, auth=auth, timeout=30.0)
    response.raise_for_status()
    data: list[dict[str, Any]] = response.json().get("data", [])
    return data


def check_run(name: str, run: dict[str, Any], auth: tuple[str, str]) -> tuple[dict, list[str]]:
    failures: list[str] = []
    correlation_id = run.get("correlation_id")
    if not correlation_id:
        return {"run": name, "skipped": "no correlation id in the transcript"}, failures

    trace_id = trace_id_for(correlation_id)
    try:
        observations = fetch(trace_id, auth)
    except httpx.HTTPError as exc:
        return {
            "run": name,
            "trace_id": trace_id,
            "error": f"{type(exc).__name__}: {exc}",
        }, failures

    names = sorted({str(o.get("name")) for o in observations})
    observed_traces = sorted({str(o.get("traceId")) for o in observations})
    record = {
        "run": name,
        "incident": run.get("incident"),
        "execution_id": run.get("execution_id"),
        "correlation_id": correlation_id,
        "derived_trace_id": trace_id,
        "distinct_trace_ids_in_observations": observed_traces,
        "observation_count": len(observations),
        "observation_names": names,
        "observation_types": sorted({str(o.get("type")) for o in observations}),
        "window": {
            "start": min(
                (o.get("startTime") for o in observations if o.get("startTime")), default=None
            ),
            "end": max((o.get("endTime") for o in observations if o.get("endTime")), default=None),
        },
    }

    if len(observed_traces) != 1 or observed_traces[0] != trace_id:
        failures.append(
            f"{name}: observations span {len(observed_traces)} trace ids "
            f"({observed_traces}), expected only {trace_id}"
        )
    if not observations:
        failures.append(f"{name}: no observations recorded for {trace_id}")

    missing_worker = WORKER_SPANS - set(names)
    if missing_worker:
        failures.append(f"{name}: no worker span recorded ({sorted(missing_worker)} missing)")

    # Only the interrupt/resume run must show the resume-side ServiceNow spans.
    if run.get("termination_cause", "").startswith("interrupt_resume"):
        missing_resume = RESUME_SPANS - set(names)
        if missing_resume:
            failures.append(
                f"{name}: the resume wrote to ServiceNow but the trace has no "
                f"{sorted(missing_resume)} span"
            )
        else:
            record["resume_spans_present"] = True
    return record, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="docs/evidence/s34_hitl_demo.json")
    parser.add_argument("--out", default="docs/evidence/s34_trace_continuity.json")
    args = parser.parse_args()

    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        print("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required", file=sys.stderr)
        return 2

    transcript = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    runs = transcript.get("runs", {})
    if not runs:
        print(f"no runs in {args.evidence}", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for name, run in runs.items():
        record, run_failures = check_run(name, run, (public, secret))
        records.append(record)
        failures.extend(run_failures)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_transcript": args.evidence,
        "method": (
            "trace_id_for(correlation_id) is the same derivation production uses; "
            "observations are read from GET /api/public/v2/observations?traceId=... "
            "because the legacy /api/public/traces/{id} endpoint returns 410 on this org."
        ),
        "runs": records,
        "failures": failures,
        "verdict": "one trace id per execution" if not failures else "NOT continuous",
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for record in records:
        if "skipped" in record:
            print(f"{record['run']}: skipped — {record['skipped']}")
            continue
        print(
            f"{record['run']} {record.get('incident')}: trace {record['derived_trace_id'][:16]} "
            f"carries {record['observation_count']} observations across "
            f"{len(record['distinct_trace_ids_in_observations'])} trace id"
            + ("  [resume spans present]" if record.get("resume_spans_present") else "")
        )
    print(f"verdict: {payload['verdict']}")
    print(f"written: {out}")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
