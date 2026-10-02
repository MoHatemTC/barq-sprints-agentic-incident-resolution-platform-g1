"""Run a coroutine to completion from synchronous worker code.

The Celery task and the graph nodes are synchronous, while the tool registry is async.
``run_blocking`` is the single bridge between them, so the "is there already a running
loop in this thread" decision lives in one place instead of being copied per caller.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
from collections.abc import Coroutine
from typing import Any


def run_blocking[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` and return its result.

    With no running loop in this thread (the normal Celery case) this is
    ``asyncio.run``. If a loop is already running here, ``asyncio.run`` would raise, so
    the coroutine is run on a short-lived helper thread with its own loop instead, inside a
    copy of the caller's context so the correlation id and log context follow it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(contextvars.copy_context().run, asyncio.run, coro).result()


__all__ = ["run_blocking"]
