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
    replace_existing: bool = False


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


class EvidenceResponse(BaseModel):
    """Serialized evidence block shown alongside the final answer."""

    id: UUID
    source_kind: str
    source_title: str | None = None
    source_url: str | None = None
    source_domain: str | None = None
    document_id: UUID | None = None
    document_title: str | None = None
    document_version_id: UUID | None = None
    chunk_id: UUID | None = None
    locator: str | None = None
    locator_end: str | None = None
    edition_label: str | None = None
    quote: str | None = None
    chunk_text: str | None = None
    freshness_status: str
    is_normative: bool
    requires_verification: bool
    relevance_score: float | None = None
    selection_metadata: dict[str, Any] | None = None


class QueryDetailResponse(BaseModel):
    """Combined query payload used by the web and Telegram transports."""

    id: UUID
    session_id: UUID
    message_id: UUID
    status: str
    query_type: str | None = None
    intent: str | None = None
    clarification_required: bool = False
    document_hints: list[str] = Field(default_factory=list)
    locator_hints: list[str] = Field(default_factory=list)
    retrieval_mode: str | None = None
    document_resolution: dict[str, Any] | None = None
    query_text: str
    requires_freshness_check: bool
    used_open_web: bool
    used_trusted_web: bool
    created_at: datetime | None = None
    finished_at: datetime | None = None
    answer: AnswerResponse | None = None
    evidence: list[EvidenceResponse] = Field(default_factory=list)
