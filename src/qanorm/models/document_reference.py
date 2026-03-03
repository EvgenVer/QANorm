"""Document reference ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class DocumentReference(Base):
    """Reference to another document extracted from document text."""

    __tablename__ = "document_references"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    source_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_nodes.id", ondelete="CASCADE"), nullable=False)
    reference_text: Mapped[str] = mapped_column(Text, nullable=False)
    referenced_code_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    matched_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document_version: Mapped["DocumentVersion"] = relationship("DocumentVersion", back_populates="references")
    source_node: Mapped["DocumentNode"] = relationship("DocumentNode", back_populates="references")
    matched_document: Mapped["Document | None"] = relationship("Document")
