from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "incident-resolution-platform"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # Connection
    servicenow_instance_url: str = Field(
        ..., description="https://devXXXXX.service-now.com, no trailing slash"
    )

    # OAuth
    servicenow_client_id: str
    servicenow_client_secret: SecretStr
    servicenow_username: str
    servicenow_password: SecretStr
    servicenow_timeout_seconds: int = Field(
        10, description="Timeout for ServiceNow API requests in seconds"
    )
    servicenow_token_expiry_buffer_seconds: int = Field(
        30,
        description="Buffer time before token expiry to refresh the token in seconds",
    )

    @field_validator("servicenow_instance_url")
    @classmethod
    def _validate_instance_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("https://"):
            raise ValueError("servicenow_instance_url must start with https://")
        return v.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
