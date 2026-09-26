import os
import sys
from enum import StrEnum
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class RetrievalMode(StrEnum):
    DENSE_ONLY = "dense_only"
    HYBRID = "hybrid"
    HYBRID_RERANKED = "hybrid_reranked"


def _get_version() -> str:
    try:
        return version("barq-sprints-agentic-incident-resolution-platform-g1")
    except PackageNotFoundError:
        return "0.1.0"


_RUNNING_UNDER_PYTEST = "pytest" in sys.modules
_IGNORE_DOTENV = _RUNNING_UNDER_PYTEST and os.environ.get("SERVICENOW_LIVE_TESTS") != "1"
_ENV_FILE = None if _IGNORE_DOTENV else ".env"


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_http_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_collection_name: str = "incident_knowledge_base"
    qdrant_log_level: str = "INFO"

    # Embedding Models
    dense_embedding_model: str = "BAAI/bge-small-en-v1.5"
    sparse_embedding_model: str = "Qdrant/bm25"
    # Retrieval
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_top_k: int = 5
    rerank_candidate_limit: int = 20  # must be larger than rerank_top_k

    @field_validator("rerank_candidate_limit")
    @classmethod
    def validate_candidate_limit(cls, v: int, info) -> int:
        # Read ``rerank_top_k``, the field that exists. Reading ``rerank_top_n``
        # (a name no field has) always fell back to 5, so any candidate limit
        # >= 5 passed and the check could never fail (#150).
        top_k = info.data.get("rerank_top_k", 5)
        if v < top_k:
            raise ValueError(f"rerank_candidate_limit ({v}) must be >= rerank_top_k ({top_k})")
        return v


