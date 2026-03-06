"""Trusted source document ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class TrustedSourceDocument(Base):
    """Normalized external document fetched from an allowlisted source."""

    __tablename__ = "trusted_source_documents"
    __table_args__ = (
        UniqueConstraint("source_url", name="uq_trusted_source_documents_source_url"),
        Index("ix_trusted_source_documents_source_domain", "source_domain"),
        Index("ix_trusted_source_documents_content_hash", "content_hash"),
        Index("ix_trusted_source_documents_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    last_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trusted_source_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    last_sync_run: Mapped["TrustedSourceSyncRun | None"] = relationship("TrustedSourceSyncRun", back_populates="documents")
    chunks: Mapped[list["TrustedSourceChunk"]] = relationship(
        "TrustedSourceChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )
