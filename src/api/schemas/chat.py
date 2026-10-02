"""Request/response models for the admin chatbot API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Maximum characters accepted for one chat message (screening enforces the
#: same bound server-side; content beyond it is a contract violation).
MAX_MESSAGE_CHARS = 6000
MIN_REQUEST_ID_CHARS = 8


class ChatSessionCreatedResponse(BaseModel):
    """The chat secret is shown exactly once; only its hash is stored."""

    session_id: UUID
    chat_secret: str


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    role: str
    content: str
    citations: list[dict[str, Any]] = []
    created_at: datetime


class SubmitMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    request_id: str = Field(
        min_length=MIN_REQUEST_ID_CHARS,
        max_length=64,
        description="Client-generated idempotency key; resubmitting it returns the existing turn.",
    )


class ChatTurnResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    request_id: str
    route: str | None
    status: str
    error_category: str | None
    usage: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None
    messages: list[ChatMessageResponse]


__all__ = [
    "MAX_MESSAGE_CHARS",
    "ChatConversationResponse",
    "ChatMessageResponse",
    "ChatSessionCreatedResponse",
    "ChatTurnResponse",
    "CreateConversationRequest",
    "RenameConversationRequest",
    "SubmitMessageRequest",
]
