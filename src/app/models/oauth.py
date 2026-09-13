from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str | None = None
    refresh_token: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(seconds=self.expires_in)

    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at
