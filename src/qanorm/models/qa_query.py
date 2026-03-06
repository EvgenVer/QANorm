"""Query ORM model for individual orchestration runs."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import QueryStatus


class QAQuery(Base):
    """Single user query handled by the Stage 2 orchestrator."""

    __tablename__ = "qa_queries"
    __table_args__ = (
        Index("ix_qa_queries_session_id", "session_id"),
        Index("ix_qa_queries_message_id", "message_id"),
        Index("ix_qa_queries_status", "status"),
        Index("ix_qa_queries_created_at", "created_at"),
        Index("ix_qa_queries_finished_at", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[QueryStatus] = mapped_column(
        Enum(
            QueryStatus,
            name="query_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=QueryStatus.PENDING,
        server_default=QueryStatus.PENDING.value,
    )
    query_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requires_freshness_check: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    used_open_web: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    used_trusted_web: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["QASession"] = relationship("QASession", back_populates="queries")
    message: Mapped["QAMessage"] = relationship("QAMessage", back_populates="queries")
    subtasks: Mapped[list["QASubtask"]] = relationship("QASubtask", back_populates="query", cascade="all, delete-orphan")
    evidence_blocks: Mapped[list["QAEvidence"]] = relationship(
        "QAEvidence",
        back_populates="query",
        cascade="all, delete-orphan",
    )
    answers: Mapped[list["QAAnswer"]] = relationship("QAAnswer", back_populates="query", cascade="all, delete-orphan")
    verification_reports: Mapped[list["VerificationReport"]] = relationship(
        "VerificationReport",
        back_populates="query",
        cascade="all, delete-orphan",
    )
    tool_invocations: Mapped[list["ToolInvocation"]] = relationship(
        "ToolInvocation",
        back_populates="query",
        cascade="all, delete-orphan",
    )
    freshness_checks: Mapped[list["FreshnessCheck"]] = relationship(
        "FreshnessCheck",
        back_populates="query",
        cascade="all, delete-orphan",
    )
    security_events: Mapped[list["SecurityEvent"]] = relationship(
        "SecurityEvent",
        back_populates="query",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent",
        back_populates="query",
        cascade="all, delete-orphan",
    )
    search_events: Mapped[list["SearchEvent"]] = relationship(
        "SearchEvent",
        back_populates="query",
        cascade="all, delete-orphan",
    )
