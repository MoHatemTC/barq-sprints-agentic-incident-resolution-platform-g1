"""Concurrency safety checks for the synchronous ServiceNow gateway."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agent.servicenow import AsyncRunner


def test_timeout_waits_until_coroutine_acknowledges_cancellation() -> None:
    cancelled = threading.Event()

    async def slow_request() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner = AsyncRunner()
    try:
        with pytest.raises(TimeoutError):
            runner.run(slow_request(), timeout=0.01, cancellation_timeout=1.0)
        assert cancelled.is_set()
    finally:
        runner.close()
