"""LLM and embedding clients, with dependency-injection providers (S2.5).

Nodes depend on the :class:`LLMClient` protocol, never on the SDK, so the unit
tests inject a scripted fake and the graph never needs a network in CI.

The production client calls Claude through the official ``anthropic`` SDK with
structured outputs (``messages.parse`` + a Pydantic schema), adaptive thinking and
the server-side refusal fallback. Every call is a Langfuse *generation* carrying
the model actually served, token usage, cost and the prompt version.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from agent.config import AgentSettings, get_agent_settings
from app.retrieval.embedding import EmbeddingEngine
from app.workers.retry_policy import RetryableError, TerminalError
from observability.redaction import redact_text
from observability.tracing import Tracer, get_tracer

M = TypeVar("M", bound=BaseModel)

#: USD per million tokens (input, output) — Anthropic list prices. Cost is recorded
#: on each generation so Langfuse can total it even for models it has no price for.
MODEL_PRICES_PER_MTOK: Mapping[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelRefusalError(TerminalError):
    """The model (and its fallback chain) declined the request."""


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    def structured(self, *, purpose: str, system: str, prompt: str, schema: type[M]) -> M:
        """Return ``schema`` parsed from the model's answer to ``prompt``."""
        ...


def usage_details(usage: Any) -> dict[str, int]:
    details = {
        "input": int(getattr(usage, "input_tokens", 0) or 0),
        "output": int(getattr(usage, "output_tokens", 0) or 0),
    }
    for field, key in (
        ("cache_read_input_tokens", "cache_read_input_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
    ):
        value = getattr(usage, field, None)
        if value:
            details[key] = int(value)
    return details


def cost_details(model: str, usage: Mapping[str, int]) -> dict[str, float] | None:
    prices = MODEL_PRICES_PER_MTOK.get(model)
    if prices is None:
        return None
    input_cost = usage.get("input", 0) * prices[0] / 1_000_000
    output_cost = usage.get("output", 0) * prices[1] / 1_000_000
    return {"input": input_cost, "output": output_cost, "total": input_cost + output_cost}


class AnthropicLLM:
    """Claude via the Anthropic SDK (sync client; the worker is sync)."""

    def __init__(self, settings: AgentSettings, tracer: Tracer, client: Any | None = None) -> None:
        self._settings = settings
        self._tracer = tracer
        if client is None:
            import anthropic

            # An empty ANTHROPIC_API_KEY= line means "unset", not "the empty key".
            api_key = (
                settings.anthropic_api_key.get_secret_value()
                if settings.anthropic_api_key is not None
                else None
            ) or None
            client = anthropic.Anthropic(
                api_key=api_key,
                timeout=settings.agent_llm_timeout_seconds,
                max_retries=settings.agent_llm_max_retries,
            )
        self._client = client

    @property
    def model_name(self) -> str:
        return self._settings.agent_llm_model

    def structured(self, *, purpose: str, system: str, prompt: str, schema: type[M]) -> M:
        import anthropic

        settings = self._settings
        with self._tracer.span(
            f"llm.{purpose}",
            as_type="generation",
            model=settings.agent_llm_model,
            input={"system": system, "prompt": prompt},
            version=settings.agent_prompt_version,
            model_parameters={
                "effort": settings.agent_llm_effort,
                "max_tokens": settings.agent_llm_max_tokens,
                "thinking": "adaptive",
            },
            metadata={"prompt_name": purpose, "prompt_version": settings.agent_prompt_version},
        ) as generation:
            try:
                response = self._client.beta.messages.parse(
                    model=settings.agent_llm_model,
                    max_tokens=settings.agent_llm_max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": settings.agent_llm_effort},
                    output_format=schema,
                    betas=[REFUSAL_FALLBACK_BETA],
                    fallbacks="default",
                )
            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as exc:
                raise RetryableError(
                    f"model call failed transiently: {type(exc).__name__}"
                ) from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    raise RetryableError(f"model call failed: HTTP {exc.status_code}") from exc
                raise TerminalError(f"model call rejected: HTTP {exc.status_code}") from exc

            served_model = str(getattr(response, "model", settings.agent_llm_model))
            usage = usage_details(response.usage)
            generation.update(
                model=served_model,
                usage_details=usage,
                cost_details=cost_details(served_model, usage),
                metadata={
                    "stop_reason": response.stop_reason,
                    "request_id": getattr(response, "_request_id", None),
                },
            )
            if response.stop_reason == "refusal":
                raise ModelRefusalError(f"model declined the {purpose} request")
            if response.stop_reason == "max_tokens":
                raise TerminalError(f"{purpose} output was truncated at max_tokens")
            parsed = response.parsed_output
            if parsed is None:
                raise TerminalError(f"{purpose} returned no structured output")
            generation.update(output=parsed.model_dump(mode="json"))
            return parsed


def bounded(text: str, limit: int) -> str:
    """Redact, then cap length (manual §11.6: length and encoding bounds)."""
    cleaned = redact_text(text.encode("utf-8", "replace").decode("utf-8"))
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + " …[truncated]"


# -- providers ------------------------------------------------------------------------


@lru_cache
def get_llm() -> LLMClient:
    """Process-wide LLM client singleton."""
    return AnthropicLLM(get_agent_settings(), get_tracer())


@lru_cache
def get_embedding_engine() -> EmbeddingEngine:
    """Process-wide embedding engine singleton: model weights load once per worker."""
    from app.retrieval.embedding import FastEmbedEngine

    return FastEmbedEngine()


__all__ = [
    "MODEL_PRICES_PER_MTOK",
    "AnthropicLLM",
    "LLMClient",
    "ModelRefusalError",
    "bounded",
    "cost_details",
    "get_embedding_engine",
    "get_llm",
    "usage_details",
]
