"""Static checks on the ServiceNow installers: limits the instance silently enforces."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_ui_action_conditions_fit_servicenow_limit() -> None:
    # A longer condition is cut at 254 characters and then ignored: the button shows on
    # every incident (seen live on Take over / Hand back).
    live_ui = _load("servicenow_apply_live_ui")
    for action in live_ui.UI_ACTIONS:
        assert len(action["condition"]) <= 254, action["name"]


def test_every_installed_source_exists() -> None:
    live_ui = _load("servicenow_apply_live_ui")
    for spec in live_ui.SCRIPT_INCLUDES + live_ui.UI_ACTIONS:
        assert (live_ui.LIVE / spec["source"]).is_file(), spec["source"]
