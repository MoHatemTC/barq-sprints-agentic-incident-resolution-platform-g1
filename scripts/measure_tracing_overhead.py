#!/usr/bin/env python3
"""Measure what Langfuse tracing adds to one incident execution (S2.5).

Runs the compiled eleven-node graph many times with the dependencies mocked, so
the only difference between the modes is tracing:

- ``off``      — no Langfuse client (the no-op tracer)
- ``memory``   — the real Langfuse client, spans exported to memory: the full
                 in-process cost (span creation, masking, serialisation, batching)
- ``langfuse`` — the real client exporting to the configured Langfuse
                 (only when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set)

The model and ServiceNow calls are mocked and return instantly, so the result is
the absolute cost of tracing one execution; docs/sprint2_tracing_and_agent.md
relates it to a real run, which is dominated by three model calls.

    uv run python scripts/measure_tracing_overhead.py --runs 300
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# The benchmark reuses the unit-test fakes (tests/agent_support.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.graph import build_graph, run_graph  # noqa: E402
from agent.state import EventPayload  # noqa: E402
from observability.tracing import Tracer, TracingSettings, build_tracer  # noqa: E402
from tests.agent_support import (  # noqa: E402
    VPN,
    FakeServiceNow,
    event_for,
    make_deps,
    sdk_llm,
)


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def measure(tracer: Tracer, runs: int, flush_each: bool) -> list[float]:
    event = EventPayload.model_validate(event_for(VPN))
    timings = []
    for i in range(runs):
        deps = make_deps(tracer=tracer, servicenow=FakeServiceNow(), llm=sdk_llm(tracer))
        graph = build_graph(deps)
        correlation = f"overhead-{uuid.uuid4()}"
        start = time.perf_counter()
        with (
            tracer.span("worker.pickup", correlation_id=correlation, as_type="agent"),
            tracer.trace_attributes(
                correlation_id=correlation, incident_number=VPN["number"], execution_id=str(i)
            ),
        ):
            run_graph(
                graph,
                event,
                execution_id=str(uuid.uuid4()),
                correlation_id=correlation,
                attempt=1,
                deps=deps,
            )
        if flush_each:
            tracer.flush()
        timings.append((time.perf_counter() - start) * 1000)
    return timings


def report(name: str, timings: list[float], baseline: list[float] | None) -> str:
    p50, p95 = statistics.median(timings), percentile(timings, 95)
    delta = ""
    if baseline is not None:
        delta = f"{p50 - statistics.median(baseline):+.2f} ms"
    return f"| {name} | {len(timings)} | {p50:.2f} | {p95:.2f} | {delta} |"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()

    modes: list[tuple[str, Callable[[], Tracer], bool]] = [("off", lambda: Tracer(None), False)]

    memory_exporter = InMemorySpanExporter()

    def memory_tracer() -> Tracer:
        return build_tracer(
            TracingSettings(
                tracing_enabled=True,
                langfuse_public_key=f"pk-lf-bench-{uuid.uuid4()}",
                langfuse_secret_key="sk-lf-bench",
            ),
            span_exporter=memory_exporter,
            tracer_provider=TracerProvider(),
        )

    modes.append(("memory (full in-process cost)", memory_tracer, True))
    live = TracingSettings()
    if live.configured:
        modes.append(("langfuse (async export)", lambda: build_tracer(live), False))

    rows = []
    baseline: list[float] | None = None
    spans_per_run = 0
    for name, factory, flush_each in modes:
        tracer = factory()
        measure(tracer, args.warmup, flush_each)
        timings = measure(tracer, args.runs, flush_each)
        rows.append(report(name, timings, baseline))
        if baseline is None:
            baseline = timings
        tracer.flush()
        if name.startswith("memory"):
            spans_per_run = len(memory_exporter.get_finished_spans()) // (args.runs + args.warmup)
        tracer.shutdown()

    print("| Mode | Runs | p50 ms | p95 ms | p50 overhead |")
    print("|---|---|---|---|---|")
    print("\n".join(rows))
    if spans_per_run:
        print(f"\nspans per execution: {spans_per_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
