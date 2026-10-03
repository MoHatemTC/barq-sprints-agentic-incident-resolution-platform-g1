"""Live check of the engineers' BARQ AI page: card and buttons per role and incident state.

Each user is a real demo user, impersonated by an admin session (setup only), and the
incident form is loaded exactly as ServiceNow renders it for them. Read-only: pages are
opened, nothing is pressed or changed.

    uv run python scripts/live/verify_engineer_page.py --out evidence.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import LABEL, P, ServiceNow  # noqa: E402
from verify_conversation import AdminSession  # noqa: E402

ENGINEER, APPROVER, CALLER = "beth.anglin", "barq.approver", "abel.tuter"


def page_as(sn: ServiceNow, user: str, sys_id: str) -> dict[str, Any]:
    admin = AdminSession()
    user_id = sn.query("sys_user", f"user_name={user}", "sys_id")[0]["sys_id"]
    request = urllib.request.Request(
        f"{admin.url}/api/now/ui/impersonate/{user_id}",
        data=b"{}",
        method="POST",
        headers={"X-UserToken": admin.ck, "Content-Type": "application/json"},
    )
    admin.opener.open(request, timeout=60).read()
    html = (
        admin.opener.open(f"{admin.url}/incident.do?sys_id={sys_id}", timeout=120)
        .read()
        .decode("utf-8", "replace")
    )
    buttons = sorted(
        {b for b in re.findall(r'id="(barq_[a-z_]+?)"', html) if not b.endswith("_bottom")}
        - {"barq_engineer"}
    )
    chip = re.search(r'class="barq-chip">([^<]+)<', html)
    return {
        "view": "barq_engineer" if "barq_engineer" in html else "other",
        "card": chip.group(1) if chip else None,
        "buttons": buttons,
    }


def newest(sn: ServiceNow, query: str) -> dict[str, Any] | None:
    rows = sn.query(
        "incident",
        f"short_descriptionSTARTSWITH{LABEL}^{query}^ORDERBYDESCsys_created_on",
        "sys_id,number,caller_id.user_name",
    )
    return rows[0] if rows else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    sn = ServiceNow()
    incidents = {
        "parked": newest(sn, f"active=true^{P}processing_state=awaiting_approval"),
        "fix_for_engineer": newest(
            sn, f"active=true^state!=6^{P}processing_state=complete^{P}human_review_required=true"
        ),
        "resolved_by_ai": newest(sn, f"state=6^{P}processing_state=complete"),
        "cancelled": newest(sn, "state=8"),
    }
    seen: dict[str, dict[str, Any]] = {}
    for name, incident in incidents.items():
        if not incident:
            continue
        seen[name] = {
            "incident": incident["number"],
            ENGINEER: page_as(sn, ENGINEER, incident["sys_id"]),
            APPROVER: page_as(sn, APPROVER, incident["sys_id"]),
        }
    parked = seen.get("parked", {})
    checks = {
        "engineer_lands_on_barq_page": parked.get(ENGINEER, {}).get("view") == "barq_engineer",
        "parked_card_says_waiting_for_approval": parked.get(ENGINEER, {}).get("card")
        == "Waiting for an engineer to approve",
        "engineer_cannot_approve": parked.get(ENGINEER, {}).get("buttons")
        == ["barq_take_over_from_ai"],
        "approver_can_approve_reject_take_over": parked.get(APPROVER, {}).get("buttons")
        == ["barq_approve_ai_fix", "barq_reject_ai_fix", "barq_take_over_from_ai"],
        "fix_for_engineer_offers_hand_back_only": seen.get("fix_for_engineer", {})
        .get(ENGINEER, {})
        .get("buttons")
        == ["barq_hand_back_to_ai"],
        "resolved_by_ai_offers_take_over_only": seen.get("resolved_by_ai", {})
        .get(ENGINEER, {})
        .get("buttons")
        == ["barq_take_over_from_ai"],
        "cancelled_offers_nothing": seen.get("cancelled", {}).get(ENGINEER, {}).get("buttons")
        == [],
    }
    if parked:
        caller = page_as(sn, CALLER, incidents["parked"]["sys_id"])  # type: ignore[index]
        seen["parked"][CALLER] = caller
        checks["caller_gets_no_barq_buttons"] = caller["buttons"] == []
    evidence = {
        "at": datetime.now(UTC).isoformat(),
        "pages": seen,
        "checks": checks,
        "passed": all(checks.values()),
    }
    Path(args.out).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "passed": evidence["passed"]}, indent=1))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
