"""``run_blocking`` is the one sync-to-async bridge used by workers and graph nodes."""

from __future__ import annotations

import asyncio
import contextvars

from app.utils.async_bridge import run_blocking

_correlation: contextvars.ContextVar[str] = contextvars.ContextVar("test_correlation", default="-")


async def _read_correlation() -> str:
    return _correlation.get()


def test_without_a_running_loop_it_just_runs_the_coroutine() -> None:
    _correlation.set("sync-caller")
    assert run_blocking(_read_correlation()) == "sync-caller"


def test_inside_a_running_loop_the_helper_thread_inherits_the_callers_context() -> None:
    async def caller() -> str:
        _correlation.set("corr-123")
        # A synchronous node called from async code: the loop is running in this thread.
        return run_blocking(_read_correlation())

    assert asyncio.run(caller()) == "corr-123"
