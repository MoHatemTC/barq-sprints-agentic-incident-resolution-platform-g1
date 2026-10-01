"""Threshold Calibration Script for Semantic Caching (Sprint 4 - S4.2).

Empirically determines the optimal similarity threshold (tau) for the
SemanticCache by computing pairwise cosine similarities on the curated
incident pair dataset defined in ``eval/calibration_pairs.py``.

Decision criterion
------------------
  tau = midpoint(min_positive, max_negative)
  Then biased conservatively: tau -= safety_margin
  Clamped strictly inside (max_negative, min_positive).

Usage
-----
    cd <repo-root>
    .venv/Scripts/python eval/calibrate_threshold.py [--verbose] [--domain DOMAIN]

    --verbose      Print rationale + bar chart for every pair.
    --domain       Filter to a specific incident domain (e.g. "network", "identity").

Output
------
  Console table of all pairs with their similarity scores.
  Per-domain breakdown.
  Summary statistics (min/max/mean/std per class).
  Recommended threshold + safety margin analysis.
  Full tau sweep table (TP/TN/FP/FN/Accuracy at each candidate value).

Notes
-----
  * Runs fully offline – no DB, Celery, or LangGraph required.
  * Uses the same FastEmbedEngine (BAAI/bge-small-en-v1.5) as production,
    so the calibrated tau is directly applicable.
  * Idempotent – yields identical output on every run (deterministic model).
  * To extend the dataset, edit eval/calibration_pairs.py only.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup – ensures imports work from repo root without installing packages
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# Import calibration dataset from sibling module
# ---------------------------------------------------------------------------
_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from calibration_pairs import CALIBRATION_PAIRS, IncidentPair  # noqa: E402

# ---------------------------------------------------------------------------
# Cosine similarity  (matches the implementation in semantic_cache.py exactly)
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two dense float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Embedding  (re-uses the production FastEmbedEngine)
# ---------------------------------------------------------------------------


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed texts using the production FastEmbedEngine.

    Deduplicated texts are embedded in a single pass to minimise model
    initialisation overhead and guarantee BM25 IDF weights are consistent.
    """
    from app.retrieval.embedding import FastEmbedEngine  # type: ignore[import]

    engine = FastEmbedEngine()
    results = engine.embed_documents(texts)
    return [r.dense for r in results]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PairResult:
    label: str
    domain: str
    expected: str  # "positive" | "negative"
    similarity: float
    rationale: str


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _stats_block(label: str, sims: list[float]) -> str:
    return (
        f"  {label} (n={len(sims)})\n"
        f"    min  = {min(sims):.4f}   max  = {max(sims):.4f}\n"
        f"    mean = {_mean(sims):.4f}   std  = {_stdev(sims):.4f}"
    )


# ---------------------------------------------------------------------------
# Calibration helpers
# ---------------------------------------------------------------------------


def _embed_pairs(
    pairs: list[IncidentPair],
) -> list[PairResult]:
    """Embed all pair texts (deduped) and return PairResult list."""
    # Deduplicate texts – avoids redundant embedding calls
    text_to_idx: dict[str, int] = {}
    all_texts: list[str] = []
    for pair in pairs:
        for text in (pair.a, pair.b):
            if text not in text_to_idx:
                text_to_idx[text] = len(all_texts)
                all_texts.append(text)

    vectors = embed_texts(all_texts)

    results: list[PairResult] = []
    for pair in pairs:
        va = vectors[text_to_idx[pair.a]]
        vb = vectors[text_to_idx[pair.b]]
        results.append(
            PairResult(
                label=pair.label,
                domain=pair.domain,
                expected=pair.expected,
                similarity=cosine_similarity(va, vb),
                rationale=pair.rationale,
            )
        )
    return results


def _print_pairs_table(
    results: list[PairResult],
    verbose: bool = False,
) -> None:
    col_w = max(len(r.label) for r in results) + 2
    header = f"  {'PAIR':<{col_w}}  {'DOMAIN':<14}  {'CLASS':<10}  {'SIMILARITY':>10}"
    print(header)
    print("  " + "-" * (col_w + 42))
    for r in sorted(results, key=lambda x: (x.expected, -x.similarity)):
        tag = "+ POSITIVE" if r.expected == "positive" else "- NEGATIVE"
        bar_len = int(r.similarity * 40)
        bar = "#" * bar_len + "." * (40 - bar_len)
        print(f"  {r.label:<{col_w}}  {r.domain:<14}  {tag:<10}  {r.similarity:>10.4f}")
        if verbose:
            print(f"    Rationale : {r.rationale}")
            print(f"    Visual    : [{bar}]")