class Settings(RetrievalSettings):
    # No model_config here on purpose. It is inherited from RetrievalSettings, and
    # re-declaring it hard-codes env_file=".env" on the subclass. #77 makes the base
    # class use env_file=None under pytest so unit tests cannot read a developer's
    # .env; a subclass override wins over that, so Settings would still load .env
    # during tests and silently undo #77's isolation. hide_input_in_errors, which is
    # what this PR needs, applies from the base class.
    app_name: str = "incident-resolution-platform"
    app_version: str = Field(default_factory=_get_version)
    log_level: str = "INFO"
    environment: Environment = Environment.DEVELOPMENT

    # Feature Flags
    active_feature_flags: dict[str, bool] = Field(
        default_factory=lambda: {
            "hitl_approvals": True,
            "dlq_replay": True,
            "eval_benchmarks": False,
            "auto_remediation": False,
        }
    )

    # Host & Network Binding (Security)
    bind_ip: str = "127.0.0.1"

    # Connection
    servicenow_instance_url: str = Field(
        ...,
        description="https://devXXXXX.service-now.com, no trailing slash",
    )

    # OAuth
    servicenow_client_id: str
    servicenow_client_secret: SecretStr
    servicenow_username: str
    servicenow_password: SecretStr
    servicenow_timeout_seconds: int = Field(
        default=10,
        description="Timeout for ServiceNow API requests in seconds",
    )
    servicenow_token_expiry_buffer_seconds: int = Field(
        default=30,
        description="Buffer time before token expiry to refresh the token in seconds",
    )
    servicenow_kb_id: str = Field(
        default="",
        description="Target Knowledge Base sys_id for KB publishing",
    )
    # KB publishing identity (#91). scripts/publish_kb.py signs in as this user when set,
    # so the incident integration user needs no knowledge-base rights.
    servicenow_kb_username: str = ""
    servicenow_kb_password: SecretStr | None = None

    # API and inbound ServiceNow webhook authentication.
    #
    # Two credentials, two audiences. ServiceNow only ever holds the OAuth client
    # below and receives short-lived JWTs for the webhook. Operators exchange
    # webhook_auth_token for a *separate* operator JWT, whose subject and roles
    # come from the two fields after it -- so ServiceNow's credential cannot reach
    # /approvals, /config, /dlq, /executions or /eval (#136, #148).
    webhook_auth_token: SecretStr = Field(
        ...,
        description="Client secret of the operator API credential, exchanged for an "
        "operator access token at /api/v1/oauth/token. Never accepted as a bearer "
        "token itself and never accepted by the ServiceNow webhook.",
    )
    operator_client_id: str = Field(
        default="barq-operator",
        min_length=1,
        description="OAuth client id for human operators; the subject of every "
        "operator token and therefore the value recorded as decided_by",
    )
    operator_roles: list[str] = Field(
        default=["operator", "approver"],
        description="Roles granted to operator tokens, carried in the signed "
        "'roles' claim that require_role() reads",
    )
    webhook_oauth_client_id: str = Field(
        ...,
        min_length=1,
        description="OAuth client ID used by the ServiceNow outbound REST message",
    )
    webhook_oauth_client_secret: SecretStr = Field(
        ...,
        min_length=16,
        description="OAuth client secret used only at the token endpoint",
    )
    webhook_oauth_signing_key: SecretStr = Field(
        ...,
        min_length=32,
        description="HMAC key used to sign short-lived webhook access tokens",
    )

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "barq_incident_dev"
    postgres_user: str = "postgres"
    postgres_password: SecretStr | None = None

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr | None = None
    redis_pool_max_connections: int = 20
    redis_socket_timeout: float = 5.0

    # Workers (S2.3) — every value is configuration-driven; the worker code must
    # contain no literal concurrency/retry/timeout numbers.
    worker_concurrency: int = Field(
        default=4,
        description="Celery worker concurrency (prefork child processes)",
    )
    worker_max_retries: int = Field(
        default=5,
        ge=1,
        description="Maximum total attempts per event (initial + retries) before "
        "dead-lettering; also written to retry_state.max_attempts so the database "
        "enforces the same budget: attempt_count may never exceed it. "
        "NOTE: a value of 1 means ONE attempt with ZERO retries — the event "
        "dead-letters on the first failure.",
    )
    worker_backoff_base: float = Field(
        default=1.0,
        description="Exponential backoff base in seconds: delay = base * 2**(attempt-1)",
    )
    worker_backoff_max: float = Field(
        default=60.0,
        description="Upper bound for a single backoff delay in seconds",
    )
    worker_backoff_jitter: bool = Field(
        default=True,
        description="Full jitter on backoff delays so parallel retriers do not sync up",
    )
    worker_soft_time_limit: int = Field(
        default=120,
        description="Soft task time limit (s): raises SoftTimeLimitExceeded inside the "
        "task so it can log and retry. Sized for an assumed sub-90s graph run; "
        "revisit when Sprint 3 lands real latency numbers",
    )
    worker_time_limit: int = Field(
        default=150,
        description="Hard task time limit (s): SIGKILL the worker process; must exceed "
        "the soft limit to leave room for cleanup",
    )
    worker_prefetch: int = Field(
        default=1,
        description="Prefetch multiplier: 1 for long-running tasks so workers do not "
        "hoard jobs while others sit idle",
    )
    worker_repo_backend: str = Field(
        default="postgres",
        description="Worker repository backend: 'postgres' or 'memory' (tests/stand-in)",
    )
    # Langfuse Tracing (optional — integration is disabled when keys are absent)
    langfuse_public_key: str | None = Field(
        default=None,
        description="Langfuse project public key (tracing disabled when absent)",
    )
    langfuse_secret_key: SecretStr | None = Field(
        default=None,
        description="Langfuse project secret key (tracing disabled when absent)",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse server URL",
    )

    @field_validator("servicenow_instance_url")
    @classmethod
    def validate_instance_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("https://"):
            raise ValueError("servicenow_instance_url must start with https://")
        return v.rstrip("/")


@lru_cache
def get_retrieval_settings() -> RetrievalSettings:
    return RetrievalSettings()


def kb_publisher_settings(settings: Settings) -> Settings:
    """Settings signed in as the KB publisher, when SERVICENOW_KB_USERNAME is set."""
    if not settings.servicenow_kb_username:
        return settings
    password = settings.servicenow_kb_password
    if password is None or not password.get_secret_value():
        raise ValueError("SERVICENOW_KB_PASSWORD must be set together with SERVICENOW_KB_USERNAME")
    return settings.model_copy(
        update={
            "servicenow_username": settings.servicenow_kb_username,
            "servicenow_password": settings.servicenow_kb_password,
        }
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
