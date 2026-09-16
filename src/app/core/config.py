from enum import StrEnum
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


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


class Settings(RetrievalSettings):
    app_name: str = "incident-resolution-platform"
    app_version: str = Field(default_factory=_get_version)
    log_level: str = "INFO"
    environment: Environment = Environment.DEVELOPMENT

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
