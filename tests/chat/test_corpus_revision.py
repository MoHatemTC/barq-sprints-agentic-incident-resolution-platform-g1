"""Configured cache writers are serialized without changing feature-off ingestion."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.retrieval import corpus_revision


def setup_writer(monkeypatch, enabled=True):
    from app.chat import config
    from app.core import config as core

    monkeypatch.setattr(
        config, "get_chat_settings", lambda: SimpleNamespace(chat_cache_enabled=enabled)
    )
    monkeypatch.setattr(
        core,
        "get_settings",
        lambda: SimpleNamespace(
            redis_password=None,
            redis_host="test",
            redis_port=6379,
            redis_socket_timeout=1,
        ),
    )
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    monkeypatch.setattr(corpus_revision.redis, "Redis", factory)
    return factory, client, client.lock.return_value


def test_cache_disabled_ingestion_does_not_contact_redis(monkeypatch):
    factory, _, _ = setup_writer(monkeypatch, enabled=False)
    with corpus_revision.chat_corpus_write_lock("kb"):
        corpus_revision.advance_chat_revision("kb", pending=True)
    factory.assert_not_called()


def test_another_writer_is_rejected_before_mutation(monkeypatch):
    _, _, lock = setup_writer(monkeypatch)
    lock.acquire.return_value = False
    with pytest.raises(RuntimeError, match="writer"):
        with corpus_revision.chat_corpus_write_lock("kb"):
            pytest.fail("busy writer must not enter mutation")
    lock.release.assert_not_called()


def test_exception_releases_owned_lock_for_a_repair_run(monkeypatch):
    _, client, lock = setup_writer(monkeypatch)
    lock.acquire.return_value = True
    with pytest.raises(ValueError):
        with corpus_revision.chat_corpus_write_lock("kb"):
            raise ValueError("failed ingestion")
    lock.release.assert_called_once()
    assert client.lock.call_args.kwargs["timeout"] is None
