"""Audit event ORM model for durable query and tool audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class AuditEvent(Base):
    """Durable audit trail record for user-visible and system events."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_session_id", "session_id"),
        Index("ix_audit_events_query_id", "query_id"),
        Index("ix_audit_events_subtask_id", "subtask_id"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="SET NULL"),
        nullable=True,
    )
    subtask_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_subtasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    session: Mapped["QASession | None"] = relationship("QASession", back_populates="audit_events")
    query: Mapped["QAQuery | None"] = relationship("QAQuery", back_populates="audit_events")
    subtask: Mapped["QASubtask | None"] = relationship("QASubtask", back_populates="audit_events")
