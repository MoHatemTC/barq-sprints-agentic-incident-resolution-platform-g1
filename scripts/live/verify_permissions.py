"""Live permission test for the roles in design 2B, on the shared instance.

Each check runs as the real test user (global background script that impersonates the
user, admin session for setup only) and asks ServiceNow itself through GlideRecordSecure
and field canRead()/canWrite(), so the answers are the instance's own ACLs and roles.

Environment: as ``verify_agentic_core.py``.

    uv run python scripts/live/verify_permissions.py --out evidence.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import ServiceNow, incident_fields  # noqa: E402
from verify_conversation import AdminSession  # noqa: E402

P = "x_2215032_ai_inc_0_ai_"
OPERATOR = "x_2215032_ai_inc_0.operator"
APP_ADMIN = "x_2215032_ai_inc_0.admin"


def probe(admin: AdminSession, user: str, body: str) -> dict[str, Any]:
    """Run ``body`` as ``user``; it must build a JS object ``r`` that is printed as JSON."""
    out = admin.run_as(
        user, "var r = {};\n" + body + "\ngs.print('BARQ_JSON' + JSON.stringify(r));"
    )
    match = re.search(r"BARQ_JSON(\{.*?\})", out)
    if not match:
        raise RuntimeError(f"no result for {user}: {out[-400:]}")
    return json.loads(match.group(1))


def read_probe(sys_id: str) -> str:
    # Journal entries are checked as the form renders them for the user (display value
    # of the secure record, field security applied); field-level canRead() does not
    # apply to journal fields.
    return (
        "function can(el, write) { return el ? (write ? el.canWrite() : el.canRead()) : false; }\n"
        "function entry(field, text) {\n"
        "  return String(g.getDisplayValue(field) || '').indexOf(text) >= 0;\n"
        "}\n"
        f"var g = new GlideRecordSecure('incident'); r.read = g.get({json.dumps(sys_id)});\n"
        "if (r.read) {\n"
        "  r.comments_read = entry('comments', 'a message');\n"
        "  r.work_notes_read = entry('work_notes', 'Engineer only');\n"
        f"  r.lock_write = can(g.{P}human_lock, true);\n"
        f"  r.enabled_write = can(g.{P}enabled, true);\n"
        f"  r.resolution_write = can(g.{P}resolution, true);\n"
        "}\n"
        "r.itil = gs.hasRole('itil');\n"
        f"r.operator = gs.hasRole({json.dumps(OPERATOR)});\n"
        f"r.app_admin = gs.hasRole({json.dumps(APP_ADMIN)});"
    )


def reply_probe(sys_id: str) -> str:
    return (
        f"var g = new GlideRecordSecure('incident'); g.get({json.dumps(sys_id)});\n"
        "g.comments = 'Permission test: the caller can reply.';\n"
        "r.updated = !!g.update();"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    sn, admin = ServiceNow(), AdminSession()
    abel = sn.query("sys_user", "user_name=abel.tuter", "sys_id")[0]["sys_id"]
    own = sn.create_incident(
        incident_fields(abel, short="Permission test: caller's own ticket", description="test")
        | {f"{P}enabled": "false"}
    )["sys_id"]
    sn.patch(
        "incident",
        own,
        {"comments": "Permission test: a message to the caller.", "work_notes": "Engineer only."},
    )
    results: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "incident": own}
    seen = {
        user: probe(admin, user, read_probe(own))
        for user in ("abel.tuter", "David.Miller", "beth.anglin", "incident_operator", "barq.admin")
    }
    seen["abel.tuter"]["reply"] = probe(admin, "abel.tuter", reply_probe(own))["updated"]
    results["seen"] = seen
    checks = {
        # P1: a caller reads only their own incidents.
        "caller_reads_own_ticket": seen["abel.tuter"]["read"] is True,
        "other_user_cannot_read_it": seen["David.Miller"]["read"] is False,
        # P2: the caller sees the conversation, never the engineer-only notes.
        "caller_reads_comments": seen["abel.tuter"].get("comments_read") is True,
        "caller_can_reply": seen["abel.tuter"].get("reply") is True,
        "caller_cannot_read_work_notes": seen["abel.tuter"].get("work_notes_read") is False,
        # P3: the caller holds none of the roles the AI buttons require.
        "caller_has_no_engineer_or_approver_role": not (
            seen["abel.tuter"]["itil"] or seen["abel.tuter"]["operator"]
        ),
        "caller_cannot_lock_or_enable_ai": not (
            seen["abel.tuter"].get("lock_write") or seen["abel.tuter"].get("enabled_write")
        ),
        # P4: an engineer works incidents but cannot approve AI fixes.
        "engineer_reads_any_ticket": seen["beth.anglin"]["read"] is True,
        "engineer_reads_work_notes": seen["beth.anglin"].get("work_notes_read") is True,
        "engineer_is_not_approver": seen["beth.anglin"]["itil"]
        and not seen["beth.anglin"]["operator"],
        # Approver and BARQ admin hold their roles.
        "approver_has_operator_role": seen["incident_operator"]["operator"] is True,
        "barq_admin_has_app_admin_role": seen["barq.admin"]["app_admin"] is True,
    }
    results["checks"] = checks
    results["passed"] = all(checks.values())
    results["finished_at"] = datetime.now(UTC).isoformat()
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "passed": results["passed"]}, indent=1))
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
