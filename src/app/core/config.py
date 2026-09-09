from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "incident-resolution-platform"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    environment: str = "development"
    app_log_level: str = "INFO"

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

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_http_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_collection_name: str = "incident_knowledge_base"
    qdrant_log_level: str = "INFO"

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

    # Embedding Models
    dense_embedding_model: str = "BAAI/bge-small-en-v1.5"
    sparse_embedding_model: str = "Qdrant/bm25"

    @field_validator("servicenow_instance_url")
    @classmethod
    def validate_instance_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("https://"):
            raise ValueError("servicenow_instance_url must start with https://")
        return v.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
