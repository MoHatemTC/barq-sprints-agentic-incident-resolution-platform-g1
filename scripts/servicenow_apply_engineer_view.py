"""Install the engineers' incident page: form view "BARQ AI" with the BARQ AI card.

Setup only (admin login from SN_INSTANCE_URL / SN_ADMIN_USER / SN_ADMIN_PASS). The
Default view is not touched: this adds a separate view and a view rule that opens
incidents in it for users with the ``itil`` role. Every record is created or updated in
place and logged to the manifest.

    uv run python scripts/servicenow_apply_engineer_view.py --manifest m.jsonl
    uv run python scripts/servicenow_apply_engineer_view.py --manifest m.jsonl --enable
    uv run python scripts/servicenow_apply_engineer_view.py --manifest m.jsonl --disable

Installing leaves the view rule off, so the page can be checked first with
``incident.do?sysparm_view=barq_engineer``. ``--enable`` makes it the engineers' default;
``--disable`` switches it off again (everyone opens the Default view, and the BARQ AI view
stays available from the form's View menu).

One page, no tabs. Kept because something depends on it: category and AI Enabled
(the agent is triggered by them), state / hold reason / impact / urgency / priority and
the resolution fields (stock mandatory rules for Resolve, On Hold and priority changes;
the stock client script shows the resolution fields only when resolving), comments and
work notes (the conversation), AI Resolution (Approve AI fix sends an edited fix).
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
P = "x_2215032_ai_inc_0_ai_"
VIEW = "barq_engineer"
LIVE = Path(__file__).resolve().parents[1] / "servicenow/ai_incident_orchestrator/live"
MACRO = "barq_ai_card"

ELEMENTS: list[tuple[str, str]] = [
    (f"{MACRO}.xml", "formatter"),
    (".begin_split", ".begin_split"),
    ("number", ""),
    ("caller_id", ""),
    ("category", ""),
    ("business_service", ""),
    ("cmdb_ci", ""),
    (f"{P}enabled", ""),
    (".split", ".split"),
    ("state", ""),
    ("hold_reason", ""),
    ("impact", ""),
    ("urgency", ""),
    ("priority", ""),
    ("assignment_group", ""),
    ("assigned_to", ""),
    (".end_split", ".end_split"),
    ("short_description", ""),
    ("description", ""),
    (f"{P}resolution", ""),
    ("close_code", ""),
    ("close_notes", ""),
    ("comments", ""),
    ("work_notes", ""),
    ("activity.xml", "formatter"),
]
RELATED_LISTS = ["incident.parent_incident"]

VIEW_RULE_SCRIPT = """(function overrideView(view, is_list) {
    // Engineers open incidents in the BARQ AI view (one page, AI card on top) unless
    // they asked for another view. Lists are not affected.
    if (is_list) return;
    if ((view == '' || view == 'default') && gs.hasRole('itil'))
        answer = 'barq_engineer';
})(view, is_list);"""


class Instance:
    def __init__(self, manifest: Path) -> None:
        self.url = os.environ["SN_INSTANCE_URL"].rstrip("/")
        token = base64.b64encode(
            f"{os.environ['SN_ADMIN_USER']}:{os.environ['SN_ADMIN_PASS']}".encode()
        ).decode()
        self.auth = f"Basic {token}"
        self.manifest = manifest

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": self.auth,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https base
            raw = resp.read()
        return json.loads(raw).get("result") if raw else None

    def find(self, table: str, query: str, fields: str = "sys_id") -> list[dict[str, Any]]:
        q = urllib.parse.urlencode(
            {
                "sysparm_query": query,
                "sysparm_fields": fields,
                "sysparm_limit": 500,
                "sysparm_exclude_reference_link": "true",
            }
        )
        return self.request("GET", f"/api/now/table/{table}?{q}") or []

    def log(self, entry: dict[str, Any]) -> None:
        entry["at"] = datetime.now(UTC).isoformat()
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def upsert(self, table: str, query: str, fields: dict[str, Any]) -> str:
        existing = self.find(table, query, "sys_id")
        if existing:
            sys_id = str(existing[0]["sys_id"])
            self.request("PATCH", f"/api/now/table/{table}/{sys_id}", fields)
            self.log({"op": "update", "table": table, "sys_id": sys_id})
            return sys_id
        created = self.request("POST", f"/api/now/table/{table}", fields)
        self.log({"op": "create", "table": table, "sys_id": created["sys_id"]})
        return str(created["sys_id"])

    def delete(self, table: str, sys_id: str) -> None:
        self.request("DELETE", f"/api/now/table/{table}/{sys_id}")
        self.log({"op": "delete", "table": table, "sys_id": sys_id})


def apply(instance: Instance) -> None:
    instance.upsert(
        "sys_ui_macro",
        f"name={MACRO}^sys_scope={SCOPE}",
        {
            "name": MACRO,
            "xml": (LIVE / "ui_macros/barq_ai_card.xml").read_text(encoding="utf-8"),
            "active": "true",
            "sys_scope": SCOPE,
            "description": "BARQ AI card at the top of the engineers' incident page.",
        },
    )
    formatter = instance.upsert(
        "sys_ui_formatter",
        f"formatter={MACRO}.xml^table=incident",
        {
            "name": "BARQ AI card",
            "formatter": f"{MACRO}.xml",
            "table": "incident",
            "type": "formatter",
            "active": "true",
            "sys_scope": SCOPE,
        },
    )
    view = instance.upsert("sys_ui_view", f"name={VIEW}", {"name": VIEW, "title": "BARQ AI"})
    section = instance.upsert(
        "sys_ui_section",
        f"name=incident^view={view}",
        {
            "name": "incident",
            "view": view,
            "caption": "",
            "title": "false",
            "header": "false",
            "sys_scope": SCOPE,
        },
    )
    for row in instance.find("sys_ui_element", f"sys_ui_section={section}"):
        instance.delete("sys_ui_element", row["sys_id"])
    for position, (element, kind) in enumerate(ELEMENTS):
        fields: dict[str, Any] = {
            "sys_ui_section": section,
            "element": element,
            "position": str(position),
        }
        if kind:
            fields["type"] = kind
        if element == f"{MACRO}.xml":
            fields["sys_ui_formatter"] = formatter
        instance.request("POST", "/api/now/table/sys_ui_element", fields)
    form = instance.upsert(
        "sys_ui_form", f"name=incident^view={view}", {"name": "incident", "view": view}
    )
    instance.upsert(
        "sys_ui_form_section",
        f"sys_ui_form={form}^sys_ui_section={section}",
        {"sys_ui_form": form, "sys_ui_section": section, "position": "0"},
    )
    related = instance.upsert(
        "sys_ui_related_list", f"name=incident^view={view}", {"name": "incident", "view": view}
    )
    for position, name in enumerate(RELATED_LISTS):
        instance.upsert(
            "sys_ui_related_list_entry",
            f"list_id={related}^related_list={name}",
            {"list_id": related, "related_list": name, "position": str(position)},
        )
    rule = instance.upsert(
        "sysrule_view",
        "table=incident^name=BARQ AI - engineers open incidents in the BARQ AI view",
        {
            "name": "BARQ AI - engineers open incidents in the BARQ AI view",
            "table": "incident",
            "advanced": "true",
            "script": VIEW_RULE_SCRIPT,
            "active": "false",
            "overrides_user_preference": "false",
            "device_type": "browser",
            "sys_scope": SCOPE,
        },
    )
    print(
        json.dumps(
            {
                "view": view,
                "section": section,
                "form": form,
                "formatter": formatter,
                "view_rule": rule,
                "elements": len(ELEMENTS),
            }
        )
    )


def switch_rule(instance: Instance, on: bool) -> None:
    for rule in instance.find(
        "sysrule_view", "table=incident^name=BARQ AI - engineers open incidents in the BARQ AI view"
    ):
        value = "true" if on else "false"
        instance.request(
            "PATCH", f"/api/now/table/sysrule_view/{rule['sys_id']}", {"active": value}
        )
        instance.log(
            {
                "op": "update",
                "table": "sysrule_view",
                "sys_id": rule["sys_id"],
                "before": {"active": "false" if on else "true"},
            }
        )
        print(f"view rule {'on' if on else 'off'}: {rule['sys_id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", type=Path, required=True)
    switch = parser.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true", help="switch the view rule on")
    switch.add_argument("--disable", action="store_true", help="switch the view rule off")
    args = parser.parse_args()
    instance = Instance(args.manifest)
    if args.enable or args.disable:
        switch_rule(instance, on=args.enable)
    else:
        apply(instance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
