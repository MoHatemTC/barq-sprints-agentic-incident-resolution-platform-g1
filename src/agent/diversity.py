"""MMR over article representatives; companion sections are bundled afterwards."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def mmr_order(
    relevance: Mapping[str, float],
    vectors: Mapping[str, Sequence[float]],
    *,
    lambda_mult: float,
) -> list[str]:
    """Greedily balance bounded query relevance and positive cosine redundancy.

    The first article remains the strongest relevance match. Lambda=1 exactly
    preserves relevance ordering. Ties use article ID, not backend response order.
    Scores here order evidence; they never authorize a write or relax its gate.
    """
    if not math.isfinite(lambda_mult) or not 0 <= lambda_mult <= 1:
        raise ValueError("MMR lambda must be between zero and one")
    ordered = sorted(relevance, key=lambda key: (-relevance[key], key))
    if len(ordered) < 2 or lambda_mult == 1:
        return ordered
    normalized: dict[str, list[float]] = {}
    dimensions = set()
    for key in ordered:
        score = relevance[key]
        values = list(vectors[key])
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("MMR relevance must be finite and bounded")
        if not values or not math.isfinite(norm) or norm == 0:
            raise ValueError("MMR requires finite, nonzero dense vectors")
        dimensions.add(len(values))
        normalized[key] = [value / norm for value in values]
    if len(dimensions) != 1:
        raise ValueError("MMR vector dimensions must match")
    selected = [ordered.pop(0)]
    while ordered:

        def objective(key: str) -> float:
            redundancy = max(
                max(
                    0.0, sum(a * b for a, b in zip(normalized[key], normalized[other], strict=True))
                )
                for other in selected
            )
            return lambda_mult * relevance[key] - (1 - lambda_mult) * redundancy

        best = min(ordered, key=lambda key: (-objective(key), -relevance[key], key))
        selected.append(best)
        ordered.remove(best)
    return selected
