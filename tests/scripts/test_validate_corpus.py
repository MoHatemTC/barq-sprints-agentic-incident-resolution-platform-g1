"""Tests for the validate_corpus CLI: partial validation must never look complete."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path("scripts/validate_corpus.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_corpus_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_coverage_matrix_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ground-truth matrix is an error — not a corpus-only silent pass."""
    module = _load_module()
    monkeypatch.setattr(module, "COVERAGE_PATH", tmp_path / "does_not_exist.csv")

    with pytest.raises(FileNotFoundError, match="Coverage matrix not found"):
        module.main()


def test_full_validation_succeeds_on_real_data(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()
    module.main()

    out = capsys.readouterr().out
    assert "11 articles" in out
    assert "25 coverage matrix scenarios" in out
