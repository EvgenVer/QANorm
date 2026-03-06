"""Verification report ORM model for answer quality checks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import VerificationResult


class VerificationReport(Base):
    """Stored results from coverage, citation, and supportedness checks."""

    __tablename__ = "verification_reports"
    __table_args__ = (
        Index("ix_verification_reports_query_id", "query_id"),
        Index("ix_verification_reports_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    coverage_result: Mapped[VerificationResult] = mapped_column(
        Enum(
            VerificationResult,
            name="verification_result_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    citation_result: Mapped[VerificationResult] = mapped_column(
        Enum(
            VerificationResult,
            name="verification_result_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    hallucination_result: Mapped[VerificationResult] = mapped_column(
        Enum(
            VerificationResult,
            name="verification_result_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    source_labeling_result: Mapped[VerificationResult] = mapped_column(
        Enum(
            VerificationResult,
            name="verification_result_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    warnings_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery"] = relationship("QAQuery", back_populates="verification_reports")
