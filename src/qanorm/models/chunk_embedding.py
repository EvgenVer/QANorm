"""Deduplicated chunk embedding storage keyed by chunk hash and model identity."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from qanorm.db.base import Base
from qanorm.db.types import EMBEDDING_DIMENSIONS

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - development fallback mirrors the Alembic fallback.
    from sqlalchemy.types import UserDefinedType

    class Vector(UserDefinedType):
        """Fallback VECTOR type for environments without pgvector installed."""

        cache_ok = True

        def __init__(self, dimensions: int | None = None) -> None:
            self.dimensions = dimensions

        def get_col_spec(self, **kw) -> str:  # noqa: ANN003 - SQLAlchemy callback signature.
            return "VECTOR" if self.dimensions is None else f"VECTOR({self.dimensions})"


class ChunkEmbedding(Base):
    """Deduplicated embedding row shared by all chunks with the same hash."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_hash",
            "model_provider",
            "model_name",
            "model_revision",
            name="uq_chunk_embeddings_hash_model",
        ),
        Index("ix_chunk_embeddings_chunk_hash", "chunk_hash"),
        Index("ix_chunk_embeddings_model_provider", "model_provider"),
        Index("ix_chunk_embeddings_model_name", "model_name"),
        Index(
            "ix_chunk_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=EMBEDDING_DIMENSIONS)
    chunk_text_sample: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Keep the column dimension fixed so pgvector can build an ANN index.
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
