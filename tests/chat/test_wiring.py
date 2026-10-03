"""Wiring test: the chat service must be built from THIS app's settings (R14).

The router tests replace the service entirely, which would hide a wiring bug
where the feature gate reads one configuration while execution uses another.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import tests.helpers as h
from app.chat.config import ChatSettings
from app.main import create_app


def _service_settings(settings: ChatSettings, monkeypatch: pytest.MonkeyPatch) -> ChatSettings:
    """Run get_chat_service against a stubbed request; capture what it built."""
    from app.chat import dependencies as deps
    from app.chat.service import ChatTurnService

    captured: dict[str, Any] = {}

    def fake_store() -> MagicMock:
        return MagicMock()

    def fake_redis() -> MagicMock:
        return MagicMock()

    def fake_llm() -> MagicMock:
        return MagicMock()

    def fake_retriever(settings: ChatSettings) -> MagicMock:
        captured["retriever_settings"] = settings
        return MagicMock()

    monkeypatch.setattr(deps, "_chat_store_singleton", fake_store)
    monkeypatch.setattr(deps, "_sync_redis_singleton", fake_redis)
    monkeypatch.setattr(deps, "get_llm", fake_llm)
    monkeypatch.setattr(deps, "build_chat_retriever", fake_retriever)

    app = create_app(settings=h.mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN))
    app.state.chat_settings = settings
    request = MagicMock()
    request.app = app

    service = deps.get_chat_service(request)
    assert isinstance(service, ChatTurnService)
    captured["service_settings"] = service._settings
    captured["budget_limit"] = service._budget._limit
    return captured["service_settings"]


def test_service_is_built_from_the_app_settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ChatSettings(
        _env_file=None,
        chat_enabled=True,
        chat_max_security_level="internal",
        chat_evidence_chunk_limit=3,
        chat_daily_budget_usd=1.25,
        chat_price_input_per_mtok=0.5,
        chat_price_output_per_mtok=2.0,
    )

    built = _service_settings(settings, monkeypatch)

    assert built is settings
    assert built.chat_evidence_chunk_limit == 3
    assert built.chat_max_security_level == "internal"


def test_second_app_with_different_settings_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ChatSettings(
        _env_file=None,
        chat_enabled=True,
        chat_daily_budget_usd=1.0,
        chat_price_input_per_mtok=0.5,
        chat_price_output_per_mtok=2.0,
    )
    second = ChatSettings(
        _env_file=None,
        chat_enabled=True,
        chat_daily_budget_usd=2.0,
        chat_price_input_per_mtok=0.5,
        chat_price_output_per_mtok=2.0,
    )

    built_first = _service_settings(first, monkeypatch)
    built_second = _service_settings(second, monkeypatch)

    assert built_first is first
    assert built_second is second
    assert built_first is not built_second
