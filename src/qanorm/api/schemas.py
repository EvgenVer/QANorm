"""Pydantic schemas for Stage 2 API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from qanorm.db.types import SessionChannel, SessionStatus


class CreateSessionRequest(BaseModel):
    """Request payload for creating a chat session."""

    channel: SessionChannel
    external_user_id: str | None = None
    external_chat_id: str | None = None


class SessionResponse(BaseModel):
    """Serialized session payload returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: SessionChannel
    external_user_id: str | None = None
    external_chat_id: str | None = None
    status: SessionStatus
    session_summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class MessageRequest(BaseModel):
    """User message payload used to create a query run."""

    content: str = Field(min_length=1)
    metadata_json: dict[str, Any] | None = None
    query_type: str | None = None


class MessageResponse(BaseModel):
    """Serialized message and query linkage payload."""

    message_id: UUID
    session_id: UUID
    query_id: UUID | None = None
    role: str
    content: str
    created_at: datetime | None = None


class StreamEvent(BaseModel):
    """Structured SSE event published for one query."""

    event: str
    query_id: UUID
    data: dict[str, Any]
    created_at: datetime
