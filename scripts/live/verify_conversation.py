"""Live verification of the connection core (design 2A) on the shared system.

The caller and the engineer act as the real test users (``abel.tuter`` and
``beth.anglin``): the admin login is used only to create labelled ``[BARQ-TEST-…]``
incidents and to run a global background script that impersonates the user, so every
ServiceNow rule sees that user as the actor. The agent works as BARQ AI Agent through
the deployed backend. Nothing is deleted.

Environment: as ``verify_agentic_core.py``. Optional CALLER_USER_NAME (abel.tuter),
ENGINEER_USER_NAME (beth.anglin).

    uv run python scripts/live/verify_conversation.py --out evidence.json [--only a,b]
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import (  # noqa: E402
    Backend,
    P,
    ServiceNow,
    incident_fields,
    wait_for,
)

LIVE = Path(__file__).resolve().parents[2] / "servicenow/ai_incident_orchestrator/live"
QUESTION_MARKER = "I need one more detail"
TERMINAL = ("succeeded", "awaiting_approval", "failed", "cancelled", "exhausted", "abandoned")


class AdminSession:
    """A logged-in admin browser session, used only to impersonate test users."""

    def __init__(self) -> None:
        self.url = os.environ["SN_INSTANCE_URL"].rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.opener.open(f"{self.url}/login.do", timeout=60).read()
        form = urllib.parse.urlencode(
            {
                "user_name": os.environ["SN_ADMIN_USER"],
                "user_password": os.environ["SN_ADMIN_PASS"],
                "sys_action": "sysverb_login",
                "sysparm_login_url": "welcome.page",
            }
        ).encode()
        self.opener.open(f"{self.url}/login.do", data=form, timeout=60).read()
        page = (
            self.opener.open(f"{self.url}/sys_remote_update_set_list.do", timeout=60)
            .read()
            .decode()
        )
        match = re.search(r"var g_ck = '([^']+)'", page)
        if not match:
            raise RuntimeError("admin login failed")
        self.ck = match.group(1)

    def run_as(self, user_name: str, body: str) -> str:
        """Run ``body`` (server JavaScript) in the global scope as ``user_name``."""
        script = (
            "var barqUser = new GlideRecord('sys_user');\n"
            f"barqUser.get('user_name', {json.dumps(user_name)});\n"
            "var barqPrevious = gs.getSession().impersonate(barqUser.getUniqueValue());\n"
            "try {\n" + body + "\n} finally { gs.getSession().impersonate(barqPrevious); }\n"
            "gs.print('BARQ_DONE');"
        )
        form = urllib.parse.urlencode(
            {
                "script": script,
                "runscript": "Run script",
                "sysparm_ck": self.ck,
                "sys_scope": "global",
                "quota_managed_transaction": "on",
            }
        ).encode()
        page = self.opener.open(f"{self.url}/sys.scripts.do", data=form, timeout=180).read()
        text = html.unescape(re.sub(r"<[^>]+>", "", page.decode("utf-8", "replace")))
        if "BARQ_DONE" not in text:
            raise RuntimeError(f"script as {user_name} failed: {text[-600:]}")
        return text

    def comment(self, user_name: str, sys_id: str, text: str) -> None:
        self.run_as(
            user_name,
            f"var inc = new GlideRecord('incident'); inc.get({json.dumps(sys_id)});\n"
            f"inc.comments = {json.dumps(text)}; inc.update();",
        )

    def hand_back(self, user_name: str, sys_id: str, instruction: str) -> None:
        """Run the real "Hand back to BARQ AI" action script as the engineer."""
        action = (LIVE / "ui_actions/hand_back_to_ai.js").read_text(encoding="utf-8")
        self.run_as(
            user_name,
            f"var current = new GlideRecord('incident'); current.get({json.dumps(sys_id)});\n"
            f"current.work_notes = {json.dumps(instruction)};\n"
            "var action = { setRedirectURL: function() {} };\n" + action,
        )


def runs(be: Backend, sys_id: str) -> list[dict[str, Any]]:
    return be.executions(sys_id)


def settled_after(be: Backend, sys_id: str, count: int) -> Callable[[], Any]:
    """The newest run once at least ``count`` runs exist and the newest has finished."""

    def check() -> Any:
        found = runs(be, sys_id)
        if len(found) >= count and found[0].get("status") in TERMINAL:
            return found[0]
        return None

    return check


def produced_a_result(be: Backend, sn: ServiceNow, sys_id: str) -> bool:
    """The newest agent run did real work (not skipped) and the incident is not left
    queued or marked in progress with nobody working on it."""
    acted = [
        r for r in runs(be, sys_id) if not str(r.get("termination_cause")).startswith("observed:")
    ]
    if not acted or str(acted[0].get("termination_cause")) == "skipped_ineligible":
        return False
    record = sn.incident(sys_id)
    ai_state = record[f"{P}processing_state"]
    waiting_caller = record["state"] == "On Hold"
    return ai_state in ("Complete", "Awaiting Approval", "Failed") or (
        ai_state == "In Progress" and waiting_caller
    )


def comments(sn: ServiceNow, sys_id: str) -> list[str]:
    return [j["value"] for j in sn.journal(sys_id) if j["element"] == "comments"]


def work_notes(sn: ServiceNow, sys_id: str) -> list[str]:
    return [j["value"] for j in sn.journal(sys_id) if j["element"] == "work_notes"]


VAGUE = "It does not work since this morning. Please help."


def scenario_ask_then_continue(sn, be, admin, caller, engineer) -> dict[str, Any]:
    """A3 + U5: vague ticket → one question, On Hold; the caller's answer continues the AI."""
    inc = sn.create_incident(
        incident_fields(caller["sys_id"], short="VPN problem", description=VAGUE)
    )
    first = wait_for(settled_after(be, inc["sys_id"], 1), 300)
    asked = sn.incident(inc["sys_id"])
    admin.comment(
        caller["user_name"],
        inc["sys_id"],
        "The VPN client says 'invalid credentials'. I changed my password yesterday.",
    )
    second = wait_for(settled_after(be, inc["sys_id"], 2), 300)
    final = sn.incident(inc["sys_id"])
    texts = comments(sn, inc["sys_id"])
    checks = {
        "first_run_finished": bool(first),
        "asked_one_question": any(QUESTION_MARKER in t for t in texts),
        "waited_on_hold": asked["state"] == "On Hold",
        "reply_queued_the_agent_again": bool(second),
        "agent_continued_to_a_result": produced_a_result(be, sn, inc["sys_id"]),
        "no_question_repeated": sum(QUESTION_MARKER in t for t in texts) <= 2,
    }
    return {
        "incident": final["number"],
        "sys_id": inc["sys_id"],
        "runs": runs(be, inc["sys_id"]),
        "final": final,
        "checks": checks,
    }


