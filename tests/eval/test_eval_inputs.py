"""The BARQ 100-turn eval inputs are tracked and framework-free.

The dataset and its loaders live under ``data/structured-io/``, which the broad
``data/*`` ignore rule hides; on a clean checkout these tests fail unless the
two named files are committed. Content changes are reviewed in git like any
other file — no hash ceremony on top.
"""

import ast
import importlib.util
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "data" / "structured-io"
DATASET = EVAL_DIR / "barq_rag_eval_dataset.json"
ADAPTERS = EVAL_DIR / "adapters.py"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout


def test_eval_inputs_are_tracked() -> None:
    tracked = set(_git("ls-files", "data/structured-io/").splitlines())
    assert "data/structured-io/barq_rag_eval_dataset.json" in tracked
    assert "data/structured-io/adapters.py" in tracked


def test_dataset_contract_20_sessions_100_turns() -> None:
    data = json.loads(DATASET.read_text())
    sessions = data["sessions"]
    assert len(sessions) == 20

    turns = [turn for session in sessions for turn in session["turns"]]
    assert len(turns) == 100
    assert len({turn["turn_id"] for turn in turns}) == 100

    behaviours = {behaviour: 0 for behaviour in ("answer", "refuse", "clarify")}
    for turn in turns:
        behaviours[turn["expected_behaviour"]] += 1
    assert behaviours == {"answer": 87, "refuse": 12, "clarify": 1}


def test_adapters_import_without_judge_frameworks() -> None:
    tree = ast.parse(ADAPTERS.read_text())
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert not ({"deepeval", "ragas", "google"} & top_level), (
        "judge frameworks must be imported lazily inside functions, not at module load"
    )

    spec = importlib.util.spec_from_file_location("barq_eval_adapters", ADAPTERS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "score_retrieval")
