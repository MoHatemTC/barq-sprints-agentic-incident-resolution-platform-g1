"""PostgreSQL cannot store NUL in JSON; the engines strip it (see app.db.json_codec)."""

from __future__ import annotations

import json

from app.db.json_codec import pg_json_dumps, strip_nul
from app.db.session import create_database_engine
from app.workers.sync_engine import create_sync_engine


def test_nul_is_removed_everywhere_in_a_document() -> None:
    document = {"brief\x00": ["a\x00b", {"c": "d\x00"}], "n": 1, "ok": None}
    assert strip_nul(document) == {"brief": ["ab", {"c": "d"}], "n": 1, "ok": None}
    encoded = pg_json_dumps(document)
    assert "\\u0000" not in encoded
    assert json.loads(encoded) == {"brief": ["ab", {"c": "d"}], "n": 1, "ok": None}


def test_text_without_nul_is_unchanged() -> None:
    document = {"text": "plain \\u0000 as literal text stays", "list": [1, 2.5, True]}
    assert json.loads(pg_json_dumps(document)) == document


def test_both_engines_use_the_nul_safe_serializer() -> None:
    url = "postgresql+psycopg://user:pass@localhost:5432/db"
    assert create_sync_engine(url).dialect._json_serializer is pg_json_dumps
    async_engine = create_database_engine("postgresql+psycopg://user:pass@localhost:5432/db")
    assert async_engine.sync_engine.dialect._json_serializer is pg_json_dumps
