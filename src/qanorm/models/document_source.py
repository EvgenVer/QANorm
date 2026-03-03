"""Document source ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class DocumentSource(Base):
    """Source location metadata for a discovered document."""

    __tablename__ = "document_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    seed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    list_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_url: Mapped[str] = mapped_column(Text, nullable=False)
    html_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    print_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped["Document"] = relationship("Document", back_populates="sources")
    document_version: Mapped["DocumentVersion"] = relationship("DocumentVersion", back_populates="sources")
