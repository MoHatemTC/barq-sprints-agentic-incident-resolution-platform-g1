"""Live verification of real urgency (agent.triage) on the shared system.

Each scenario uses a demo caller with no recent incidents, so the repeat rule only
applies where the scenario wants it. Admin login: labelled fixtures only. Nothing deleted.

    uv run python scripts/live/verify_triage.py --out evidence.json [--only a,b]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import Backend, ServiceNow, incident_fields, wait_for  # noqa: E402
from verify_conversation import comments, settled_after, work_notes  # noqa: E402


def fresh_callers(sn: ServiceNow, count: int) -> list[str]:
    """Demo users with no roles and no incident in the last 8 days."""
    found: list[str] = []
    users = sn.query(
        "sys_user", "active=true^user_nameLIKE.^ORDERBYDESCuser_name", "sys_id,user_name,roles"
    )
    for user in users:
        if user.get("roles") or user["user_name"] == "barq.admin":
            continue
        recent = sn.query(
            "incident",
            f"caller_id={user['sys_id']}^opened_at>javascript:gs.daysAgoStart(8)",
            "sys_id",
        )
        if not recent:
            found.append(user["user_name"])
        if len(found) >= count:
            break
    return found


def caller(sn: ServiceNow, name: str) -> str:
    return str(sn.query("sys_user", f"user_name={name}", "sys_id")[0]["sys_id"])


def reasons_of(be: Backend, sn: ServiceNow, sys_id: str) -> str:
    """Where the risk reasons are visible: the approval brief of a parked run and the
    work notes the agent wrote on the incident."""
    texts = list(work_notes(sn, sys_id))
    for run in be.executions(sys_id):
        if run.get("status") == "awaiting_approval":
            try:
                texts.append(json.dumps(be.get(f"/api/v1/approvals/pending/{run['execution_id']}")))
            except Exception:  # noqa: BLE001 - evidence only
                pass
    return "\n".join(texts)


def scenario_attack_goes_to_a_person(sn, be, names) -> dict[str, Any]:
    """S2: a reported compromise is a security incident for a person, never self-service."""
    inc = sn.create_incident(
        incident_fields(
            caller(sn, names.pop()),
            short="Files encrypted after clicking an email link",
            description=(
                "I clicked a link in an email this morning and now all my files have a "
                "strange extension and a note asking for payment."
            ),
            category="software",
        )
    )
    run = wait_for(settled_after(be, inc["sys_id"], 1), 300)
    final = sn.incident(inc["sys_id"])
    detail = reasons_of(be, sn, inc["sys_id"])
    checks = {
        "parked_for_a_person": bool(run) and run.get("status") == "awaiting_approval",
        "not_resolved": final["state"] != "Resolved",
        "no_steps_sent_to_caller": not any(
            "how to fix it" in c for c in comments(sn, inc["sys_id"])
        ),
        "security_reason_recorded": "security" in detail.lower(),
    }
    return {"incident": final["number"], "sys_id": inc["sys_id"], "run": run, "checks": checks}


def scenario_burst_is_a_likely_outage(sn, be, names) -> dict[str, Any]:
    """A6: three similar reports within minutes make a likely outage."""
    text = "The expense portal shows error 502 when I submit a claim."
    created = []
    for _ in range(3):
        created.append(
            sn.create_incident(
                incident_fields(
                    caller(sn, names.pop()),
                    short="Expense portal returns error 502 on submit",
                    description=text,
                )
            )
        )
        time.sleep(20)
    last = created[-1]
    run = wait_for(settled_after(be, last["sys_id"], 1), 300)
    final = sn.incident(last["sys_id"])
    detail = reasons_of(be, sn, last["sys_id"])
    checks = {
        "third_report_parked": bool(run) and run.get("status") == "awaiting_approval",
        "outage_reason_recorded": "likely outage" in detail,
        "not_resolved": final["state"] != "Resolved",
    }
    return {
        "incidents": [sn.incident(c["sys_id"])["number"] for c in created],
        "incident": final["number"],
        "sys_id": last["sys_id"],
        "run": run,
        "checks": checks,
    }


def scenario_p1_single_user_is_reassessed(sn, be, names) -> dict[str, Any]:
    """A5: a P1 that is one person's VPN login is handled as low risk, with the reasons."""
    inc = sn.create_incident(
        incident_fields(
            caller(sn, names.pop()),
            short="Docking station no longer charges my laptop",
            description=(
                "Since this morning my laptop does not charge when it is on my docking "
                "station; the monitors still work. Only my own desk is affected."
            ),
            category="hardware",
            impact="1",
            urgency="1",
        )
    )
    run = wait_for(settled_after(be, inc["sys_id"], 1), 300)
    final = sn.incident(inc["sys_id"])
    notes = work_notes(sn, inc["sys_id"])
    checks = {
        "handled_without_waiting_for_approval": bool(run) and run.get("status") == "succeeded",
        "reassessment_explained": any("Reassessed by BARQ AI Agent" in n for n in notes),
        "priority_field_unchanged": str(final.get("priority", "")).startswith("1"),
    }
    return {
        "incident": final["number"],
        "sys_id": inc["sys_id"],
        "run": run,
        "final": final,
        "checks": checks,
    }


def scenario_repeat_needs_approval(sn, be, names) -> dict[str, Any]:
    """A10: the same caller reporting the same problem again needs an engineer's approval."""
    who = caller(sn, names.pop())
    first = sn.create_incident(
        incident_fields(
            who,
            short="Teams microphone not detected in meetings",
            description="Teams does not detect my headset microphone in meetings.",
            category="software",
        )
    )
    wait_for(settled_after(be, first["sys_id"], 1), 300)
    # The second report comes after the first was worked, as a real repeat would.
    second = sn.create_incident(
        incident_fields(
            who,
            short="Teams microphone not detected in meetings again",
            description="Teams again does not detect my headset microphone in meetings.",
            category="software",
        )
    )
    run = wait_for(settled_after(be, second["sys_id"], 1), 300)
    final = sn.incident(second["sys_id"])
    detail = reasons_of(be, sn, second["sys_id"])
    checks = {
        "second_report_parked": bool(run) and run.get("status") == "awaiting_approval",
        "repeat_reason_recorded": "repeat from the same caller" in detail,
    }
    return {
        "incident": final["number"],
        "sys_id": second["sys_id"],
        "first": first["sys_id"],
        "run": run,
        "checks": checks,
    }


SCENARIOS = {
    "attack_goes_to_a_person": scenario_attack_goes_to_a_person,
    "burst_is_a_likely_outage": scenario_burst_is_a_likely_outage,
    "p1_single_user_is_reassessed": scenario_p1_single_user_is_reassessed,
    "repeat_needs_approval": scenario_repeat_needs_approval,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    sn, be = ServiceNow(), Backend()
    names = fresh_callers(sn, 8)
    chosen = [s for s in args.only.split(",") if s] or list(SCENARIOS)
    results: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "scenarios": {}}
    for name in chosen:
        print(f"== {name}", flush=True)
        try:
            outcome = SCENARIOS[name](sn, be, names)
        except Exception as exc:  # noqa: BLE001 - record and continue
            outcome = {"error": f"{type(exc).__name__}: {exc}", "checks": {"ran": False}}
        outcome["passed"] = all(outcome.get("checks", {}).values())
        results["scenarios"][name] = outcome
        summary = {k: outcome.get(k) for k in ("incident", "checks", "passed", "error")}
        print(json.dumps(summary), flush=True)
    results["finished_at"] = datetime.now(UTC).isoformat()
    Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return 0 if all(r["passed"] for r in results["scenarios"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