def scenario_engineer_takes_over_then_hands_back(sn, be, admin, caller, engineer) -> dict[str, Any]:
    """E5 + E6: an engineer writing to the caller takes control; hand back resumes the AI."""
    inc = sn.create_incident(
        incident_fields(caller["sys_id"], short="VPN keeps failing", description=VAGUE)
    )
    wait_for(settled_after(be, inc["sys_id"], 1), 300)
    admin.comment(engineer["user_name"], inc["sys_id"], "Hi, I am looking at this for you now.")
    taken = wait_for(
        lambda: (r := sn.incident(inc["sys_id"]))[f"{P}human_lock"] == "true" and r, 60
    )
    observed = wait_for(
        lambda: any(
            str(r.get("termination_cause")) == "observed:incident.engineer_replied"
            for r in runs(be, inc["sys_id"])
        ),
        120,
    )
    count = len(runs(be, inc["sys_id"]))
    admin.hand_back(
        engineer["user_name"],
        inc["sys_id"],
        "The caller changed their password yesterday; use the VPN credential article.",
    )
    resumed = wait_for(settled_after(be, inc["sys_id"], count + 1), 300)
    final = sn.incident(inc["sys_id"])
    notes = work_notes(sn, inc["sys_id"])
    checks = {
        "engineer_reply_locked_the_agent_out": bool(taken),
        "stood_down_note": any("stood down" in n for n in notes),
        "engineer_reply_recorded": bool(observed),
        "hand_back_note_with_instruction": any(
            "Handed back to BARQ AI Agent" in n and "Instruction" in n for n in notes
        ),
        "agent_resumed": bool(resumed) and produced_a_result(be, sn, inc["sys_id"]),
        "lock_cleared": final[f"{P}human_lock"] == "false",
    }
    return {
        "incident": final["number"],
        "sys_id": inc["sys_id"],
        "runs": runs(be, inc["sys_id"]),
        "final": final,
        "checks": checks,
    }


