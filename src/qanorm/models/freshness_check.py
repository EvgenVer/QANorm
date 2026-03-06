"""Freshness check ORM model for stale detection state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import FreshnessCheckStatus


class FreshnessCheck(Base):
    """Stored result of a document freshness verification run."""

    __tablename__ = "freshness_checks"
    __table_args__ = (
        Index("ix_freshness_checks_query_id", "query_id"),
        Index("ix_freshness_checks_document_id", "document_id"),
        Index("ix_freshness_checks_document_version_id", "document_version_id"),
        Index("ix_freshness_checks_check_status", "check_status"),
        Index("ix_freshness_checks_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    check_status: Mapped[FreshnessCheckStatus] = mapped_column(
        Enum(
            FreshnessCheckStatus,
            name="freshness_check_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=FreshnessCheckStatus.PENDING,
        server_default=FreshnessCheckStatus.PENDING.value,
    )
    local_edition_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remote_edition_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    refresh_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery"] = relationship("QAQuery", back_populates="freshness_checks")
    document: Mapped["Document"] = relationship("Document")
    document_version: Mapped["DocumentVersion | None"] = relationship("DocumentVersion")
    refresh_job: Mapped["IngestionJob | None"] = relationship("IngestionJob")
