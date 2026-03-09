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


class AnswerCitationResponse(BaseModel):
    """Serialized citation shown in a structured answer section."""

    title: str
    edition_label: str | None = None
    locator: str | None = None
    quote: str | None = None
    is_normative: bool
    requires_verification: bool


class AnswerSectionResponse(BaseModel):
    """Structured answer section for the web and Telegram adapters."""

    heading: str
    body: str
    source_kind: str
    citations: list[AnswerCitationResponse] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    """Serialized structured answer returned by later answer endpoints."""

    answer_text: str
    markdown: str
    answer_format: str
    coverage_status: str
    has_stale_sources: bool
    has_external_sources: bool
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sections: list[AnswerSectionResponse] = Field(default_factory=list)
    model_name: str | None = None
