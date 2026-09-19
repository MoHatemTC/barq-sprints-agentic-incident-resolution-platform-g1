"""Exported update sets must not contain records in the ServiceNow Global scope.

Installing a Global record changes platform behaviour for every user of that table,
not just for this app, and ServiceNow refuses to commit one inside a scoped update set.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPORTS = sorted(p for p in (REPO / "servicenow").rglob("*.xml") if "sdk-app" not in str(p))


def _global_records(export: Path) -> list[str]:
    """Names of records in the export whose sys_scope is Global.

    sys_scope lives inside each sys_update_xml's <payload> CDATA, so the payload is
    parsed as XML in its own right.
    """
    found: list[str] = []
    root = ET.parse(export).getroot()
    for update in root.iter("sys_update_xml"):
        name_element = update.find("name")
        payload = update.find("payload")
        if payload is None or not payload.text:
            continue
        try:
            record = ET.fromstring(payload.text)
        except ET.ParseError:
            continue
        for node in [record, *list(record)]:
            scope = node.find("sys_scope") if len(node) else None
            if scope is None:
                continue
            if (scope.get("display_value") or "").strip().lower() == "global":
                found.append(name_element.text if name_element is not None else node.tag)
    return found


def test_exports_exist() -> None:
    """Guard the guard: a rename must not turn this into a vacuous pass."""
    assert EXPORTS, "No update-set XML found under servicenow/ - has the layout changed?"


def test_no_global_scope_records_in_exports() -> None:
    found = {export.name: _global_records(export) for export in EXPORTS}
    found = {name: records for name, records in found.items() if records}
    assert not found, (
        f"Global-scope records in an exported update set: {found}. "
        "Build the record inside x_2215032_ai_inc_0 and re-export."
    )
