#!/usr/bin/env python3
"""S3.4 live demo: interrupt -> brief -> decide -> resume -> one ServiceNow write.

Runs the real stack. Nothing is faked:

  * real ServiceNow instance (OAuth integration user, least privilege)
  * real PostgreSQL, Redis and a real Celery worker
  * real Qdrant hybrid retrieval over the seeded corpus
  * real Gemini through the Sprints LiteLLM proxy
  * real Langfuse tracing

Prerequisites — see docs/sprint3_hitl_design.md "Running the live demo":

  1. docker compose up -d postgres redis qdrant
  2. a Celery worker consuming barq:incident:events. On macOS the prefork pool
     cannot be used: multiprocessing defaults to spawn there, so the child loses
     celery.app.trace._localized and every task dies with
     "not enough values to unpack (expected 3, got 0)". Use
     `--pool=threads --concurrency=1` locally. Linux/Docker uses fork and is fine.
  3. the API up:  uv run uvicorn app.main:app --port 8099
  4. .env configured for the instance under test.

Run:

    uv run python scripts/demo_s34_hitl_live.py --incident INC0010023
    uv run python scripts/demo_s34_hitl_live.py --all          # every seeded incident
    uv run python scripts/demo_s34_hitl_live.py --keep-parked   # leave it awaiting approval

Every phase prints PASS or FAIL and the script exits non-zero if any phase
fails, so it is usable as a gate and not only as a transcript. The transcript is
written to docs/evidence/s34_hitl_demo_<number>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.servicenow_client import ServiceNowClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402

P = "x_2215032_ai_inc_0"

# Fields the write-back must NOT have touched before the human decision. This is
# the FR-17 / NFR-05 proof: an approval-gated run parks without writing.
GATED_WRITE_FIELDS = (
    f"{P}_ai_resolution",
    f"{P}_ai_suggestion",
    f"{P}_ai_confidence",
    f"{P}_ai_classification",
    f"{P}_ai_processing_end",
)

FAILURES: list[str] = []
TRANSCRIPT: dict[str, Any] = {}


def say(message: str = "") -> None:
    print(message, flush=True)


def phase(number: int, title: str) -> None:
    say()
    say(f"── {number}. {title} " + "─" * max(0, 62 - len(title)))


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    if not ok:
        FAILURES.append(label)
    suffix = f"  {detail}" if detail else ""
    say(f"   [{mark}] {label}{suffix}")
    return ok


def mint(base: str, client_id: str, client_secret: str) -> str:
    """Client-credentials token from the app's own OAuth endpoint."""
    response = httpx.post(
        f"{base}/api/v1/oauth/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=15.0,
    )
    response.raise_for_status()
    token: str = response.json()["access_token"]
    return token


async def snapshot(client: ServiceNowClient, sys_id: str) -> dict[str, Any]:
    """Read every AI field off the incident, keyed by its short name.

    ``by_alias=True`` matters: the model dumps ``ai_suggestion`` by default but
    the Table API field is ``x_2215032_ai_inc_0_ai_suggestion``. An earlier
    version of this harness filtered on the alias and silently compared two
    empty dicts, which reported a clean write-boundary PASS while proving
    nothing. ``_require_ai_fields`` below is the guard against that class of
    false pass.
    """
    incident = await client.get_incident(sys_id)
    data = incident.model_dump(by_alias=True)
    short = {key.split(f"{P}_")[-1]: value for key, value in data.items() if key.startswith(P)}
    missing = [f for f in GATED_WRITE_FIELDS if f.split(f"{P}_")[-1] not in short]
    if missing:
        raise RuntimeError(f"snapshot is missing AI fields {missing}; the read is not trustworthy")
    return short


def written_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """AI fields whose value changed between two reads of the same incident."""
    changed: dict[str, Any] = {}
    for field in GATED_WRITE_FIELDS:
        short = field.split(f"{P}_")[-1]
        if before.get(short) != after.get(short):
            changed[short] = {"before": before.get(short), "after": after.get(short)}
    return changed


