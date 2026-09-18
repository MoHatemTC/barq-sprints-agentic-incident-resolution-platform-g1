"""Regenerate / verify the committed ``openapi.json`` from the live application.

Usage:
    python scripts/export_openapi.py             # regenerate openapi.json
    python scripts/export_openapi.py --check     # exit 1 on drift, write nothing

The export is environment-independent: the app is constructed with placeholder
settings so the committed artifact never depends on local secrets. ``tests/
test_openapi_contract.py`` guards the committed file against drift; this script
is the remediation tool when that test fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def _export_settings() -> Settings:
    """Placeholder settings so the export never touches real secrets."""
    return Settings(  # type: ignore[call-arg]
        servicenow_instance_url="https://dev00000.service-now.com",
        servicenow_client_id="export-placeholder",
        servicenow_client_secret="export-placeholder",  # type: ignore[arg-type]
        servicenow_username="export-placeholder",
        servicenow_password="export-placeholder",  # type: ignore[arg-type]
        webhook_auth_token="export-placeholder",
        app_version="0.1.0",
    )


def build_spec() -> dict:
    return create_app(settings=_export_settings()).openapi()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only; exit 1 on drift")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args()

    target = REPO_ROOT / "openapi.json"
    rendered = json.dumps(build_spec(), indent=args.indent) + "\n"

    if args.check:
        committed = target.read_text(encoding="utf-8") if target.exists() else ""
        if committed == rendered:
            print("openapi.json is in sync with the application.")
            return 0
        print(
            "openapi.json is out of sync with the application. "
            "Run `python scripts/export_openapi.py` and commit the result."
        )
        return 1

    target.write_text(rendered, encoding="utf-8")
    print(f"Wrote {target} ({len(rendered)} bytes). "
          f"Verify with: python scripts/export_openapi.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
