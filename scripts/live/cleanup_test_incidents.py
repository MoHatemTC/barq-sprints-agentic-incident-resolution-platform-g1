"""Cancel this harness's own finished test incidents so they cannot skew later runs.

Only incidents whose short description starts with the harness label
``[BARQ-TEST-2026-10-03]``, that are not closed or cancelled yet (resolved ones included)
and older than ``--older-than`` minutes, are touched. Anything else — the team's
``[AUDIT-…]`` incidents, the older parked runs, real incidents — is never selected.
Nothing is deleted:

1. a paused AI run on the incident is rejected through the backend (operator API, the
   reason says it is test clean-up), so ServiceNow and the backend stay in agreement;
2. the incident is set to Canceled with a work note.

Open test incidents would otherwise look like a live outage to the agent's triage
(several similar open incidents in the last hour), which is correct behaviour for real
incidents and wrong for leftovers from earlier test runs.

    uv run python scripts/live/cleanup_test_incidents.py [--older-than 20] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import LABEL, Backend, P, ServiceNow  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--older-than", type=int, default=20, help="minutes")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sn, be = ServiceNow(), Backend()
    cutoff = (datetime.now(UTC) - timedelta(minutes=args.older_than)).strftime("%Y-%m-%d %H:%M:%S")
    rows = sn.query(
        "incident",
        f"short_descriptionSTARTSWITH{LABEL}^stateNOT IN7,8^sys_created_on<{cutoff}",
        "sys_id,number,short_description,state",
    )
    done = []
    for row in rows:
        if not row["short_description"].startswith(LABEL):
            continue
        rejected = None
        for run in be.executions(row["sys_id"]):
            if run.get("status") == "awaiting_approval":
                rejected = run["execution_id"]
                if not args.dry_run:
                    be.post(
                        f"/api/v1/approvals/{rejected}/decide",
                        {"decision": "rejected", "reason": "Test clean-up: harness fixture."},
                    )
        if not args.dry_run:
            sn.patch(
                "incident",
                row["sys_id"],
                {
                    # AI Enabled off first, in the same update: the conversation rule then
                    # ignores the change, so cancelling a resolved fixture is not recorded
                    # as a reopen (which would count against the knowledge article).
                    f"{P}enabled": "false",
                    "state": "8",
                    "close_code": "Solution provided",
                    "close_notes": "Cancelled: BARQ live-test fixture, cleaned up after the run.",
                    "work_notes": (
                        "Cancelled by the BARQ live-test clean-up (fixture, not a real incident)."
                    ),
                },
            )
        done.append({"number": row["number"], "rejected_run": rejected})
    print(json.dumps({"dry_run": args.dry_run, "cancelled": done}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
