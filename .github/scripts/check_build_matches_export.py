#!/usr/bin/env python3
"""Check the SDK build still reproduces the exported update set.

Issue #54. ``ai_incident_orchestrator_s1_1.xml`` is the authoritative artifact: it is
what was imported and committed on dev434590 and dev204871 (39 records, 0 collisions).
The Fluent source is only trustworthy while a build reproduces it.

SDK 4.11.2 breaks that. It marks all five processing-state choice sys_ids
``deleted: true`` in ``src/fluent/generated/keys.ts`` and mints replacements that differ
on every fresh build, so a runbook deploy would replace the choices on the instance.
4.8.0 leaves ``keys.ts`` byte-identical. This script is the assertion that keeps the two
in step, whatever the pinned version happens to be.

The ``sys_choice_set`` *container* record is deliberately not compared: the build emits
its own id for it and the export carries the one the instance created. Only the five
``sys_choice`` rows ship the values scripts depend on.

The 14 ``sys_dictionary`` records across the S1.1 and S1.3 exports are compared on
``internal_type``, ``max_length`` and ``default_value`` rather than on sys_id, because
the build mints its own dictionary ids while the exports carry the instance's.
Attributes are what matters: at 4.11.2 the build blanks ``max_length`` on 7 of the
original 13 fields without touching ``keys.ts`` or the choices, so a future SDK could
regress field definitions while passing every other check here.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ELEMENT = "x_2215032_ai_inc_0_ai_processing_state"
TABLE = "incident"

REPO = Path(__file__).resolve().parents[2]
SDK_APP = REPO / "servicenow" / "ai_incident_orchestrator" / "sdk-app"
CHOICE_FILE = f"sys_choice_{TABLE}_{ELEMENT}.xml"
# 4.8.0 writes to dist/app/update/. 4.11.2 moves the same file to
# author_elective_update/, so look in both and say which one was used rather than
# reporting a missing file when the build merely relocated it.
BUILT_CANDIDATES = (
    SDK_APP / "dist" / "app" / "update" / CHOICE_FILE,
    SDK_APP / "dist" / "app" / "author_elective_update" / CHOICE_FILE,
)
EXPORT_DIR = REPO / "servicenow" / "ai_incident_orchestrator"
CHOICE_EXPORT = EXPORT_DIR / "ai_incident_orchestrator_s1_1.xml"
S13_EXPORT = EXPORT_DIR / "ai_incident_orchestrator_s1_3.xml"
FIELD_EXPORTS = (CHOICE_EXPORT, S13_EXPORT)
# S1.3 ships as the update set; the Fluent source must target the same records so a
# deploy updates them in place instead of adding a second copy (#94).
S13_TABLES = (
    "sys_script",
    "sysevent_script_action",
    "sysevent_register",
    "sys_rest_message",
    "sys_rest_message_fn",
    "sys_rest_message_fn_headers",
    "sys_properties",
)


def _choices(root: ET.Element) -> dict[str, str]:
    """Map sys_id -> value for every sys_choice row describing our field.

    Both documents nest ``sys_choice`` and ``sys_choice_set`` inside each other, in
    opposite orders, so this walks the whole tree rather than assuming a shape.
    """
    found: dict[str, str] = {}
    for node in root.iter("sys_choice"):
        sys_id = node.findtext("sys_id")
        value = node.findtext("value")
        element = node.findtext("element") or node.get("field")
        if sys_id and value and element == ELEMENT:
            found[sys_id.strip()] = value.strip()
    return found


def _from_export(path: Path) -> dict[str, str]:
    """Choice rows live in an escaped XML document inside <payload>."""
    for update in ET.parse(path).getroot().findall("sys_update_xml"):
        if (update.findtext("name") or "") != f"sys_choice_{TABLE}_{ELEMENT}":
            continue
        payload = update.findtext("payload") or ""
        return _choices(ET.fromstring(payload))
    return {}


# Field attributes that change what the column actually is. sys_id is deliberately
# excluded: the build mints its own, the export carries the instance's.
DICT_ATTRS = ("internal_type", "max_length", "default_value")


def _fields(root: ET.Element) -> dict[str, tuple[str, ...]]:
    """Map element name -> the attributes of its sys_dictionary record."""
    found: dict[str, tuple[str, ...]] = {}
    for node in root.iter("sys_dictionary"):
        element = node.findtext("element") or node.get("element") or ""
        if not element.startswith("x_2215032_ai_inc_0_ai_"):
            continue
        found[element] = tuple((node.findtext(a) or "").strip() for a in DICT_ATTRS)
    return found


def _built_fields() -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for parent in {p.parent for p in BUILT_CANDIDATES}:
        for path in sorted(parent.glob(f"sys_dictionary_{TABLE}_*.xml")):
            found.update(_fields(ET.parse(path).getroot()))
    return found


def _exported_fields(paths: tuple[Path, ...]) -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for path in paths:
        for update in ET.parse(path).getroot().findall("sys_update_xml"):
            if (update.findtext("type") or "") != "Dictionary":
                continue
            found.update(_fields(ET.fromstring(update.findtext("payload") or "")))
    return found


def _check_fields() -> int:
    built = _built_fields()
    exported = _exported_fields(FIELD_EXPORTS)

    if not exported:
        print("::error::no sys_dictionary records found in the exported update sets")
        return 1
    if built == exported:
        print(f"\n{len(built)} field definitions match the exported update set:")
        for element in sorted(built):
            kind, length, default = built[element]
            shown = f"default={default!r}" if default else ""
            print(f"  ok  {element:<45} {kind:<16} max_length={length:<5} {shown}")
        return 0

    print("::error::the built field definitions no longer match the exported update set")
    for element in sorted(exported.keys() - built.keys()):
        print(f"  missing from build : {element}")
    for element in sorted(built.keys() - exported.keys()):
        print(f"  extra in build     : {element}")
    for element in sorted(built.keys() & exported.keys()):
        if built[element] != exported[element]:
            for attr, was, now in zip(DICT_ATTRS, exported[element], built[element], strict=True):
                if was != now:
                    print(f"  {element} {attr}: {was!r} -> {now!r}")
    return 1


def _check_s13_records() -> int:
    exported: set[tuple[str, str]] = set()
    for update in ET.parse(S13_EXPORT).getroot().findall("sys_update_xml"):
        record = next(iter(ET.fromstring(update.findtext("payload") or "")), None)
        if record is not None and record.tag in S13_TABLES:
            exported.add((record.tag, (record.findtext("sys_id") or "").strip()))

    built: set[tuple[str, str]] = set()
    for parent in {p.parent for p in BUILT_CANDIDATES}:
        for table in S13_TABLES:
            for path in parent.glob(f"{table}_*.xml"):
                sys_id = path.stem.removeprefix(f"{table}_")
                if len(sys_id) != 32:
                    continue
                record = ET.parse(path).getroot().find(table)
                if record is not None and record.get("action") == "DELETE":
                    continue
                built.add((table, sys_id))

    if built == exported:
        print(f"\n{len(built)} S1.3 records share their sys_ids with the exported update set")
        return 0
    print("::error::the S1.3 Fluent records and the S1.3 update set use different sys_ids")
    for table, sys_id in sorted(exported - built):
        print(f"  only in the export : {table} {sys_id}")
    for table, sys_id in sorted(built - exported):
        print(f"  only in the build  : {table} {sys_id}")
    return 1


def main() -> int:
    built_path = next((p for p in BUILT_CANDIDATES if p.exists()), None)
    if built_path is None:
        looked = "\n".join(f"  {p.relative_to(REPO)}" for p in BUILT_CANDIDATES)
        print(f"::error::the build produced no {CHOICE_FILE}. Looked in:\n{looked}")
        return 1

    print(f"built file: {built_path.relative_to(REPO)}")
    built = _choices(ET.parse(built_path).getroot())
    exported = _from_export(CHOICE_EXPORT)

    if not exported:
        print(f"::error::no choice records for {ELEMENT} found in {CHOICE_EXPORT.name}")
        return 1

    if built == exported:
        print(f"{len(built)} choice records match the exported update set:")
        for sys_id, value in sorted(built.items(), key=lambda kv: kv[1]):
            print(f"  ok  {value:<18} {sys_id}")
        return _check_fields() or _check_s13_records()

    print("::error::the SDK build no longer reproduces the exported update set")
    for sys_id in sorted(exported.keys() - built.keys()):
        print(f"  missing from build : {exported[sys_id]:<18} {sys_id}")
    for sys_id in sorted(built.keys() - exported.keys()):
        print(f"  minted by build    : {built[sys_id]:<18} {sys_id}")
    for sys_id in sorted(built.keys() & exported.keys()):
        if built[sys_id] != exported[sys_id]:
            print(f"  value changed      : {exported[sys_id]} -> {built[sys_id]}  {sys_id}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