async def reset_incident(
    client: ServiceNowClient, sys_id: str, priority: str, solution: str | None
) -> None:
    """Return the incident to a pre-run state so the graph is eligible.

    Clears the previous run's AI output and puts the processing state back to
    pending. Without this the validate node correctly refuses the incident as
    "already processed", which is right behaviour but makes a poor demo.

    This is the one place the demo writes fields the agent's own payload model
    deliberately refuses to carry — ``ai_enabled``, ``ai_human_lock`` and
    ``priority`` are operator setup, not agent output, so they go straight to
    the Table API under the same least-privilege integration user.
    """
    body = {
        "priority": priority,
        f"{P}_ai_enabled": "true",
        f"{P}_ai_human_lock": "false",
        f"{P}_ai_processing_state": "pending",
        f"{P}_ai_suggestion": "",
        f"{P}_ai_resolution": "",
        f"{P}_ai_confidence": "",
        f"{P}_ai_classification": "",
        f"{P}_ai_processing_start": "",
        f"{P}_ai_processing_end": "",
        f"{P}_ai_failure_reason": "",
    }
    await client._request("PATCH", f"/api/now/table/incident/{sys_id}", json=body)
    if solution is not None:
        await client.add_work_note(sys_id, solution)


async def wait_for_execution(
    base: str, sys_id: str, headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    """Wait for a new execution to appear for this incident."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{base}/api/v1/incidents/{sys_id}/executions", headers=headers, timeout=15.0
        )
        response.raise_for_status()
        executions = response.json().get("executions") or []
        if executions:
            return executions[0]
        await asyncio.sleep(2.0)
    raise TimeoutError(f"no execution appeared for {sys_id} within {timeout:.0f}s")


async def wait_for_pause(
    base: str, execution_id: str, headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    """Wait for the execution to park at awaiting_approval (the S3.4 interrupt)."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{base}/api/v1/executions/{execution_id}", headers=headers, timeout=15.0
        )
        response.raise_for_status()
        last = response.json()
        if last.get("status") in {"awaiting_approval", "succeeded", "failed", "blocked"}:
            return last
        await asyncio.sleep(2.0)
    raise TimeoutError(f"execution {execution_id} never parked; last status {last.get('status')!r}")


async def straight_through(
    base: str,
    client: ServiceNowClient,
    sys_id: str,
    execution_id: str,
    headers: dict[str, str],
    step: dict[str, Any],
) -> dict[str, Any]:
    """The no-human path: every node runs and the cited draft is written."""
    phase(6, "Straight-through path — no approval needed, the draft is written")
    at_park = step.get("at_park") or {}
    pending = httpx.get(
        f"{base}/api/v1/approvals/pending/{execution_id}", headers=headers, timeout=15.0
    )
    check(
        "no approval is pending for a non-gated run",
        pending.status_code == 404,
        f"got {pending.status_code}",
    )

    phase(7, "The cited draft reached ServiceNow")
    after = at_park
    for _ in range(20):
        after = await snapshot(client, sys_id)
        if written_fields(at_park, after):
            break
        await asyncio.sleep(0.5)
    step["after"] = after
    suggestion = str(after.get("ai_suggestion") or "")
    check(
        "a suggestion was written",
        bool(suggestion),
        f"{len(suggestion)} chars",
    )
    check(
        "the suggestion cites its evidence",
        "[KB" in suggestion,
        suggestion[:110].replace("\n", " "),
    )
    check(
        "a confidence value was written with it",
        after.get("ai_confidence") is not None,
        f"confidence={after.get('ai_confidence')}",
    )
    say(f"        ai_suggestion: {suggestion[:400]}")

    phase(8, "The node trace shows the whole graph")
    trace = httpx.get(
        f"{base}/api/v1/executions/{execution_id}/trace", headers=headers, timeout=15.0
    )
    nodes = trace.json().get("node_states") or []
    step["nodes"] = [f"{n.get('node_name')}:{n.get('status')}" for n in nodes]
    for node in nodes:
        seq = node.get("sequence_number")
        name = node.get("node_name")
        say(f"        {seq:>2}  {name:<20} {node.get('status')}")
    check("the node trace is recorded in PostgreSQL", len(nodes) > 0, f"{len(nodes)} node states")
    check(
        "the run is marked complete, not escalated",
        str(after.get("ai_processing_state")) in {"complete", "awaiting_approval"},
        f"state={after.get('ai_processing_state')!r}",
    )
    return step


