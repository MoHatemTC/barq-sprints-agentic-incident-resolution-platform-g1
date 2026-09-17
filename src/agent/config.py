"""Agent runtime configuration (S2.5).

The retrieval, safety and risk values default to the ones the BARQ IT Service
Operations Manual (Edition 4.0, §11.7) states for the AI Suggested Response pilot:
``top_k=5``, ``threshold=0.55``, ``floor=0.45``, ``risk_p=[1]``. The manual says to
read them "from the repository, not from a photograph of somebody's screen" — this
file is that repository copy, and every value can be overridden by environment
variable under change control.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AGENT_VERSION = "s2.5-graph-1.0.0"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_version: str = AGENT_VERSION
    agent_graph_backend: Literal["langgraph", "stub"] = Field(
        default="langgraph",
        description="'stub' keeps S2.3's simulated graph (failure-injection demo and the "
        "S2.3 integration suite); 'langgraph' runs the eleven-node state machine.",
    )
    agent_checkpointer_backend: Literal["postgres", "memory"] = Field(
        default="postgres",
        description="'postgres' persists checkpoints to workflow_state (S2.2); 'memory' "
        "is for tests and local runs without a database.",
    )

    # -- model -------------------------------------------------------------------
    litellm_base_url: str = Field(
        default="https://management.sprints.ai/litellm",
        description="Sprints LiteLLM proxy (OpenAI-compatible).",
    )
    litellm_api_key: SecretStr | None = Field(
        default=None, description="Per-learner LiteLLM key (Gemini models only)."
    )
    agent_llm_model: str = Field(
        default="gemini/gemini-3.5-flash",
        description="A model the key allows: names must start with 'gemini/'.",
    )
    agent_llm_reasoning_effort: Literal["low", "medium", "high"] | None = Field(
        default=None, description="Unset = the model's default thinking level."
    )
    agent_llm_max_tokens: int = Field(default=16000, gt=0)
    agent_llm_timeout_seconds: float = Field(default=60.0, gt=0)
    agent_llm_max_retries: int = Field(default=2, ge=0)
    agent_prompt_version: str = "v1"

    # -- manual §11.7: search ------------------------------------------------------
    agent_retrieval_top_k: int = Field(default=5, gt=0)
    agent_retrieval_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Minimum dense cosine similarity of the best chunk for the evidence "
        "to count. Below it the incident is escalated as 'no evidence'.",
    )

    agent_max_security_level: Literal["public", "internal", "restricted"] = Field(
        default="internal",
        description="Highest article audience the agent may quote. The draft lands in a "
        "field every support user can read, so restricted articles are excluded unless "
        "this is raised deliberately (S1.4 #45 default).",
    )

    # -- manual §11.7: safety ------------------------------------------------------
    agent_confidence_floor: float = Field(default=0.45, ge=0.0, le=1.0)
    agent_risk_priorities: list[int] = Field(
        default_factory=lambda: [1],
        description="Priorities that leave the automated path before any search runs.",
    )

    # -- manual §11.3: eligibility -------------------------------------------------
    agent_supported_categories: list[str] = Field(
        default_factory=lambda: ["network", "software", "hardware", "inquiry"],
        description="ServiceNow incident categories the corpus covers.",
    )
    agent_max_incident_chars: int = Field(
        default=6000,
        gt=0,
        description="Length bound on incident text sent to the model (manual §11.6).",
    )

    # -- write-back ------------------------------------------------------------------
    agent_write_back_enabled: bool = Field(
        default=True,
        description="False = dry run: the act node records what it would write.",
    )


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()


__all__ = ["AGENT_VERSION", "AgentSettings", "get_agent_settings"]