def _print_domain_breakdown(results: list[PairResult]) -> None:
    """Print per-domain similarity summary."""
    domains = sorted({r.domain for r in results})
    print(f"\n  {'DOMAIN':<16}  {'n':>4}  {'POS_MEAN':>9}  {'NEG_MEAN':>9}  {'GAP':>8}")
    print("  " + "-" * 54)
    for d in domains:
        pos = [r.similarity for r in results if r.domain == d and r.expected == "positive"]
        neg = [r.similarity for r in results if r.domain == d and r.expected == "negative"]
        n = len(pos) + len(neg)
        pos_mean = f"{_mean(pos):.4f}" if pos else "   N/A  "
        neg_mean = f"{_mean(neg):.4f}" if neg else "   N/A  "
        gap = f"{(_mean(pos) - _mean(neg)):+.4f}" if pos and neg else "   N/A  "
        print(f"  {d:<16}  {n:>4}  {pos_mean:>9}  {neg_mean:>9}  {gap:>8}")


def _tau_sweep(
    positives: list[PairResult],
    negatives: list[PairResult],
    current_tau: float,
    recommended_tau: float,
) -> float:
    """Print per-tau correctness sweep and return the tau with best accuracy."""
    total = len(positives) + len(negatives)
    print(
        f"  {'tau':>6}  {'TP':>4}  {'TN':>4}  {'FP':>4}  {'FN':>4}  {'Acc':>7}  {'Prec':>7}  {'Rec':>7}  Note"
    )
    print("  " + "-" * 74)

    best_acc = -1.0
    best_tau = current_tau
    for candidate in [round(0.60 + i * 0.02, 2) for i in range(21)]:
        tp = sum(1 for r in positives if r.similarity >= candidate)
        fn = len(positives) - tp
        tn = sum(1 for r in negatives if r.similarity < candidate)
        fp = len(negatives) - tn
        acc = (tp + tn) / total
        prec = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        notes: list[str] = []
        if abs(candidate - current_tau) < 1e-9:
            notes.append("<- current")
        if abs(candidate - recommended_tau) < 1e-9:
            notes.append("<- recommended")
        note_str = "  ".join(notes)
        print(
            f"  {candidate:>6.2f}  {tp:>4}  {tn:>4}  {fp:>4}  {fn:>4}"
            f"  {acc:>6.1%}  {prec:>6.1%}  {rec:>6.1%}  {note_str}"
        )
        if acc > best_acc:
            best_acc = acc
            best_tau = candidate
    return best_tau


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------


