"""Invalidate chat answer partitions around standard corpus mutations."""

from collections.abc import Iterator
from contextlib import contextmanager

import redis


@contextmanager
def chat_corpus_write_lock(collection: str) -> Iterator[None]:
    """Cache-enabled writers serialize so one cannot finalize another's revision."""
    from app.chat.cache import PREFIX, digest
    from app.chat.config import get_chat_settings
    from app.core.config import get_settings

    if not get_chat_settings().chat_cache_enabled:
        yield
        return
    settings = get_settings()
    password = settings.redis_password.get_secret_value() if settings.redis_password else None
    with redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        socket_timeout=settings.redis_socket_timeout,
    ) as client:
        # No automatic expiry: reclaiming a lock while its writer still runs
        # could admit answers from a partial corpus. Fail fast for another writer.
        lock = client.lock(PREFIX + "writer:" + digest(collection), timeout=None)
        if not lock.acquire(blocking=False):
            raise RuntimeError("A cache-enabled corpus writer is already active")
        try:
            yield
        finally:
            lock.release()


def advance_chat_revision(collection: str, *, pending: bool = False) -> None:
    # Ingestion can run independently of the chat API. Feature-off ingestion
    # must continue to work without Redis or chat credentials.
    from app.chat.cache import bump_revision
    from app.chat.config import get_chat_settings
    from app.core.config import get_settings

    if not get_chat_settings().chat_cache_enabled:
        return
    settings = get_settings()
    password = settings.redis_password.get_secret_value() if settings.redis_password else None
    with redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        socket_timeout=settings.redis_socket_timeout,
    ) as client:
        # If invalidation is configured but unavailable, abort the writer;
        # never report a cache-coherent successful ingestion without it.
        bump_revision(client, collection, pending=pending)
