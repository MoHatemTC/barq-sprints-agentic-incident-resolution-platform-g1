"""Red-team harness for the deterministic input guardrails.

Runs ``data/adversarial/redteam_corpus.json`` through pattern screening and redaction and
reports what each layer did. This measures only the deterministic first layer: a missed
attack here is expected to be caught by the semantic classifier (a model call, so it is
not exercised by this harness and is not claimed by its numbers).

Secret-shaped values are stored in the corpus as ``{{name}}`` placeholders with fragments
joined here at run time, so no tracked file contains a literal credential shape.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent.guardrails.input_screening import screen_text
from observability.redaction import redact_text

CORPUS_PATH = Path(__file__).resolve().parents[3] / "data" / "adversarial" / "redteam_corpus.json"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def load_corpus(path: Path = CORPUS_PATH) -> dict[str, Any]:
    corpus: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return corpus


def render(text: str, fragments: dict[str, list[str]]) -> str:
    """Join the fragments behind every ``{{name}}`` placeholder."""
    return _PLACEHOLDER.sub(lambda m: "".join(fragments[m.group(1)]), text)


@dataclass(frozen=True, slots=True)
class CaseResult:
    id: str
    kind: str  # attack | benign | secret
    label: str
    expect: str
    observed: str  # detected | missed | blocked | review | passed | redacted | leaked
    #: True when the observed outcome is exactly the recorded one. A documented gap
    #: ("known_gap") is recorded as the miss/leak itself, so fixing it is a change too.
    meets_expectation: bool


def evaluate(corpus: dict[str, Any] | None = None) -> list[CaseResult]:
    corpus = corpus or load_corpus()
    fragments: dict[str, list[str]] = corpus.get("fragments", {})
    results: list[CaseResult] = []

    for case in corpus["attacks"]:
        outcome = screen_text(render(case["text"], fragments))
        observed = "detected" if outcome.flagged else "missed"
        results.append(
            CaseResult(
                case["id"],
                "attack",
                case["label"],
                case["expect"],
                observed,
                observed == {"detect": "detected", "known_gap": "missed"}[case["expect"]],
            )
        )

    for case in corpus["benign"]:
        outcome = screen_text(render(case["text"], fragments))
        observed = "blocked" if outcome.blocking else ("review" if outcome.flagged else "passed")
        results.append(
            CaseResult(
                case["id"],
                "benign",
                case["label"],
                case["expect"],
                observed,
                observed
                == {"pass": "passed", "review": "review", "known_limitation": "blocked"}[
                    case["expect"]
                ],
            )
        )

    for case in corpus["secrets"]:
        text = render(case["text"], fragments)
        needle = render(case["secret"], fragments)
        observed = "leaked" if needle in redact_text(text) else "redacted"
        results.append(
            CaseResult(
                case["id"],
                "secret",
                case["label"],
                case["expect"],
                observed,
                observed == {"redact": "redacted", "known_gap": "leaked"}[case["expect"]],
            )
        )
    return results


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    attacks = [r for r in results if r.kind == "attack"]
    benign = [r for r in results if r.kind == "benign"]
    secrets = [r for r in results if r.kind == "secret"]
    return {
        "attacks": {
            "total": len(attacks),
            "detected_by_patterns": sum(r.observed == "detected" for r in attacks),
            "missed": [r.label for r in attacks if r.observed == "missed"],
        },
        "benign": {
            "total": len(benign),
            "hard_blocked": [r.label for r in benign if r.observed == "blocked"],
            "sent_to_semantic_review": [r.label for r in benign if r.observed == "review"],
        },
        "secrets": {
            "total": len(secrets),
            "redacted": sum(r.observed == "redacted" for r in secrets),
            "leaked": [r.label for r in secrets if r.observed == "leaked"],
        },
        "cases": [asdict(r) for r in results],
    }


__all__ = ["CORPUS_PATH", "CaseResult", "evaluate", "load_corpus", "render", "summarize"]
