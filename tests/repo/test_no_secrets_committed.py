"""#51: the repository secret scan must exist, and must actually catch things.

`CRED-01` used to report `PASS` over a narrow file list (and was then deleted
outright), so every case raised in #51 slipped through. These tests assert three
things: the scanner runs over `git ls-files` with no extension filter and no
exemptions, each of the cases named in #51 is detected, and the values that are
deliberately allowed are recorded as (file, rule, sha256) with a reason rather
than skipped by directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path("scripts/scan_repo_secrets.py")
ALLOWLIST_PATH = Path("scripts/secret_scan_allowlist.json")


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("scan_repo_secrets_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rules(text: str) -> set[str]:
    module = _load_module()
    return {f.rule for f in module.scan_text(text, "fixture", set())}


# ---------------------------------------------------------------------------
# Coverage of the scanner itself
# ---------------------------------------------------------------------------


def test_scanner_walks_git_ls_files_with_no_extension_filter() -> None:
    module = _load_module()
    tracked = module.tracked_files()
    assert "scripts/verification_report.json" in tracked, (
        "the report other checks exclude must be scanned"
    )
    assert "scripts/secret_scan_allowlist.json" in tracked
    extensions = {Path(p).suffix for p in tracked}
    for expected in (
        ".py",
        ".ts",
        ".cjs",
        ".sh",
        ".xml",
        ".md",
        ".yaml",
        ".yml",
        ".json",
        ".txt",
        "",
    ):
        assert expected in extensions


def test_binary_files_are_skipped_and_reported() -> None:
    module = _load_module()
    _, skipped = module.scan_repo()
    assert any(s.endswith(".png") for s in skipped)


def test_repository_scan_is_clean() -> None:
    module = _load_module()
    findings, _ = module.scan_repo()
    assert findings == [], "\n".join(f"{f.path}:{f.line} [{f.rule}] {f.detail}" for f in findings)


def test_allowlist_stores_digests_and_reasons_not_values() -> None:
    import json

    data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    assert data["allow"], "an empty allowlist means the fixtures were never looked at"
    for entry in data["allow"]:
        assert set(entry) == {"path", "rule", "sha256", "reason"}
        assert len(entry["sha256"]) == 64
        assert entry["reason"].strip()


# ---------------------------------------------------------------------------
# The six cases from #51
# ---------------------------------------------------------------------------


def test_case_env_file_password() -> None:
    line = "PASS" + "WORD=" + "hunter2" + "abc\n"
    assert "credential-assignment" in _rules(line)


def test_case_markdown_unquoted_client_secret() -> None:
    line = "Set the client_se" + "cret: hunter2" + "xyz in the script.\n"
    assert "credential-assignment" in _rules(line)


def test_case_xml_user_password_tag() -> None:
    line = "<user_pass" + "word>hunter2" + "xyz</user_pass" + "word>\n"
    assert "credential-tag" in _rules(line)


def test_case_typescript_literal() -> None:
    line = "const api" + 'Key = "Sup3rS3' + 'cret99";\n'
    assert "credential-assignment" in _rules(line)


def test_case_shell_curl_basic_auth() -> None:
    line = "curl -s -u svc:" + "hunter2" + "abc https://example.invalid/api\n"
    assert "inline-basic-auth" in _rules(line)


def test_case_exported_report_access_token() -> None:
    text = '{"access_' + 'token": "ghp_' + 'abcdefghijklmnopqrstuvwxyz0123"}\n'
    rules = _rules(text)
    assert "github-token" in rules and "credential-assignment" in rules


# ---------------------------------------------------------------------------
# Values that must NOT be reported
# ---------------------------------------------------------------------------


def test_code_paths_urls_and_templates_are_not_findings() -> None:
    module = _load_module()
    rejected = [
        "SecretStr",
        "${ADMIN_PASS}",
        "/api/v1/oauth/token",
        "https://dev407364.service-now.com",
        "_build_client",
        "changeme",
    ]
    for value in rejected:
        assert not module.looks_like_credential(value), value
