"""Subtask ORM model for decomposed query plans."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import SubtaskStatus


class QASubtask(Base):
    """Single decomposed unit of work for a user query."""

    __tablename__ = "qa_subtasks"
    __table_args__ = (
        Index("ix_qa_subtasks_query_id", "query_id"),
        Index("ix_qa_subtasks_parent_subtask_id", "parent_subtask_id"),
        Index("ix_qa_subtasks_status", "status"),
        Index("ix_qa_subtasks_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_subtask_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_subtasks.id", ondelete="CASCADE"),
        nullable=True,
    )
    subtask_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SubtaskStatus] = mapped_column(
        Enum(
            SubtaskStatus,
            name="subtask_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=SubtaskStatus.PENDING,
        server_default=SubtaskStatus.PENDING.value,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    query: Mapped["QAQuery"] = relationship("QAQuery", back_populates="subtasks")
    parent_subtask: Mapped["QASubtask | None"] = relationship("QASubtask", remote_side=[id], back_populates="child_subtasks")
    child_subtasks: Mapped[list["QASubtask"]] = relationship("QASubtask", back_populates="parent_subtask", cascade="all, delete-orphan")
    evidence_blocks: Mapped[list["QAEvidence"]] = relationship(
        "QAEvidence",
        back_populates="subtask",
        cascade="all, delete-orphan",
    )
    tool_invocations: Mapped[list["ToolInvocation"]] = relationship(
        "ToolInvocation",
        back_populates="subtask",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent",
        back_populates="subtask",
        cascade="all, delete-orphan",
    )
    search_events: Mapped[list["SearchEvent"]] = relationship(
        "SearchEvent",
        back_populates="subtask",
        cascade="all, delete-orphan",
    )
