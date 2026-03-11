"""Document alias ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class DocumentAlias(Base):
    """Alternative designation that may resolve to a canonical document."""

    __tablename__ = "document_aliases"
    __table_args__ = (
        UniqueConstraint("document_id", "alias_normalized", name="uq_document_aliases_document_id_alias_normalized"),
        Index("ix_document_aliases_document_id", "document_id"),
        Index("ix_document_aliases_alias_normalized", "alias_normalized"),
        Index("ix_document_aliases_alias_type", "alias_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    alias_raw: Mapped[str] = mapped_column(Text, nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped["Document"] = relationship("Document", back_populates="aliases")
