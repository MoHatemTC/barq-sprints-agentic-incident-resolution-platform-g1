"""Tests for publish_manual_sections: dry-run default, alias/retired skipping,
resumable report, and the semantic body verification."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from tests.publishing.conftest import KB_SYS_ID

SCRIPT = Path("scripts/publish_manual_sections.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("publish_manual_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _with_kb_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "servicenow_kb_id", KB_SYS_ID)


def test_dry_run_is_the_default_and_writes_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _with_kb_id(monkeypatch)
    module = _load_module()
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["publish_manual_sections.py", "--report", str(report)])
    exit_code = module.main()

    assert exit_code == 0
    data = json.loads(report.read_text())
    assert data["dry_run"] is True
    assert len(data["results"]) == 0
    assert len(data["failed"]) == 0
    # 10 Section-6 aliases + 1 archived scan are skipped; index 6.1 produces
    # no article at all.
    reasons = " ".join(s["reason"] for s in data["skipped"])
    assert "alias of existing corpus article KB0001-v2.0" in reasons
    assert any("retired" in s["reason"] for s in data["skipped"])
    assert data["manifest_sha256"], "the report pins the reviewed manifest"


def test_publish_run_is_idempotent_and_reports_sys_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake,  # in-memory ServiceNow from publishing/conftest.py
) -> None:
    _with_kb_id(monkeypatch)
    module = _load_module()
    built = fake.build_client()
    monkeypatch.setattr(module, "ServiceNowKBClient", lambda settings: built)

    report = tmp_path / "report.json"
    argv = ["publish_manual_sections.py", "--report", str(report), "--allow-writes"]
    monkeypatch.setattr("sys.argv", argv)
    assert module.main() == 0

    data = json.loads(report.read_text())
    assert data["dry_run"] is False
    assert len(data["failed"]) == 0
    assert len(data["results"]) == 64, "64 published manual units expected"
    first = {r["article_id"]: r for r in data["results"]}
    assert first["KB2065-v1.0"]["outcome"] == "created"
    assert first["KB2065-v1.0"]["sys_id"]
    assert first["KB2065-v1.0"]["stored_body_sha256"]

    # A re-run is the retry path: same manifest hash, same source IDs, no duplicates.
    monkeypatch.setattr("sys.argv", argv)
    assert module.main() == 0
    data2 = json.loads(report.read_text())
    assert data2["manifest_sha256"] == data["manifest_sha256"]
    assert all(r["outcome"] == "unchanged" for r in data2["results"])
    assert len(fake.rows) == 64, "re-running must not create duplicate rows"
    assert len(data2.get("history", [])) == 1
    assert data2["history"][0]["results"], "previous run results preserved in history"


def test_verify_stored_rejects_truncated_body() -> None:
    from app.models.knowledge import Article, SecurityLevel, WorkflowState
    from app.publishing.payload import build_kb_payload
    from app.publishing.servicenow_kb import ServiceNowWriteRejectedError, _verify_stored

    article = Article(
        article_number="KB2001",
        version="1.0",
        title="Section 1.1 title",
        short_description="One-line summary.",
        category="reference",
        service="knowledge-base",
        workflow_state=WorkflowState.PUBLISHED,
        security_level=SecurityLevel.INTERNAL,
        body="Step one and step two with full detail before anything is removed.",
    )
    sent = build_kb_payload(article, KB_SYS_ID)
    stored = {**sent, "sys_id": "sys1"}
    _verify_stored(stored, sent, "KB2001-v1.0")  # identical content passes

    truncated = {**sent, "text": sent["text"].replace(" before anything is removed", "")}
    with pytest.raises(ServiceNowWriteRejectedError, match="truncated or altered"):
        _verify_stored(truncated, sent, "KB2001-v1.0")

    # Benign whitespace the instance's sanitiser introduces is not a failure.
    sanitised = {**sent, "text": sent["text"].replace("</p><p>", "</p> <p>")}
    _verify_stored(sanitised, sent, "KB2001-v1.0")
