"""LLM and embedding clients, with dependency-injection providers (S2.5).

Nodes depend on the :class:`LLMClient` protocol, never on the SDK, so the unit
tests inject a scripted fake and the graph never needs a network in CI.

The production client calls **Gemini through the Sprints LiteLLM proxy** — the
programme's mandated model path (Ahmed Mansour, 2026-09-17: "we will use gemini
models only"). The proxy speaks the OpenAI API, so the official ``openai`` SDK is
the client: ``chat.completions.parse`` with a Pydantic ``response_format`` gives
schema-validated structured output. Every call is a Langfuse *generation* with the
model that served it, token usage, the cost LiteLLM reports, and the prompt version.
Embeddings stay local (FastEmbed): the S1.4 index was built with them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from agent.config import AgentSettings, get_agent_settings
from app.retrieval.embedding import EmbeddingEngine
from app.workers.retry_policy import RetryableError, TerminalError
from observability.redaction import redact_text
from observability.tracing import Tracer, get_tracer

M = TypeVar("M", bound=BaseModel)

#: Response header in which the LiteLLM proxy reports the USD cost of the call.
COST_HEADER = "x-litellm-response-cost"


class ModelRefusalError(TerminalError):
    """The model declined the request (content filter)."""


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    def model_for_purpose(self, purpose: str, override: str | None = None) -> str: ...

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: type[M],
        model: str | None = None,
    ) -> M:
        """Return ``schema`` parsed from the model's answer to ``prompt``."""
        ...


def usage_details(usage: Any) -> dict[str, int]:
    """OpenAI-shaped usage → Langfuse usage details."""
    if usage is None:
        return {}
    details = {
        "input": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output": int(getattr(usage, "completion_tokens", 0) or 0),
    }
    reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0)
    if reasoning:
        details["reasoning_tokens"] = int(reasoning)
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0)
    if cached:
        details["cached_tokens"] = int(cached)
    return details


def cost_details(header_value: str | None) -> dict[str, float] | None:
    """The proxy's own cost figure, if it sent one."""
    try:
        return {"total": float(header_value)} if header_value else None
    except ValueError:
        return None


class LiteLLMClient:
    """Gemini via the LiteLLM proxy, through the OpenAI SDK (sync; the worker is sync)."""

    def __init__(self, settings: AgentSettings, tracer: Tracer, client: Any | None = None) -> None:
        self._settings = settings
        self._tracer = tracer
        if client is None:
            import openai

            if settings.litellm_api_key is None or not settings.litellm_api_key.get_secret_value():
                raise TerminalError("LITELLM_API_KEY is not configured")
            client = openai.OpenAI(
                base_url=settings.litellm_base_url,
                api_key=settings.litellm_api_key.get_secret_value(),
                timeout=settings.agent_llm_timeout_seconds,
                max_retries=settings.agent_llm_max_retries,
            )
        self._client = client
        self._last_model_used: str | None = None
        self._purpose_models: dict[str, str] = {}

    def model_for_purpose(self, purpose: str, override: str | None = None) -> str:
        """Resolve purpose-specific model name, taking into account overrides."""
        if override:
            return override
        if purpose == "diagnose":
            return self._settings.agent_diagnostic_model or self._settings.agent_llm_model
        if purpose in ("generate", "resolution"):
            return self._settings.agent_resolution_model or self._settings.agent_llm_model
        if purpose in ("verify_evidence", "critic"):
            return self._settings.agent_critic_model or self._settings.agent_llm_model
        return self._settings.agent_llm_model

    _model_for_purpose = model_for_purpose  # Preserves internal alias

    @property
    def model_name(self) -> str:
        """Return the exact model selected and used for execution, or the default configured model.

        When a resolution procedure is drafted, this returns the resolution model used
        for drafting. Otherwise, it returns the model used in the most recent LLM execution,
        falling back to `agent_llm_model`.
        """
        if "generate" in self._purpose_models:
            return self._purpose_models["generate"]
        if "resolution" in self._purpose_models:
            return self._purpose_models["resolution"]
        if self._last_model_used is not None:
            return self._last_model_used
        return self._settings.agent_llm_model

    @property
    def last_model_used(self) -> str | None:
        """Return the model that was used in the most recent LLM invocation."""
        return self._last_model_used

    def get_used_model(self, purpose: str) -> str | None:
        """Return the model that was resolved and used for a given purpose, if called."""
        return self._purpose_models.get(purpose)

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: type[M],
        model: str | None = None,
    ) -> M:
        import openai

        settings = self._settings
        selected_model = self.model_for_purpose(purpose, model)
        self._last_model_used = selected_model
        self._purpose_models[purpose] = selected_model
        options: dict[str, Any] = {"max_completion_tokens": settings.agent_llm_max_tokens}
        if settings.agent_llm_reasoning_effort:
            options["reasoning_effort"] = settings.agent_llm_reasoning_effort
        with self._tracer.span(
            f"llm.{purpose}",
            as_type="generation",
            model=selected_model,
            input={"system": system, "prompt": prompt},
            version=settings.agent_prompt_version,
            model_parameters=options,
            metadata={"prompt_name": purpose, "prompt_version": settings.agent_prompt_version},
        ) as generation:
            try:
                raw = self._client.chat.completions.with_raw_response.parse(
                    model=selected_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=schema,
                    **options,
                )
                completion = raw.parse()
            except (openai.RateLimitError, openai.APIConnectionError) as exc:
                raise RetryableError(
                    f"model call failed transiently: {type(exc).__name__}"
                ) from exc
            except openai.APIStatusError as exc:
                if exc.status_code >= 500 or exc.status_code == 408:
                    raise RetryableError(f"model call failed: HTTP {exc.status_code}") from exc
                raise TerminalError(f"model call rejected: HTTP {exc.status_code}") from exc
            except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as exc:
                raise TerminalError(f"{purpose} output unusable: {type(exc).__name__}") from exc
            except ValueError as exc:  # pydantic: the model broke the schema
                raise TerminalError(f"{purpose} output failed validation") from exc

            choice = completion.choices[0] if completion.choices else None
            generation.update(
                model=str(completion.model or settings.agent_llm_model),
                usage_details=usage_details(completion.usage),
                cost_details=cost_details(raw.headers.get(COST_HEADER)),
                metadata={
                    "finish_reason": getattr(choice, "finish_reason", None),
                    "request_id": completion.id,
                },
            )
            if choice is None:
                raise TerminalError(f"{purpose} returned no choices")
            if choice.finish_reason == "content_filter" or choice.message.refusal:
                raise ModelRefusalError(f"model declined the {purpose} request")
            if choice.finish_reason == "length":
                raise TerminalError(f"{purpose} output was truncated")
            parsed = choice.message.parsed
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
    return LiteLLMClient(get_agent_settings(), get_tracer())


@lru_cache
def get_embedding_engine() -> EmbeddingEngine:
    """Process-wide embedding engine singleton: model weights load once per worker."""
    from app.retrieval.embedding import FastEmbedEngine

    return FastEmbedEngine()


__all__ = [
    "COST_HEADER",
    "LLMClient",
    "LiteLLMClient",
    "ModelRefusalError",
    "bounded",
    "cost_details",
    "get_embedding_engine",
    "get_llm",
    "usage_details",
]
