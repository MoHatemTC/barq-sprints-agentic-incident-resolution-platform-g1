"""Apply the BARQ live ServiceNow UI (bridge + incident actions) to an instance.

Setup only: uses the instance admin login from the environment (SN_INSTANCE_URL,
SN_ADMIN_USER, SN_ADMIN_PASS); the agent never uses it. Idempotent: records are
matched by name in the x_2215032_ai_inc_0 scope and updated in place. Every change is
appended to a JSON-lines manifest (--manifest) with the previous values, so it can be
reverted by deactivating what was created and restoring what was changed.

    uv run python scripts/servicenow_apply_live_ui.py --manifest change_manifest.jsonl
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCOPE = "51a63bbf738bc7502aedfed25ab8b789"
OPERATOR = "gs.hasRole('x_2215032_ai_inc_0.operator')"
PAUSED = "current.x_2215032_ai_inc_0_ai_processing_state == 'awaiting_approval'"
LIVE = Path(__file__).resolve().parents[1] / "servicenow/ai_incident_orchestrator/live"

SCRIPT_INCLUDES = [
    {
        "name": "BarqBackend",
        "api_name": "x_2215032_ai_inc_0.BarqBackend",
        "access": "package_private",
        "client_callable": "false",
        "active": "true",
        "description": "Server-side bridge from ServiceNow to the BARQ backend.",
        "source": "BarqBackend.js",
    }
]

UI_ACTIONS = [
    {
        "name": "Approve AI fix",
        "action_name": "barq_approve_ai_fix",
        "condition": f"{PAUSED} && {OPERATOR}",
        "hint": "Approve the paused BARQ AI run. Edit AI Resolution first to approve your own fix.",
        "order": "100",
        "source": "ui_actions/approve_ai_fix.js",
    },
    {
        "name": "Reject AI fix",
        "action_name": "barq_reject_ai_fix",
        "condition": f"{PAUSED} && {OPERATOR}",
        "hint": "Reject the paused BARQ AI run; nothing is applied.",
        "order": "110",
        "source": "ui_actions/reject_ai_fix.js",
    },
    {
        "name": "Take over from AI",
        "action_name": "barq_take_over_from_ai",
        "condition": (
            "current.x_2215032_ai_inc_0_ai_human_lock != true && current.active == true && "
            "(gs.hasRole('itil') || " + OPERATOR + ")"
        ),
        "hint": "Lock BARQ AI Agent out of this incident and handle it yourself.",
        "order": "120",
        "source": "ui_actions/take_over_from_ai.js",
    },
]

#: Superseded actions: deactivated, never deleted (they edited fields only, or embedded
#: the operator secret in script text).
RETIRE = ["Approve AI Suggestion", "Refuse AI Suggestion", "Approve & Capture to KB"]


class Instance:
    def __init__(self, url: str, user: str, password: str, manifest: Path) -> None:
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.manifest = manifest

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": self.auth,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https base
            return json.load(resp).get("result")

    def find(self, table: str, query: str, fields: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"sysparm_query": query, "sysparm_fields": fields})
        return self.request("GET", f"/api/now/table/{table}?{q}") or []

    def log(self, entry: dict[str, Any]) -> None:
        entry["at"] = datetime.now(UTC).isoformat()
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def upsert(self, table: str, query: str, fields: dict[str, Any]) -> str:
        existing = self.find(table, query, "sys_id," + ",".join(k for k in fields if k != "script"))
        if existing:
            sys_id = existing[0]["sys_id"]
            before = {k: v for k, v in existing[0].items() if k != "sys_id"}
            self.request("PATCH", f"/api/now/table/{table}/{sys_id}", fields)
            self.log({"op": "update", "table": table, "sys_id": sys_id, "before": before})
            return str(sys_id)
        created = self.request("POST", f"/api/now/table/{table}", fields)
        self.log({"op": "create", "table": table, "sys_id": created["sys_id"]})
        return str(created["sys_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    instance = Instance(
        os.environ["SN_INSTANCE_URL"],
        os.environ["SN_ADMIN_USER"],
        os.environ["SN_ADMIN_PASS"],
        args.manifest,
    )
    for spec in SCRIPT_INCLUDES:
        fields = {k: v for k, v in spec.items() if k != "source"}
        fields["script"] = (LIVE / spec["source"]).read_text(encoding="utf-8")
        fields["sys_scope"] = SCOPE
        sys_id = instance.upsert(
            "sys_script_include", f"name={spec['name']}^sys_scope={SCOPE}", fields
        )
        print(f"script include {spec['name']}: {sys_id}")
    for spec in UI_ACTIONS:
        fields = {k: v for k, v in spec.items() if k != "source"}
        fields.update(
            {
                "script": (LIVE / spec["source"]).read_text(encoding="utf-8"),
                "table": "incident",
                "active": "true",
                "form_button": "true",
                "client": "false",
                "sys_scope": SCOPE,
            }
        )
        sys_id = instance.upsert(
            "sys_ui_action", f"name={spec['name']}^table=incident^sys_scope={SCOPE}", fields
        )
        print(f"ui action {spec['name']}: {sys_id}")
    for name in RETIRE:
        for record in instance.find(
            "sys_ui_action", f"name={name}^table=incident^sys_scope={SCOPE}", "sys_id,active"
        ):
            if record["active"] == "true":
                instance.request(
                    "PATCH", f"/api/now/table/sys_ui_action/{record['sys_id']}", {"active": "false"}
                )
                instance.log(
                    {
                        "op": "update",
                        "table": "sys_ui_action",
                        "sys_id": record["sys_id"],
                        "before": {"active": "true", "name": name},
                    }
                )
                print(f"retired ui action {name}: {record['sys_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
