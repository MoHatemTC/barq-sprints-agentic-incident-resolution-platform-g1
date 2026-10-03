"""Install the users' BARQ AI page: an Employee Center page (?id=barq_ai) with one widget.

Setup only (admin login from SN_INSTANCE_URL / SN_ADMIN_USER / SN_ADMIN_PASS). Records are
created or updated in place in the app scope and logged to the manifest. Undo: deactivate
the page (``--disable`` sets the page to admin-only), nothing else depends on it.

    uv run python scripts/servicenow_apply_user_chat.py --manifest m.jsonl [--disable]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from servicenow_apply_engineer_view import Instance  # noqa: E402

SCOPE = "51a63bbf738bc7502aedfed25ab8b789"
WIDGET_DIR = (
    Path(__file__).resolve().parents[1]
    / "servicenow/ai_incident_orchestrator/live/widgets/barq_ai_assistant"
)
WIDGET_ID = "x_2215032_ai_inc_0_barq_ai_assistant"
MENU_LABEL = "BARQ AI"
PAGE_ID = "barq_ai"


def read(name: str) -> str:
    return (WIDGET_DIR / name).read_text(encoding="utf-8")


def apply(instance: Instance) -> None:
    widget = instance.upsert(
        "sp_widget",
        f"id={WIDGET_ID}",
        {
            "id": WIDGET_ID,
            "name": "BARQ AI Assistant",
            "template": read("template.html"),
            "client_script": read("client.js"),
            "script": read("server.js"),
            "css": read("style.scss"),
            "public": "false",
            "sys_scope": SCOPE,
            "description": "Users' chat with BARQ AI: questions, tickets, replies, confirm.",
        },
    )
    page = instance.upsert(
        "sp_page",
        f"id={PAGE_ID}",
        {
            "id": PAGE_ID,
            "title": "BARQ AI",
            "short_description": "Chat with BARQ AI",
            # This page is the chat: hide the portal's own Virtual Agent launcher here only.
            "css": ".sp-ac-root { display: none !important; }",
            "public": "false",
            "roles": "",
            "sys_scope": SCOPE,
        },
    )
    container = instance.upsert(
        "sp_container", f"sp_page={page}", {"sp_page": page, "order": "1", "width": "container"}
    )
    row = instance.upsert(
        "sp_row", f"sp_container={container}", {"sp_container": container, "order": "1"}
    )
    column = instance.upsert(
        "sp_column", f"sp_row={row}", {"sp_row": row, "order": "1", "size": "12"}
    )
    instance.upsert(
        "sp_instance",
        f"sp_column={column}^sp_widget={widget}",
        {"sp_column": column, "sp_widget": widget, "order": "1", "active": "true"},
    )
    # Employee Center top menu (next to "Get support"): one item, removed by --disable.
    menu = instance.find("sp_portal", "url_suffix=esc", "sp_rectangle_menu")[0]["sp_rectangle_menu"]
    item = instance.upsert(
        "sp_rectangle_menu_item",
        f"sp_rectangle_menu={menu}^label={MENU_LABEL}",
        {
            "sp_rectangle_menu": menu,
            "label": MENU_LABEL,
            "type": "url",
            "url": f"?id={PAGE_ID}",
            "order": "100",
            "active": "true",
            "sys_scope": SCOPE,
        },
    )
    # Employee Center draws the second header row from its navigation items; this one
    # shows the menu item beside "Get support".
    nav = instance.find(
        "sn_ex_sp_portal_extensible_navigation_item", "menu_title=Get support^position=right"
    )[0]
    navigation = instance.find(
        "sn_ex_sp_portal_extensible_navigation_item",
        f"sys_id={nav['sys_id']}",
        "portal_extensible_navigation",
    )[0]["portal_extensible_navigation"]
    instance.upsert(
        "sn_ex_sp_portal_extensible_navigation_item",
        f"menu={item}",
        {
            "portal_extensible_navigation": navigation,
            "navigation_item_type": "menu",
            "menu": item,
            "menu_title": MENU_LABEL,
            "render_as": "menu",
            "position": "right",
            "order": "160",
            "active": "true",
            # Created in Employee Center's own scope (its table refuses other scopes).
        },
    )
    print(f"widget {widget}; page {page} -> /esc?id={PAGE_ID}; menu item {item}")


def disable(instance: Instance) -> None:
    for page in instance.find("sp_page", f"id={PAGE_ID}"):
        instance.request("PATCH", f"/api/now/table/sp_page/{page['sys_id']}", {"roles": "admin"})
        instance.log(
            {"op": "update", "table": "sp_page", "sys_id": page["sys_id"], "before": {"roles": ""}}
        )
        print(f"page {PAGE_ID} limited to admins")
    for item in instance.find("sp_rectangle_menu_item", f"label={MENU_LABEL}^sys_scope={SCOPE}"):
        instance.request(
            "PATCH", f"/api/now/table/sp_rectangle_menu_item/{item['sys_id']}", {"active": "false"}
        )
        instance.log(
            {
                "op": "update",
                "table": "sp_rectangle_menu_item",
                "sys_id": item["sys_id"],
                "before": {"active": "true"},
            }
        )
        print(f"menu item {item['sys_id']} hidden")
        for nav in instance.find(
            "sn_ex_sp_portal_extensible_navigation_item", f"menu={item['sys_id']}"
        ):
            instance.request(
                "PATCH",
                f"/api/now/table/sn_ex_sp_portal_extensible_navigation_item/{nav['sys_id']}",
                {"active": "false"},
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    instance = Instance(args.manifest)
    if args.disable:
        disable(instance)
    else:
        apply(instance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
