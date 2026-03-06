"""Tool invocation ORM model for audited tool usage."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import ToolInvocationStatus


class ToolInvocation(Base):
    """Audited tool call performed during a query run."""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index("ix_tool_invocations_query_id", "query_id"),
        Index("ix_tool_invocations_subtask_id", "subtask_id"),
        Index("ix_tool_invocations_status", "status"),
        Index("ix_tool_invocations_tool_name", "tool_name"),
        Index("ix_tool_invocations_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    subtask_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_subtasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_scope: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[ToolInvocationStatus] = mapped_column(
        Enum(
            ToolInvocationStatus,
            name="tool_invocation_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=ToolInvocationStatus.PENDING,
        server_default=ToolInvocationStatus.PENDING.value,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery"] = relationship("QAQuery", back_populates="tool_invocations")
    subtask: Mapped["QASubtask | None"] = relationship("QASubtask", back_populates="tool_invocations")
