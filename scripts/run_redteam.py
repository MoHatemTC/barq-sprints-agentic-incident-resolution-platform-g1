"""Run the red-team corpus through the deterministic guardrails and record the results.

    uv run python scripts/run_redteam.py            # print the summary
    uv run python scripts/run_redteam.py --write    # also write docs/evidence/redteam_results.json

Only pattern screening and redaction are exercised; no model is called. See
``agent.guardrails.redteam`` for what the numbers do and do not claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent.guardrails.redteam import evaluate, summarize  # noqa: E402

EVIDENCE = REPO_ROOT / "docs" / "evidence" / "redteam_results.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help=f"write {EVIDENCE.relative_to(REPO_ROOT)}"
    )
    args = parser.parse_args()

    results = evaluate()
    summary = summarize(results)
    attacks, benign, redaction = summary["attacks"], summary["benign"], summary["redaction"]
    print(f"attacks : {attacks['detected_by_patterns']}/{attacks['total']} caught by patterns")
    for label in attacks["missed"]:
        print(f"          not caught by patterns (left to the semantic classifier): {label}")
    print(
        f"benign  : {len(benign['hard_blocked'])}/{benign['total']} hard-blocked, "
        f"{len(benign['sent_to_semantic_review'])} sent to semantic review"
    )
    for label in benign["hard_blocked"]:
        print(f"          hard-blocked: {label}")
    print(f"redaction: {redaction['redacted']}/{redaction['total']} forms redacted")
    for label in redaction["not_redacted"]:
        print(f"          form not redacted: {label}")

    off_record = [r for r in results if not r.meets_expectation]
    if off_record:
        print(f"\n{len(off_record)} case(s) differ from the recorded outcome:")
        for r in off_record:
            print(f"  {r.kind} '{r.label}': recorded {r.expect}, observed {r.observed}")

    if args.write:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {EVIDENCE.relative_to(REPO_ROOT)}")
    return 1 if off_record else 0


if __name__ == "__main__":
    raise SystemExit(main())
