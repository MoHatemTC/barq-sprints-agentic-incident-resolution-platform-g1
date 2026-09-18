"""Pydantic V2 schemas for runtime configuration inspection with secret redaction."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import RetrievalMode, Settings

REDACTED_SENTINEL = "***REDACTED***"


class RedactedConfigResponse(BaseModel):
    """Schema representing runtime configuration with all sensitive secrets redacted."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Metadata
    app_name: str = Field(..., description="Application name")
    app_version: str = Field(..., description="Semantic version of running application")
    environment: str = Field(
        ..., description="Runtime environment (development, staging, production)"
    )
    log_level: str = Field(..., description="Configured logging level")
    bind_ip: str = Field(..., description="Bound IP address for listener")

    # ServiceNow
    servicenow_instance_url: str = Field(..., description="Base ServiceNow instance URL")
    servicenow_client_id: str = Field(..., description="ServiceNow OAuth client ID")
    servicenow_username: str = Field(..., description="ServiceNow API username")
    client_secret: str = Field(
        default=REDACTED_SENTINEL,
        description="Redacted ServiceNow OAuth client secret",
    )
    servicenow_client_secret: str = Field(
        default=REDACTED_SENTINEL,
        description="Redacted ServiceNow OAuth client secret",
    )
    servicenow_password: str = Field(
        default=REDACTED_SENTINEL,
        description="Redacted ServiceNow API user password",
    )
    servicenow_timeout_seconds: int = Field(
        ..., description="Timeout in seconds for ServiceNow requests"
    )
    servicenow_token_expiry_buffer_seconds: int = Field(
        ...,
        description="Buffer seconds before token expiration to refresh",
    )
    servicenow_kb_id: str = Field(..., description="Target knowledge base sys_id")

    # Webhook
    webhook_auth_token: str = Field(
        default=REDACTED_SENTINEL,
        description="Redacted webhook bearer authentication secret",
    )

    # PostgreSQL
    postgres_host: str = Field(..., description="PostgreSQL server hostname")
    postgres_port: int = Field(..., description="PostgreSQL server port")
    postgres_db: str = Field(..., description="Target database name")
    postgres_user: str = Field(..., description="Database connection username")
    postgres_password: str = Field(
        default=REDACTED_SENTINEL,
        description="Redacted PostgreSQL database password",
    )

    # Redis
    redis_host: str = Field(..., description="Redis cache and queue hostname")
    redis_port: int = Field(..., description="Redis port")
    redis_password: str = Field(
        default=REDACTED_SENTINEL,
        description="Redacted Redis authentication password",
    )

    # Qdrant & Embeddings
    qdrant_url: str = Field(..., description="Qdrant vector engine URL")
    qdrant_http_port: int = Field(..., description="Qdrant HTTP port")
    qdrant_grpc_port: int = Field(..., description="Qdrant gRPC port")
    qdrant_collection_name: str = Field(
        ..., description="Active incident knowledge base collection name"
    )
    qdrant_log_level: str = Field(..., description="Qdrant logging verbosity")
    dense_embedding_model: str = Field(
        ..., description="Dense embedding transformer model identifier"
    )
    sparse_embedding_model: str = Field(..., description="Sparse keyword model identifier")
    retrieval_mode: RetrievalMode = Field(
        default=RetrievalMode.HYBRID,
        description="Active retrieval strategy: 'dense_only', 'hybrid', or 'hybrid_reranked'",
    )

    # Feature Flags
    active_feature_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Active platform feature flag statuses",
    )

    @classmethod
    def from_settings(cls, settings: Settings) -> RedactedConfigResponse:
        """Construct a sanitized response directly from runtime application settings."""
        return cls(
            app_name=settings.app_name,
            app_version=settings.app_version,
            environment=str(settings.environment),
            log_level=settings.log_level,
            bind_ip=settings.bind_ip,
            servicenow_instance_url=settings.servicenow_instance_url,
            servicenow_client_id=settings.servicenow_client_id,
            client_secret=REDACTED_SENTINEL,
            servicenow_client_secret=REDACTED_SENTINEL,
            servicenow_username=settings.servicenow_username,
            servicenow_password=REDACTED_SENTINEL,
            servicenow_timeout_seconds=settings.servicenow_timeout_seconds,
            servicenow_token_expiry_buffer_seconds=settings.servicenow_token_expiry_buffer_seconds,
            servicenow_kb_id=settings.servicenow_kb_id,
            webhook_auth_token=REDACTED_SENTINEL,
            postgres_host=settings.postgres_host,
            postgres_port=settings.postgres_port,
            postgres_db=settings.postgres_db,
            postgres_user=settings.postgres_user,
            postgres_password=REDACTED_SENTINEL,
            redis_host=settings.redis_host,
            redis_port=settings.redis_port,
            redis_password=REDACTED_SENTINEL,
            qdrant_url=settings.qdrant_url,
            qdrant_http_port=settings.qdrant_http_port,
            qdrant_grpc_port=settings.qdrant_grpc_port,
            qdrant_collection_name=settings.qdrant_collection_name,
            qdrant_log_level=settings.qdrant_log_level,
            dense_embedding_model=settings.dense_embedding_model,
            sparse_embedding_model=settings.sparse_embedding_model,
            retrieval_mode=getattr(settings, "retrieval_mode", RetrievalMode.HYBRID),
            active_feature_flags=dict(getattr(settings, "active_feature_flags", {})),
        )
