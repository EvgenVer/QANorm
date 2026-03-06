"""Answer ORM model for persisted query responses."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import AnswerStatus, CoverageStatus


class QAAnswer(Base):
    """Final persisted answer and top-level answer metadata."""

    __tablename__ = "qa_answers"
    __table_args__ = (
        UniqueConstraint("query_id", name="uq_qa_answers_query_id"),
        Index("ix_qa_answers_status", "status"),
        Index("ix_qa_answers_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_format: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[AnswerStatus] = mapped_column(
        Enum(
            AnswerStatus,
            name="answer_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=AnswerStatus.DRAFT,
        server_default=AnswerStatus.DRAFT.value,
    )
    coverage_status: Mapped[CoverageStatus] = mapped_column(
        Enum(
            CoverageStatus,
            name="coverage_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=CoverageStatus.PARTIAL,
        server_default=CoverageStatus.PARTIAL.value,
    )
    has_stale_sources: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    has_external_sources: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery"] = relationship("QAQuery", back_populates="answers")
