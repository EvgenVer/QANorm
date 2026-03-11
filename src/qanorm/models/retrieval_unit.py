"""Retrieval unit ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ModuleNotFoundError:  # pragma: no cover - imported by migrations/tests without pgvector installed
    from sqlalchemy.types import UserDefinedType

    class Vector(UserDefinedType):
        """Fallback VECTOR type used in environments without pgvector bindings."""

        cache_ok = True

        def __init__(self, dimensions: int) -> None:
            self.dimensions = dimensions

        def get_col_spec(self, **_: object) -> str:
            return f"VECTOR({self.dimensions})"

from qanorm.db.base import Base
from qanorm.db.types import EMBEDDING_DIMENSIONS


class RetrievalUnit(Base):
    """Derived retrieval chunk built on top of Stage 1 document versions."""

    __tablename__ = "retrieval_units"
    __table_args__ = (
        Index("ix_retrieval_units_document_version_id", "document_version_id"),
        Index("ix_retrieval_units_unit_type", "unit_type"),
        Index("ix_retrieval_units_anchor_node_id", "anchor_node_id"),
        Index("ix_retrieval_units_chunk_hash", "chunk_hash"),
        Index("ix_retrieval_units_text_tsv", "text_tsv", postgresql_using="gin"),
        Index("ix_retrieval_units_embedding", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    unit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    anchor_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    start_order_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_order_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator_primary: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document_version: Mapped["DocumentVersion"] = relationship("DocumentVersion", back_populates="retrieval_units")
    anchor_node: Mapped["DocumentNode | None"] = relationship("DocumentNode", back_populates="retrieval_units")