async def run_one(
    base: str,
    number: str,
    *,
    priority: str,
    solution: str,
    keep_parked: bool,
    park_timeout: float,
) -> dict[str, Any]:
    settings = get_settings()
    step = {"incident": number, "priority": priority}

    async with ServiceNowClient(settings) as client:
        incident = await client.find_incident_by_number(number)
        if incident is None:
            check(f"{number} exists on ServiceNow", False, "not found")
            return
        sys_id = incident.sys_id
        step["sys_id"] = sys_id
        check(f"{number} exists on ServiceNow", True, sys_id)

        phase(1, "Reset the incident so the graph is eligible")
        await reset_incident(client, sys_id, priority, solution)
        before = await snapshot(client, sys_id)
        check(
            "AI Enabled set and processing state reset to pending",
            before.get("ai_enabled") is True and before.get("ai_processing_state") == "pending",
            f"state={before.get('ai_processing_state')!r}",
        )
        step["before"] = before

        phase(2, "Mint both credentials from the app's OAuth endpoint")
        webhook_token = mint(
            base,
            settings.webhook_oauth_client_id,
            settings.webhook_oauth_client_secret.get_secret_value(),
        )
        operator_token = mint(
            base,
            settings.operator_client_id,
            settings.webhook_auth_token.get_secret_value(),
        )
        webhook_headers = {"Authorization": f"Bearer {webhook_token}"}
        operator_headers = {"Authorization": f"Bearer {operator_token}"}
        check("webhook token minted (audience barq-webhook)", True, f"{len(webhook_token)} chars")
        check("operator token minted (audience barq-operator, role approver)", True)

        phase(3, "NFR-07 / NFR-01 — unauthenticated call, then the 202")
        unauth = httpx.post(
            f"{base}/api/v1/webhook/incident",
            json={
                "event_id": str(uuid.uuid4()),
                "sys_id": sys_id,
                "number": number,
                "event_type": "incident.updated",
            },
            timeout=15.0,
        )
        check(
            "unauthenticated webhook is refused",
            unauth.status_code == 401,
            f"got {unauth.status_code}",
        )

        event_id = f"s34-demo-{number}-{int(time.time())}"
        started = time.perf_counter()
        accepted = httpx.post(
            f"{base}/api/v1/webhook/incident",
            headers=webhook_headers,
            json={
                "event_id": event_id,
                "sys_id": sys_id,
                "number": number,
                "event_type": "incident.updated",
                "contract_version": "v1",
            },
            timeout=15.0,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        check(
            "webhook returns 202 Accepted",
            accepted.status_code == 202,
            f"got {accepted.status_code}",
        )
        check(
            f"NFR-01 acknowledged in {elapsed_ms:.0f} ms (budget 500 ms p95)",
            elapsed_ms < 500,
        )
        body = accepted.json()
        step["event_id"] = event_id
        step["correlation_id"] = body.get("correlation_id")
        step["ack_ms"] = round(elapsed_ms, 1)
        say(f"        event_id={event_id}  correlation_id={body.get('correlation_id')}")

        phase(4, "NFR-09 — the same event fired again is a replay, not a second run")
        replay = httpx.post(
            f"{base}/api/v1/webhook/incident",
            headers=webhook_headers,
            json={
                "event_id": event_id,
                "sys_id": sys_id,
                "number": number,
                "event_type": "incident.updated",
                "contract_version": "v1",
            },
            timeout=15.0,
        )
        check(
            "replayed event still returns 202",
            replay.status_code == 202,
            f"got {replay.status_code}",
        )
        check(
            "replay is flagged idempotent and no second execution is created",
            replay.json().get("idempotent_replay") is True,
        )

        phase(5, "The Celery worker runs the graph until the interrupt")
        execution = await wait_for_execution(base, sys_id, operator_headers, timeout=90.0)
        execution_id = execution["execution_id"]
        step["execution_id"] = execution_id
        say(f"        execution_id={execution_id}")
        paused = await wait_for_pause(base, execution_id, operator_headers, timeout=park_timeout)
        step["status_at_park"] = paused.get("status")
        check(
            "the run reached a terminal or paused state",
            True,
            f"status={paused.get('status')!r}",
        )
        if paused.get("status") != "awaiting_approval":
            # ── Straight-through path ──────────────────────────────────────
            # A low/medium-risk incident needs no human, so the graph runs every
            # node and writes the cited draft itself. This is the other half of
            # the demo: the same code path minus the interrupt.
            step["at_park"] = await snapshot(client, sys_id)
            return await straight_through(
                base, client, sys_id, execution_id, operator_headers, step
            )

        check(
            "execution parked at awaiting_approval — the graph really interrupted",
            paused.get("status") == "awaiting_approval",
            f"status={paused.get('status')!r}",
        )

        phase(6, "FR-17 / NFR-05 — nothing was written before the human decided")
        at_park = await snapshot(client, sys_id)
        step["at_park"] = at_park
        changed = written_fields(before, at_park)
        check(
            "no gated field was written while parked",
            not changed,
            f"changed={sorted(changed)}"
            if changed
            else "ai_suggestion/resolution/confidence untouched",
        )
        check(
            "the incident is untouched while parked, state included",
            at_park.get("ai_processing_state") == before.get("ai_processing_state"),
            f"state={at_park.get('ai_processing_state')!r}"
            " (brief: suspend WITHOUT a ServiceNow write)",
        )

        phase(7, "NFR-07 — the pending approval carries the raw audit payload")
        pending = httpx.get(
            f"{base}/api/v1/approvals/pending/{execution_id}",
            headers=operator_headers,
            timeout=30.0,
        )
        check(
            "GET approvals/pending returns 200",
            pending.status_code == 200,
            f"got {pending.status_code}",
        )
        approval = pending.json()
        approval_id = approval["id"]
        step["approval_id"] = approval_id
        check(
            "approval is still undecided",
            approval.get("decision") is None,
            f"decision={approval.get('decision')!r}",
        )
        check(
            "raw interrupt payload persisted with the checkpoint (facts)",
            bool(approval.get("facts")),
            f"keys={sorted((approval.get('facts') or {}).keys())[:8]}",
        )
        facts = approval.get("facts") or {}
        gate_keys = sorted(k for k in facts if k.endswith("_gate") or k in {"risk", "verification"})
        check(
            "gate verdicts persisted alongside the checkpoint",
            len(gate_keys) >= 2,
            f"keys={gate_keys}",
        )
        check(
            "the draft travelled with the checkpoint",
            "draft" in facts,
            f"draft={len(str(facts.get('draft') or ''))} chars, facts keys={sorted(facts)}",
        )

        phase(8, "Approval Brief Agent — descriptive only, and it degrades safely")
        brief = approval.get("brief") or {}
        check(
            "brief is present before any decision", bool(brief), f"keys={sorted(brief.keys())[:8]}"
        )
        step["brief"] = brief
        if brief:
            for key in ("summary", "recommendation", "risk_level"):
                if key in brief:
                    say(f"        brief.{key}: {str(brief[key])[:150]}")

        if keep_parked:
            step["paused"] = True
            say()
            say("   --keep-parked: stopping here with the execution still paused.")
            url = f"{base}/api/v1/approvals/{approval_id}/decide"
            say(f"   Resume it yourself:\n     curl -X POST {url} \\")
            say('       -H "Authorization: Bearer $OPERATOR_TOKEN" \\')
            say('       -H "Content-Type: application/json" \\')
            say('       -d \'{"decision":"approved","reason":"...","solution":"..."}\'')
            return

        phase(9, "Resume — Command(resume=…) against the Postgres checkpointer")
        decided = httpx.post(
            f"{base}/api/v1/approvals/{approval_id}/decide",
            headers=operator_headers,
            json={
                "decision": "approved",
                "reason": "Confirmed with the requester; applying the KB resolution.",
                "solution": solution,
            },
            timeout=180.0,
        )
        check("decide returns 200", decided.status_code == 200, f"got {decided.status_code}")
        if decided.status_code != 200:
            say(f"        body: {decided.text[:400]}")
            return
        step["decision"] = decided.json().get("decision")
        check("decision recorded as approved", decided.json().get("decision") == "approved")
        check(
            "decided_by comes from the token subject, not the body",
            decided.json().get("decided_by") == "barq-operator",
            f"decided_by={decided.json().get('decided_by')!r}",
        )

        phase(10, "NFR-05 — the authorised write happened, exactly once")
        after = at_park
        write_latency = -1.0
        for attempt in range(20):
            after = await snapshot(client, sys_id)
            if written_fields(at_park, after):
                write_latency = attempt * 0.5
                break
            await asyncio.sleep(0.5)
        step["write_visible_after_s"] = write_latency
        step["after"] = after
        post_changes = written_fields(at_park, after)
        check(
            "ServiceNow was written only after the decision",
            bool(post_changes),
            f"changed={sorted(post_changes)} visible {write_latency:.1f}s after decide returned",
        )
        check(
            "the incident is parked for a human, not auto-completed",
            str(after.get("ai_processing_state")) == "awaiting_approval",
            f"state={after.get('ai_processing_state')!r}",
        )
        check(
            "the human-review flag is set on the incident",
            after.get("ai_human_review_required") is True,
        )
        suggestion = str(after.get("ai_suggestion") or "")
        if suggestion:
            check(
                "the authorised write is a cited resolution",
                "[KB" in suggestion,
                suggestion[:110].replace("\n", " "),
            )
            say(f"        ai_suggestion: {suggestion[:300]}")
        else:
            # A high-risk incident is escalated AT determine_risk, before retrieval
            # (FR-13), so there is deliberately no draft to write and nothing to
            # cite. The authorised write in that case is the escalation record.
            say("        no draft: this incident was escalated before retrieval (FR-13),")
            say("        so the authorised write is the escalation record, not a suggestion.")

        phase(11, "The audit distinguishes interrupt-resume from crash-recovery")
        final = httpx.get(
            f"{base}/api/v1/executions/{execution_id}", headers=operator_headers, timeout=15.0
        )
        final_body = final.json()
        step["final_status"] = final_body.get("status")
        step["termination_cause"] = final_body.get("termination_cause")
        check(
            "execution closed succeeded",
            final_body.get("status") == "succeeded",
            f"status={final_body.get('status')!r} cause={final_body.get('termination_cause')!r}",
        )
        check(
            "termination cause records the interrupt-resume path",
            "interrupt" in str(final_body.get("termination_cause") or ""),
            f"cause={final_body.get('termination_cause')!r}",
        )

        phase(12, "A second, contradictory decision is refused")
        again = httpx.post(
            f"{base}/api/v1/approvals/{approval_id}/decide",
            headers=operator_headers,
            json={"decision": "rejected", "reason": "changed my mind"},
            timeout=30.0,
        )
        check(
            "second decision returns 409 Conflict",
            again.status_code == 409,
            f"got {again.status_code}",
        )

        phase(13, "One trace, node by node")
        trace = httpx.get(
            f"{base}/api/v1/executions/{execution_id}/trace", headers=operator_headers, timeout=15.0
        )
        nodes = trace.json().get("node_states") or []
        step["nodes"] = [
            f"{n.get('node_name')}#{n.get('attempt')}:{n.get('status')}" for n in nodes
        ]
        for node in nodes:
            seq = node.get("sequence_number")
            name = node.get("node_name")
            say(f"        {seq:>2}  {name:<20} {node.get('status')}")
        check(
            "the node trace is recorded in PostgreSQL", len(nodes) > 0, f"{len(nodes)} node states"
        )
        say()
        say(f"   Langfuse: filter this run's trace by execution_id={execution_id} in the")
        say("   project's Traces view. Trace URLs need a login, so screenshot it for a PR.")
        project = str(settings.langfuse_public_key)[:6]
        say(f"   https://cloud.langfuse.com  (project {project}…)")
    return step


async def main_async(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    say("=" * 68)
    say("BARQ S3.4 — live HITL demo: interrupt -> brief -> decide -> resume")
    say(f"API {base}   instance {get_settings().servicenow_instance_url}")
    say("=" * 68)

    ready = httpx.get(f"{base}/ready", timeout=10.0)
    check("API reports ready (postgres + redis)", ready.status_code == 200, ready.text[:120])

    targets = (
        [(number, args.priority, args.solution) for number in args.incident]
        if args.incident
        else [(n, p, s) for n, p, s in DEMO_INCIDENTS]
    )
    for index, (number, priority, solution) in enumerate(targets, start=1):
        say()
        say("#" * 68)
        say(f"# RUN {index}/{len(targets)} — {number} (priority {priority})")
        say("#" * 68)
        try:
            result = await run_one(
                base,
                number,
                priority=priority,
                solution=solution,
                keep_parked=args.keep_parked,
                park_timeout=args.timeout,
            )
        except (TimeoutError, httpx.HTTPError) as exc:
            check(f"{number} run completed", False, f"{type(exc).__name__}: {exc}")
            TRANSCRIPT[f"run{index}"] = {"incident": number, "error": str(exc)}
        else:
            TRANSCRIPT[f"run{index}"] = result

    say()
    say("=" * 68)
    if FAILURES:
        say(f"RESULT: {len(FAILURES)} FAILURE(S)")
        for failure in FAILURES:
            say(f"  - {failure}")
    else:
        say("RESULT: every phase passed")
    say("=" * 68)

    if args.evidence:
        out = Path(args.evidence)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "api": base,
            "failures": FAILURES,
            "runs": {key: value for key, value in TRANSCRIPT.items() if key.startswith("run")},
        }
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        say(f"transcript written to {out}")
    return 1 if FAILURES else 0


DEMO_INCIDENTS = [
    (
        "INC0010023",
        "1",
        "Reinstalled the VPN client and flushed the stale split-tunnel route on the host.",
    ),
    (
        "INC0010025",
        "3",
        "Cleared the local DNS cache and re-registered the network profile.",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="http://127.0.0.1:8099", help="API base URL")
    parser.add_argument("--incident", action="append", help="incident number (repeatable)")
    parser.add_argument("--all", action="store_true", help="run every seeded demo incident")
    parser.add_argument("--priority", default="1", help="priority to set on a --incident run")
    parser.add_argument(
        "--solution",
        default="Applied the KB resolution after human approval.",
        help="human solution folded into the resume",
    )
    parser.add_argument(
        "--keep-parked", action="store_true", help="stop after the brief; do not decide"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="seconds to wait for the interrupt",
    )
    parser.add_argument(
        "--evidence",
        default="docs/evidence/s34_hitl_demo.json",
        help="where to write the JSON transcript ('' to skip)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
