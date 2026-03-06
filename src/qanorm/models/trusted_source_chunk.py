"""Trusted source chunk ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class TrustedSourceChunk(Base):
    """Chunked searchable text extracted from a trusted source document."""

    __tablename__ = "trusted_source_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_trusted_source_chunks_document_chunk"),
        Index("ix_trusted_source_chunks_document_id", "document_id"),
        Index("ix_trusted_source_chunks_created_at", "created_at"),
        Index("ix_trusted_source_chunks_text_tsv", "text_tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trusted_source_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped["TrustedSourceDocument"] = relationship("TrustedSourceDocument", back_populates="chunks")
