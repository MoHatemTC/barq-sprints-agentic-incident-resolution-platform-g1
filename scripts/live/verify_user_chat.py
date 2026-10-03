"""Live verification of the users' BARQ AI page (Employee Center ?id=barq_ai).

Drives the real widget through the Service Portal API exactly as the page does, while
an admin session impersonates a fresh demo caller (setup only), so the widget's server
script runs in the app scope as that user. The agent works the created incident as
BARQ AI Agent on the shared EC2. Nothing is deleted.

    uv run python scripts/live/verify_user_chat.py --out evidence.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import Backend, P, ServiceNow, wait_for  # noqa: E402
from verify_conversation import AdminSession, settled_after  # noqa: E402
from verify_triage import fresh_callers  # noqa: E402

WIDGET = "x_2215032_ai_inc_0_barq_ai_assistant"


class Page:
    """The BARQ AI widget, called as the impersonated user."""

    def __init__(self, admin: AdminSession, sn: ServiceNow, user_sys_id: str) -> None:
        self.admin = admin
        self.url = admin.url
        portal = sn.query("sp_portal", "url_suffix=esc", "sys_id")[0]["sys_id"]
        self.instance = sn.query("sp_instance", f"sp_widget.id={WIDGET}", "sys_id")[0]["sys_id"]
        self.path = f"/api/now/sp/rectangle/{self.instance}?id=barq_ai&portal_id={portal}"
        self._call("POST", f"/api/now/ui/impersonate/{user_sys_id}", {})

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "X-UserToken": self.admin.ck,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with self.admin.opener.open(request, timeout=240) as response:
            return json.load(response)

    def act(self, **params: Any) -> dict[str, Any]:
        result = self._call("POST", self.path, params).get("result") or {}
        return result.get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    sn, be, admin = ServiceNow(), Backend(), AdminSession()
    name = fresh_callers(sn, 1)[0]
    user = sn.query("sys_user", f"user_name={name}", "sys_id,name")[0]
    page = Page(admin, sn, user["sys_id"])
    evidence: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "caller": name}

    first = page.act(action="init")
    sent = page.act(
        action="send",
        message="My Wi-Fi keeps dropping every few minutes on the 5 GHz corporate network.",
    )
    proposal = sent.get("proposal") or {}
    created = (
        page.act(
            action="create",
            proposal={
                **proposal,
                "short_description": "[BARQ-TEST-2026-10-03] "
                + str(proposal.get("short_description") or "Wi-Fi drops on 5 GHz"),
            },
        ).get("created")
        or {}
    )
    record = (
        sn.query(
            "incident",
            f"sys_id={created.get('sys_id')}",
            f"number,caller_id,{P}enabled,contact_type",
        )
        if created
        else []
    )
    run = wait_for(settled_after(be, created["sys_id"], 1), 300) if created else None
    time.sleep(5)
    opened = page.act(action="open", sys_id=created.get("sys_id")) if created else {}
    ticket = opened.get("ticket") or {}
    agent_said = [e for e in ticket.get("conversation", []) if e.get("agent")]

    # Follow-up as the caller: confirm a solved ticket, or answer the agent.
    follow: dict[str, Any] = {}
    if ticket.get("can_confirm"):
        follow = page.act(action="confirm", sys_id=created["sys_id"]).get("ticket") or {}
    elif agent_said:
        follow = (
            page.act(
                action="reply",
                sys_id=created["sys_id"],
                text="It happens only on the 5 GHz network in the office, since Monday.",
            ).get("ticket")
            or {}
        )

    others = sn.query("incident", f"caller_id!={user['sys_id']}^active=true", "sys_id")
    foreign = page.act(action="open", sys_id=others[0]["sys_id"]) if others else {}

    checks = {
        "page_loads_with_chat": first.get("chat_available") is True,
        "new_caller_sees_no_tickets": first.get("tickets") == [],
        "chat_answers": bool((sent.get("reply") or {}).get("content")),
        "problem_gets_ticket_proposal": bool(proposal.get("short_description")),
        "ticket_created_as_the_caller": bool(record)
        and record[0]["caller_id"] == user["sys_id"]
        and record[0][f"{P}enabled"] == "true",
        "agent_worked_the_ticket": bool(run),
        "agent_message_visible_in_chat": bool(agent_said),
        "no_work_notes_in_chat": all(
            "work" not in str(e.get("who", "")).lower() for e in ticket.get("conversation", [])
        ),
        "follow_up_applied": bool(follow),
        "other_users_ticket_refused": foreign.get("error") == "That ticket is not available.",
    }
    evidence.update(
        {
            "incident": created.get("number"),
            "sys_id": created.get("sys_id"),
            "reply": sent.get("reply"),
            "proposal": proposal,
            "ticket": ticket,
            "follow_up": follow,
            "run": run,
            "checks": checks,
            "passed": all(checks.values()),
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )
    Path(args.out).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {"incident": created.get("number"), "checks": checks, "passed": evidence["passed"]},
            indent=1,
        )
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