def scenario_caller_reopens_ai_resolution(sn, be, admin, caller, engineer) -> dict[str, Any]:
    """U8/D: a reply on an AI-resolved incident reopens it for an engineer, with the AI fix."""
    inc = sn.create_incident(
        incident_fields(
            caller["sys_id"],
            short="VPN invalid credentials after password reset",
            description=(
                "Since this morning the VPN client says invalid credentials although I can "
                "reach the internet. I reset my password yesterday."
            ),
        )
    )
    wait_for(settled_after(be, inc["sys_id"], 1), 300)
    resolved = sn.incident(inc["sys_id"])
    admin.comment(caller["user_name"], inc["sys_id"], "I tried all the steps and it still fails.")
    reopened = wait_for(
        lambda: (r := sn.incident(inc["sys_id"]))["state"] == "In Progress" and r, 60
    )
    observed = wait_for(
        lambda: any(
            str(r.get("termination_cause")) == "observed:incident.reopened"
            for r in runs(be, inc["sys_id"])
        ),
        120,
    )
    final = sn.incident(inc["sys_id"])
    checks = {
        "was_resolved_by_agent": resolved["state"] == "Resolved"
        and resolved["resolved_by"] == "BARQ AI Agent",
        "reopened_to_in_progress": bool(reopened),
        "engineer_has_it": final[f"{P}human_lock"] == "true"
        and final[f"{P}human_review_required"] == "true",
        "hand_over_note": any(
            "after a BARQ AI Agent resolution" in n for n in work_notes(sn, inc["sys_id"])
        ),
        "reopen_recorded": bool(observed),
    }
    return {
        "incident": final["number"],
        "sys_id": inc["sys_id"],
        "runs": runs(be, inc["sys_id"]),
        "final": final,
        "checks": checks,
    }


SCENARIOS = {
    "ask_then_continue": scenario_ask_then_continue,
    "engineer_takes_over_then_hands_back": scenario_engineer_takes_over_then_hands_back,
    "caller_reopens_ai_resolution": scenario_caller_reopens_ai_resolution,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    sn, be, admin = ServiceNow(), Backend(), AdminSession()
    users = {}
    for role, default in (("caller", "abel.tuter"), ("engineer", "beth.anglin")):
        name = os.environ.get(f"{role.upper()}_USER_NAME", default)
        record = sn.query("sys_user", f"user_name={name}", "sys_id,user_name")[0]
        users[role] = record
    chosen = [s for s in args.only.split(",") if s] or list(SCENARIOS)
    results: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "scenarios": {}}
    for name in chosen:
        print(f"== {name}", flush=True)
        try:
            outcome = SCENARIOS[name](sn, be, admin, users["caller"], users["engineer"])
        except Exception as exc:  # noqa: BLE001 - record and continue
            outcome = {"error": f"{type(exc).__name__}: {exc}", "checks": {"ran": False}}
        outcome["passed"] = all(outcome.get("checks", {}).values())
        results["scenarios"][name] = outcome
        print(
            json.dumps({k: outcome.get(k) for k in ("incident", "checks", "passed", "error")}),
            flush=True,
        )
    results["finished_at"] = datetime.now(UTC).isoformat()
    Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return 0 if all(r["passed"] for r in results["scenarios"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
