"""Validation CLI for the knowledge base corpus and coverage matrix."""

import csv
from pathlib import Path

from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/barq_articles.json")
COVERAGE_PATH = Path("data/coverage_matrix.csv")

VALID_SOURCES = {"manual", "synthetic"}


def main() -> None:
    if not CORPUS_PATH.exists():
        raise FileNotFoundError(f"Corpus file not found: {CORPUS_PATH}")

    source = LocalJSONSource(CORPUS_PATH)
    articles = source.load_articles()
    print(f"✅ Successfully validated {len(articles)} articles against Article model")

    if not COVERAGE_PATH.exists():
        raise FileNotFoundError(f"Coverage matrix not found: {COVERAGE_PATH}")

    corpus_ids = {a.article_number for a in articles} | {a.unique_key for a in articles}
    # The matrix must be standard-CSV machine-readable: no comment stripping here.
    with COVERAGE_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        inc = row["incident_id"]
        if not inc or inc.startswith("#"):
            raise ValueError(f"Non-incident row in coverage matrix: {inc!r}")

        row_source = (row.get("source") or "").strip()
        if row_source not in VALID_SOURCES:
            raise ValueError(f"Unknown source {row_source!r} in {inc}")

        if row["is_answerable"] != "true":
            continue

        primary = [x for x in row["primary_article_ids"].split(";") if x]
        acceptable = [x for x in row["acceptable_article_ids"].split(";") if x]
        forbidden = [x for x in (row.get("forbidden_article_ids") or "").split(";") if x]
        if not primary:
            raise ValueError(f"{inc} is answerable but has no primary_article_ids")

        for kind, ids in (
            ("primary_article_id", primary),
            ("acceptable_article_id", acceptable),
            ("forbidden_article_id", forbidden),
        ):
            for aid in ids:
                if aid not in corpus_ids:
                    raise ValueError(f"Unknown {kind} {aid} in {inc}")

        overlap = (set(primary) | set(acceptable)) & set(forbidden)
        if overlap:
            raise ValueError(
                f"{inc}: articles {sorted(overlap)} are both acceptable/primary "
                "and forbidden — a run surfacing them would be scored as a pass"
            )

    print(f"✅ Successfully validated {len(rows)} coverage matrix scenarios")


if __name__ == "__main__":
    main()
