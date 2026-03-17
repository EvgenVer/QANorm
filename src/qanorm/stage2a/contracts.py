"""Pydantic contracts shared by the Stage 2A / Stage 2B agent layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from qanorm.stage2a.retrieval.engine import DocumentCandidate, RetrievalHit


class Stage2AQueryRequest(BaseModel):
    """One user query routed through the Stage 2A runtime."""

    query_text: str = Field(min_length=1)
    debug: bool = False


class RuntimeEventDTO(BaseModel):
    """One runtime event streamed to the chat UI while the answer is being built."""

    event_type: Literal[
        "query_received",
        "query_rewritten",
        "controller_started",
        "tool_started",
        "tool_finished",
        "evidence_updated",
        "composer_started",
        "verifier_started",
        "answer_ready",
        "warning",
    ]
    message: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    level: Literal["info", "warning"] = "info"
    is_terminal: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationMessageDTO(BaseModel):
    """One message inside one local Streamlit chat session."""

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    answer_mode: Literal["direct", "partial", "clarify", "no_answer"] | None = None
    result_payload: dict[str, Any] | None = None


class ConversationMemoryDTO(BaseModel):
    """Bounded conversational memory stored only inside Streamlit session state."""

    conversation_summary: str = ""
    active_document_hints: list[str] = Field(default_factory=list)
    active_locator_hints: list[str] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)


class Stage2AChatSessionDTO(BaseModel):
    """One local browser-scoped chat session for Stage 2B."""

    session_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    messages: list[ConversationMessageDTO] = Field(default_factory=list)
    memory: ConversationMemoryDTO = Field(default_factory=ConversationMemoryDTO)
    last_result: dict[str, Any] | None = None
    runtime_events: list[RuntimeEventDTO] = Field(default_factory=list)


class DocumentCandidateDTO(BaseModel):
    """Serializable document candidate produced by the retrieval layer."""

    document_id: UUID
    document_version_id: UUID | None = None
    score: float = Field(ge=0.0)
    reason: str = Field(min_length=1)
    matched_value: str | None = None
    display_code: str = Field(min_length=1)
    title: str | None = None

    @classmethod
    def from_candidate(cls, candidate: DocumentCandidate) -> "DocumentCandidateDTO":
        """Convert one retrieval-engine candidate into a stable DTO."""

        return cls(
            document_id=candidate.document_id,
            document_version_id=candidate.document_version_id,
            score=candidate.score,
            reason=candidate.reason,
            matched_value=candidate.matched_value,
            display_code=candidate.display_code,
            title=candidate.title,
        )


class RetrievalHitDTO(BaseModel):
    """Serializable retrieval hit used in tool observations and evidence packs."""

    source_kind: str = Field(min_length=1)
    score: float = Field(ge=0.0)
    document_id: UUID
    document_version_id: UUID
    document_display_code: str | None = None
    document_title: str | None = None
    node_id: UUID | None = None
    retrieval_unit_id: UUID | None = None
    order_index: int | None = None
    locator: str | None = None
    heading_path: str | None = None
    text: str = Field(min_length=1)

    @classmethod
    def from_hit(cls, hit: RetrievalHit) -> "RetrievalHitDTO":
        """Convert one retrieval-engine hit into a stable DTO."""

        return cls(
            source_kind=hit.source_kind,
            score=hit.score,
            document_id=hit.document_id,
            document_version_id=hit.document_version_id,
            document_display_code=hit.document_display_code,
            document_title=hit.document_title,
            node_id=hit.node_id,
            retrieval_unit_id=hit.retrieval_unit_id,
            order_index=hit.order_index,
            locator=hit.locator,
            heading_path=hit.heading_path,
            text=hit.text,
        )


class ToolObservationDTO(BaseModel):
    """Structured observation returned by one retrieval tool invocation."""

    tool_name: str = Field(min_length=1)
    message: str | None = None
    document_candidates: list[DocumentCandidateDTO] = Field(default_factory=list)
    hits: list[RetrievalHitDTO] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceItemDTO(BaseModel):
    """Compact grounded evidence item consumed by answer modules."""

    evidence_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    document_id: UUID
    document_version_id: UUID
    document_display_code: str | None = None
    document_title: str | None = None
    node_id: UUID | None = None
    retrieval_unit_id: UUID | None = None
    locator: str | None = None
    heading_path: str | None = None
    score: float = Field(ge=0.0)
    text: str = Field(min_length=1)

    @classmethod
    def from_hit(cls, hit: RetrievalHit, *, evidence_id: str) -> "EvidenceItemDTO":
        """Create one evidence item from one retrieval hit."""

        return cls(
            evidence_id=evidence_id,
            source_kind=hit.source_kind,
            document_id=hit.document_id,
            document_version_id=hit.document_version_id,
            document_display_code=hit.document_display_code,
            document_title=hit.document_title,
            node_id=hit.node_id,
            retrieval_unit_id=hit.retrieval_unit_id,
            locator=hit.locator,
            heading_path=hit.heading_path,
            score=hit.score,
            text=hit.text,
        )


class AnswerClaimDTO(BaseModel):
    """One answer claim linked back to evidence ids."""

    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool = True


class Stage2AAnswerDTO(BaseModel):
    """Final grounded answer emitted by the Stage 2A runtime."""

    mode: Literal["direct", "partial", "clarify", "no_answer"]
    answer_text: str = Field(min_length=1)
    claims: list[AnswerClaimDTO] = Field(default_factory=list)
    evidence: list[EvidenceItemDTO] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    debug_trace: list[str] = Field(default_factory=list)
