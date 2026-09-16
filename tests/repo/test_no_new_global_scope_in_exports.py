"""Exported update sets must not add records in the ServiceNow Global scope.

The known Global records below are tracked for removal; anything else in Global fails.
This list only shrinks: delete an entry once the record is fixed and re-exported, and
assert zero once it is empty.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPORTS = sorted(p for p in (REPO / "servicenow").rglob("*.xml") if "sdk-app" not in str(p))

# sys_id -> why it is still here. Tracked by #48 (ACLs) and #102.
KNOWN_GLOBAL_RECORDS: dict[str, str] = {
    "sys_security_acl_a2a3e94e9f6012103b32602e9a0a1c16": "#48 - sys_journal_field create",
    "sys_security_acl_7fa32d4e9f6012103b32602e9a0a1c36": "#48 - sys_journal_field read",
    "sys_security_acl_91b7ec2cc3313010a282a539e540dd37": "#48 - incident.* write",
    "sys_security_acl_66f0fbc60a0a0b0100ce40e98ad45972": "#48 - incident.comments write",
    "sys_script_2f5038f9475f8b10c148497f316d43f5": (
        "#48 - 'AI Enforce Human Lock Safety Stop' business rule added by #32; "
        "must be rebuilt inside x_2215032_ai_inc_0 and LOCK-03 re-run"
    ),
}


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


def test_no_unlisted_global_scope_records() -> None:
    """Any Global record that is not a known, tracked exception fails the build."""
    unlisted: dict[str, list[str]] = {}
    for export in EXPORTS:
        extra = [n for n in _global_records(export) if n not in KNOWN_GLOBAL_RECORDS]
        if extra:
            unlisted[export.name] = extra

    assert not unlisted, (
        f"New Global-scope records in an exported update set: {unlisted}. "
        "The PRD rule is that nothing ships in Global - installing this changes "
        "platform security for every user of the affected tables, not just this app. "
        "Build the record inside x_2215032_ai_inc_0 and re-export. If it genuinely "
        "must be Global, add it to KNOWN_GLOBAL_RECORDS with the issue that tracks "
        "removing it."
    )


def test_known_global_list_has_no_stale_entries() -> None:
    """When a record is fixed on the PDI, its entry must be deleted, not left behind.

    A stale allowlist silently re-permits a record if it ever comes back.
    """
    present = {name for export in EXPORTS for name in _global_records(export)}
    stale = sorted(set(KNOWN_GLOBAL_RECORDS) - present)
    assert not stale, (
        f"These are no longer in any export, so remove them from KNOWN_GLOBAL_RECORDS: "
        f"{stale}. If the list is now empty, replace it with an assertion of zero."
    )
