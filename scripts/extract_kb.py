"""Extract Section 6 runbooks from data/barq-system-kb.pdf into JSON."""

from pathlib import Path

from app.retrieval.pdf_extractor import PDFKnowledgeExtractor


def main() -> None:
    extractor = PDFKnowledgeExtractor(Path("data/barq-system-kb.pdf"))
    out_path = Path("data/corpus/barq_articles.json")
    saved = extractor.save_corpus(out_path)
    articles = extractor.extract_articles()
    print(f"Extracted {len(articles)} articles from {extractor.pdf_path} into {saved}")
    for a in articles:
        print(
            f"  - {a.article_id}: {a.title} [{a.service}, {a.security_level}, {a.workflow_state}]"
        )


if __name__ == "__main__":
    main()
