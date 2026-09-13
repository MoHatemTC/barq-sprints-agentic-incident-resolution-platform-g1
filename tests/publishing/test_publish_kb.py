"""Tests for the publish_kb CLI: dry-run, empty corpus, missing KB id, report."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/barq_articles.json")
FAKE_KB_ID = "kb-base-1111111111111111"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "publish_kb_under_test", Path("scripts/publish_kb.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    module = _load_module()
    monkeypatch.setattr("sys.argv", ["publish_kb.py", *argv])
    return module.main()


def _with_kb_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The script reads the lru-cached settings; patch the shared instance."""
    monkeypatch.setattr(get_settings(), "servicenow_kb_id", FAKE_KB_ID)


def test_dry_run_publishes_nothing_and_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corpus_file: Path,
) -> None:
    _with_kb_id(monkeypatch)
    report = tmp_path / "report.json"
    exit_code = _run(
        monkeypatch,
        "--corpus",
        str(corpus_file),
        "--report",
        str(report),
        "--dry-run",
    )

    assert exit_code == 0
    data = json.loads(report.read_text())
    assert data["dry_run"] is True
    assert data["created"] == 0 and data["updated"] == 0 and data["failed"] == []


def test_dry_run_on_real_corpus_validates_all_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The committed corpus must validate end-to-end without any PDI."""
    if not CORPUS_PATH.exists():
        pytest.skip("real corpus not available")
    _with_kb_id(monkeypatch)
    report = tmp_path / "report.json"
    exit_code = _run(
        monkeypatch, "--corpus", str(CORPUS_PATH), "--report", str(report), "--dry-run"
    )

    assert exit_code == 0
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    assert len(articles) == 11


def test_missing_kb_id_fails_before_any_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, corpus_file: Path
) -> None:
    monkeypatch.setattr(get_settings(), "servicenow_kb_id", "")
    exit_code = _run(
        monkeypatch, "--corpus", str(corpus_file), "--report", str(tmp_path / "r.json")
    )

    assert exit_code == 1


def test_empty_corpus_fails_loud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _with_kb_id(monkeypatch)
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    exit_code = _run(monkeypatch, "--corpus", str(empty), "--report", str(tmp_path / "r.json"))

    assert exit_code == 1


def test_missing_corpus_file_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exit_code = _run(
        monkeypatch,
        "--corpus",
        str(tmp_path / "nope.json"),
        "--report",
        str(tmp_path / "r.json"),
    )
    assert exit_code == 1


def test_without_allow_writes_the_run_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corpus_file: Path,
) -> None:
    """No flags at all must not publish to a live instance.

    Added while reviewing #88. The script previously wrote unless --dry-run was passed,
    which is the opposite of scripts/test_client.py after #76 (issue #41) — and this one
    also creates kb_category records on the shared PDI. The destructive path is the one
    that has to be asked for.
    """
    _with_kb_id(monkeypatch)
    report = tmp_path / "report.json"

    # No --dry-run and no --allow-writes. If this ever makes HTTP calls the test suite
    # has no network fake mounted, so it would fail rather than reach an instance.
    exit_code = _run(monkeypatch, "--corpus", str(corpus_file), "--report", str(report))

    assert exit_code == 0
    data = json.loads(report.read_text())
    assert data["dry_run"] is True, "a run without --allow-writes must be a dry run"
    assert data["created"] == 0 and data["updated"] == 0
