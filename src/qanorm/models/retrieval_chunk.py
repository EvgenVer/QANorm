"""Retrieval-oriented chunk model built on top of normalized document nodes."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class RetrievalChunk(Base):
    """Search-optimized chunk derived from one active document version."""

    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index", name="uq_retrieval_chunks_version_chunk_index"),
        Index("ix_retrieval_chunks_document_id", "document_id"),
        Index("ix_retrieval_chunks_document_version_id", "document_version_id"),
        Index("ix_retrieval_chunks_start_node_id", "start_node_id"),
        Index("ix_retrieval_chunks_end_node_id", "end_node_id"),
        Index("ix_retrieval_chunks_chunk_hash", "chunk_hash"),
        Index("ix_retrieval_chunks_is_active", "is_active"),
        Index("ix_retrieval_chunks_chunk_text_tsv", "chunk_text_tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    end_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(100), nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_text_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    document: Mapped["Document"] = relationship("Document")
    document_version: Mapped["DocumentVersion"] = relationship("DocumentVersion")
    start_node: Mapped["DocumentNode"] = relationship("DocumentNode", foreign_keys=[start_node_id])
    end_node: Mapped["DocumentNode"] = relationship("DocumentNode", foreign_keys=[end_node_id])
    evidence_blocks: Mapped[list["QAEvidence"]] = relationship("QAEvidence", back_populates="chunk")
