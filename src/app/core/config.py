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


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID


class Settings(RetrievalSettings):
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

    # WebHook
    webhook_auth_token: str = Field(
        ...,
        description="Bearer token for webhook authentication",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
