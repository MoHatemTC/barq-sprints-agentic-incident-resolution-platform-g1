"""Chat-specific settings (admin chatbot).

Follows the ``AgentSettings`` pattern: a separate ``BaseSettings`` group with
its own env prefix-free names, overridable per environment. Nothing here is
required at import time so the default test suite never needs chat env values.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Single source of truth with the core settings: None under pytest so unit
# tests never read a developer .env (the #77 isolation rule).
from app.core.config import _ENV_FILE


class ChatSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    chat_enabled: bool = Field(
        default=False,
        description="Master toggle for the admin chatbot API. Off by default; the "
        "endpoints answer 503 until it is explicitly enabled.",
    )
    chat_model: str | None = Field(
        default=None,
        description="Optional Gemini model override for chat calls (defaults to agent_llm_model).",
    )
    chat_max_security_level: Literal["public", "internal", "restricted"] = Field(
        default="restricted",
        description="Highest article audience the chatbot may quote. Trusted admin "
        "operators share the incident agent's 'restricted' ceiling.",
    )
    chat_evidence_chunk_limit: int = Field(
        default=5,
        gt=0,
        description="Evidence chunks fetched per knowledge answer (manual §11.7 top_k).",
    )
    chat_max_evidence_chars: int = Field(
        default=12000,
        gt=0,
        description="Upper bound on the serialized evidence text sent to the answering model.",
    )
    chat_max_output_tokens: int = Field(
        default=2000,
        gt=0,
        description="Worst-case output tokens per chat model call; sizes the budget reserve.",
    )
    chat_max_prompt_chars: int = Field(
        default=60000,
        gt=0,
        description="Hard upper bound on system+prompt chars for any single chat model "
        "call; the gateway refuses larger dispatches instead of sending them.",
    )
    chat_history_message_limit: int = Field(
        default=6,
        gt=0,
        description="Recent messages rehydrated verbatim as conversation context.",
    )
    chat_cache_enabled: bool = False
    chat_cache_similarity_threshold: float = Field(default=0.95, gt=0, le=1)
    chat_cache_ttl_seconds: int = Field(default=3600, gt=0)
    chat_cache_max_entries: int = Field(default=64, gt=0, le=256)
    chat_cache_prompt_version: str = "chat-v1"
    chat_memory_budget_chars: int = Field(
        default=16000,
        gt=0,
        description="Approximate memory budget (~4k tokens) for the summary plus "
        "recent messages included in route/answer prompts.",
    )
    chat_turn_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description="Wall-clock bound for one synchronous chat turn.",
    )

    # -- cost control ------------------------------------------------------------
    # Paid processing is refused while either rate is unset: an estimate against
    # guessed list prices is not a budget. The values must be the verified
    # Sprints proxy rates (see eval/README.md for how they are obtained).
    chat_daily_budget_usd: float = Field(
        default=1.0,
        gt=0,
        description="Chat-specific daily model allowance, reset at 00:00 UTC.",
    )
    chat_price_input_per_mtok: float | None = Field(
        default=None,
        gt=0,
        description="Verified proxy input price in USD per million tokens.",
    )
    chat_price_output_per_mtok: float | None = Field(
        default=None,
        gt=0,
        description="Verified proxy output price in USD per million tokens.",
    )

    @field_validator("chat_model")
    @classmethod
    def _gemini_only(cls, value: str | None) -> str | None:
        """Hold chat to the programme's Gemini-only proxy promise (agent/config.py)."""
        if value is not None and not value.startswith("gemini/"):
            raise ValueError(f"Model must name a Gemini model ('gemini/…'); got {value!r}")
        return value

    @property
    def budget_configured(self) -> bool:
        """True when both verified price rates are present."""
        return (
            self.chat_price_input_per_mtok is not None
            and self.chat_price_output_per_mtok is not None
        )


@lru_cache
def get_chat_settings() -> ChatSettings:
    return ChatSettings()


__all__ = ["ChatSettings", "get_chat_settings"]
