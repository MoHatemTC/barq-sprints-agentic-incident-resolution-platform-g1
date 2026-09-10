"""Validation CLI for the knowledge base corpus and coverage matrix."""

import csv
from pathlib import Path

from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/articles.json")
COVERAGE_PATH = Path("data/coverage_matrix.csv")


def main() -> None:
    if not CORPUS_PATH.exists():
        raise FileNotFoundError(f"Corpus file not found: {CORPUS_PATH}")

    source = LocalJSONSource(CORPUS_PATH)
    articles = source.load_articles()
    print(f"✅ Successfully validated {len(articles)} articles against Article model")

    if COVERAGE_PATH.exists():
        corpus_ids = {a.article_id for a in articles}
        with COVERAGE_PATH.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if row["is_answerable"] == "true":
                for aid in row["primary_article_ids"].split(";"):
                    if aid and aid not in corpus_ids:
                        inc = row["incident_id"]
                        raise ValueError(f"Unknown primary_article_id {aid} in {inc}")
        print(f"✅ Successfully validated {len(rows)} coverage matrix scenarios")


if __name__ == "__main__":
    main()
