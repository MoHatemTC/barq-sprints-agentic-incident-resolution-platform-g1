#!/usr/bin/env python3
"""Check the SDK build still reproduces the exported update set's choice records.

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
EXPORT = REPO / "servicenow" / "ai_incident_orchestrator" / "ai_incident_orchestrator_s1_1.xml"


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


def main() -> int:
    built_path = next((p for p in BUILT_CANDIDATES if p.exists()), None)
    if built_path is None:
        looked = "\n".join(f"  {p.relative_to(REPO)}" for p in BUILT_CANDIDATES)
        print(f"::error::the build produced no {CHOICE_FILE}. Looked in:\n{looked}")
        return 1

    print(f"built file: {built_path.relative_to(REPO)}")
    built = _choices(ET.parse(built_path).getroot())
    exported = _from_export(EXPORT)

    if not exported:
        print(f"::error::no choice records for {ELEMENT} found in {EXPORT.name}")
        return 1

    if built == exported:
        print(f"{len(built)} choice records match the exported update set:")
        for sys_id, value in sorted(built.items(), key=lambda kv: kv[1]):
            print(f"  ok  {value:<18} {sys_id}")
        return 0

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
