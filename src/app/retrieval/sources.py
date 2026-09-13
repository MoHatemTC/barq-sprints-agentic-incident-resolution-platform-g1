"""Article sources — the single boundary between where articles come from and
the rest of the pipeline.

Everything downstream (chunking, embedding, ingestion) consumes the
`KnowledgeSource` protocol and never knows the origin. Switching Path A ->
Path B, or wiring the ServiceNow read-back, becomes a configuration change
rather than a rewrite.
"""

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.models.knowledge import Article


class KnowledgeSource(Protocol):
    """Anything that can produce validated articles can feed the pipeline."""

    def load_articles(self) -> list[Article]: ...


class LocalJSONSource:
    """Path A source: articles authored from scratch in a local JSON file.

    Expects a JSON array of article objects. Validation is fail-fast — our own
    corpus must be correct before anything reaches ServiceNow.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_articles(self) -> list[Article]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"{self.path} must contain a JSON array of articles")
        return [Article.model_validate(item) for item in raw]


def summarize_validation_error(error: ValidationError, source_name: str) -> str:
    """One-line description of why an article failed validation.

    Used by the Path B supplied-data source to build its schema validation
    report; kept here so the error format has one definition.
    """
    issues = "; ".join(
        f"{'.'.join(str(loc) for loc in issue['loc'])}: {issue['msg']}" for issue in error.errors()
    )
    return f"{source_name}: {issues}"