def run_calibration(
    pairs: list[IncidentPair],
    verbose: bool = False,
    domain_filter: str | None = None,
) -> float:
    """Run calibration and return the recommended tau value.

    Parameters
    ----------
    pairs:
        List of IncidentPair objects to evaluate.
    verbose:
        If True, print rationale and visual bar for each pair.
    domain_filter:
        If set, only pairs whose ``domain`` matches this string are evaluated.
    """
    current_tau = 0.80  # current value in semantic_cache.py

    # Apply domain filter
    if domain_filter:
        pairs = [p for p in pairs if p.domain == domain_filter]
        if not pairs:
            print(f"[!] No pairs found for domain '{domain_filter}'. Aborting.")
            sys.exit(1)

    print("=" * 76)
    print("  BARQ G1 - Sprint 4 S4.2 - Semantic Threshold Calibration")
    print(f"  Model : BAAI/bge-small-en-v1.5  |  Pairs: {len(pairs)}  |  Metric: cosine similarity")
    if domain_filter:
        print(f"  Domain filter: {domain_filter}")
    print("=" * 76)

    # ------------------------------------------------------------------ #
    # Step 1 – Embed                                                      #
    # ------------------------------------------------------------------ #
    print("\n[1/4]  Embedding incident signatures ...", flush=True)
    results = _embed_pairs(pairs)
    dim = 0
    # Peek dimension from first embedding by re-embedding a single text
    try:
        sample = embed_texts([pairs[0].a])
        dim = len(sample[0])
    except Exception:
        dim = 384  # fallback known value
    print(
        f"       Embedded {len({p.a for p in pairs} | {p.b for p in pairs})} "
        f"unique texts -> {dim}-dim vectors\n"
    )

    positives = [r for r in results if r.expected == "positive"]
    negatives = [r for r in results if r.expected == "negative"]

    # ------------------------------------------------------------------ #
    # Step 2 – Results table                                              #
    # ------------------------------------------------------------------ #
    print("[2/4]  Similarity Results\n")
    _print_pairs_table(results, verbose=verbose)

    # ------------------------------------------------------------------ #
    # Step 3 – Statistics                                                 #
    # ------------------------------------------------------------------ #
    print("\n[3/4]  Statistics\n")
    pos_sims = [r.similarity for r in positives]
    neg_sims = [r.similarity for r in negatives]

    print("-" * 76)
    print(_stats_block("Positives", pos_sims))
    print(_stats_block("Negatives", neg_sims))

    separation_gap = min(pos_sims) - max(neg_sims)
    print(f"\n  Separation gap (min_positive - max_negative) : {separation_gap:+.4f}")

    if separation_gap <= 0:
        overlap = [r.label for r in positives if r.similarity <= max(neg_sims)] + [
            r.label for r in negatives if r.similarity >= min(pos_sims)
        ]
        print(
            f"\n  WARNING: Classes overlap! Overlapping pairs: {overlap}\n"
            "  Consider enriching incident signatures (add service/category fields)."
        )

    print("\n  Domain breakdown:")
    _print_domain_breakdown(results)

    # ------------------------------------------------------------------ #
    # Threshold recommendation                                            #
    # ------------------------------------------------------------------ #
    min_pos = min(pos_sims)
    max_neg = max(neg_sims)
    midpoint = (min_pos + max_neg) / 2.0
    # Conservative bias: prefer missing a cluster over false-positive clustering.
    # Safety margin = 10% of separation gap, minimum 0.01 absolute.
    safety_margin = max(0.01, abs(separation_gap) * 0.10)
    recommended_tau = midpoint - safety_margin
    recommended_tau = max(recommended_tau, max_neg + 0.01)  # must stay above max_neg
    recommended_tau = min(recommended_tau, min_pos - 0.01)  # must stay below min_pos
    recommended_tau = round(recommended_tau, 4)

    print("\n" + "-" * 76)
    print("  THRESHOLD RECOMMENDATION")
    print("-" * 76)
    print(f"  min_positive           : {min_pos:.4f}")
    print(f"  max_negative           : {max_neg:.4f}")
    print(f"  Midpoint               : {midpoint:.4f}")
    print(f"  Safety margin          : -{safety_margin:.4f}")
    print(f"  Recommended tau        : {recommended_tau:.4f}")
    print(f"  Current coded tau      : {current_tau:.4f}")

    delta = abs(recommended_tau - current_tau)
    if delta < 0.02:
        print(
            f"\n  OK  Current tau={current_tau:.4f} is within +/-0.02 of recommended. "
            "No change required."
        )
    elif recommended_tau > current_tau:
        print(
            f"\n  RAISE tau {current_tau:.4f} -> {recommended_tau:.4f}\n"
            "  Reason: current threshold may admit false-positive clusters."
        )
    else:
        print(
            f"\n  LOWER tau {current_tau:.4f} -> {recommended_tau:.4f}\n"
            "  Reason: current threshold may miss valid clusters (false negatives)."
        )

    # ------------------------------------------------------------------ #
    # Step 4 – Tau sweep                                                  #
    # ------------------------------------------------------------------ #
    print("\n[4/4]  Tau Sweep\n")
    print("-" * 76)
    best_tau = _tau_sweep(positives, negatives, current_tau, recommended_tau)
    print(f"\n  Best overall accuracy at tau = {best_tau:.2f}")
    print("=" * 76)

    return recommended_tau


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate the semantic similarity threshold (tau) for BARQ G1 Sprint 4 S4.2.\n"
            "Dataset is loaded from eval/calibration_pairs.py."
        )
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print rationale and visual bar chart for each pair.",
    )
    parser.add_argument(
        "--domain",
        "-d",
        type=str,
        default=None,
        metavar="DOMAIN",
        help=(
            "Filter evaluation to a single incident domain "
            "(e.g. network, identity, database, sap, email, hardware, storage, cloud, facilities, cross-domain)."
        ),
    )
    args = parser.parse_args()

    run_calibration(
        pairs=list(CALIBRATION_PAIRS),
        verbose=args.verbose,
        domain_filter=args.domain,
    )
    sys.exit(0)
