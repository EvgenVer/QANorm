"""Document node ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from qanorm.db.base import Base
from qanorm.db.types import EMBEDDING_DIMENSIONS

try:
    from pgvector.sqlalchemy import Vector
except ModuleNotFoundError:
    class Vector(UserDefinedType):
        """Fallback VECTOR type for environments without pgvector installed."""

        cache_ok = True

        def __init__(self, dimensions: int) -> None:
            self.dimensions = dimensions

        def get_col_spec(self, **_: object) -> str:
            return f"VECTOR({self.dimensions})"


class DocumentNode(Base):
    """Normalized structural node of a document."""

    __tablename__ = "document_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    parent_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_nodes.id", ondelete="CASCADE"), nullable=True)
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document_version: Mapped["DocumentVersion"] = relationship("DocumentVersion", back_populates="nodes")
    parent: Mapped["DocumentNode | None"] = relationship("DocumentNode", remote_side=[id], back_populates="children")
    children: Mapped[list["DocumentNode"]] = relationship("DocumentNode", back_populates="parent", cascade="all, delete-orphan")
    references: Mapped[list["DocumentReference"]] = relationship("DocumentReference", back_populates="source_node", cascade="all, delete-orphan")
