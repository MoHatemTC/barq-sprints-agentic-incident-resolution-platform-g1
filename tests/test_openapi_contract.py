"""The committed ``openapi.json`` must match the live application.

``scripts/export_openapi.py`` regenerates the file and can check it with
``--check``, but nothing ran that check automatically: the script's own
docstring claimed a test file guarded the committed spec, and that file did not
exist. This is it. Without it the spec drifts silently — a route, a status code
or a field can change and the committed JSON keeps describing the old contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.export_openapi import build_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = REPO_ROOT / "openapi.json"


def test_committed_openapi_matches_the_application() -> None:
    """``openapi.json`` is byte-for-byte what the app would generate now."""
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    generated = json.loads(json.dumps(build_spec()))
    assert committed == generated, (
        "openapi.json is out of sync with the application. "
        "Regenerate it with: python scripts/export_openapi.py"
    )


def test_error_responses_are_documented() -> None:
    """A client must be able to see the failures, not only the successes.

    Every route returns its failures through the unified envelope, and a spec
    that lists only 200/202/422 tells a generated client that an auth failure
    or a conflict is undocumented. The two gates that matter most — the
    webhook and the approval decision — are pinned by name here.
    """
    spec = json.loads(COMMITTED.read_text(encoding="utf-8"))
    expectations = {
        ("/api/v1/webhook/incident", "post"): {"202", "401", "422", "503"},
        ("/api/v1/approvals/{id}/decide", "post"): {"200", "401", "403", "404", "409", "503"},
    }
    for (path, method), required in expectations.items():
        responses = spec["paths"][path][method]["responses"]
        missing = required - set(responses)
        assert not missing, f"{method.upper()} {path} does not document {sorted(missing)}"
