"""The manual extractor must target the committed PDF edition, not a phantom file."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "manual" / "extract_manual.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("extract_manual_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_manual_targets_the_committed_pdf() -> None:
    module = _load_module()
    assert module.DEFAULT_MANUAL_PDF == Path("data/barq-system-kb.pdf")
