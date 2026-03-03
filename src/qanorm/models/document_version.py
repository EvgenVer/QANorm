"""Document version ORM model."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import ProcessingStatus, StatusNormalized


class DocumentVersion(Base):
    """Specific discovered version of a document."""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    edition_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_status_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_normalized: Mapped[StatusNormalized] = mapped_column(
        Enum(StatusNormalized, name="status_normalized_enum"),
        nullable=False,
        default=StatusNormalized.UNKNOWN,
    )
    text_actualized_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    description_actualized_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_outdated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status_enum"),
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    document: Mapped["Document"] = relationship("Document", back_populates="versions", foreign_keys=[document_id])
    sources: Mapped[list["DocumentSource"]] = relationship("DocumentSource", back_populates="document_version", cascade="all, delete-orphan")
    raw_artifacts: Mapped[list["RawArtifact"]] = relationship("RawArtifact", back_populates="document_version", cascade="all, delete-orphan")
    nodes: Mapped[list["DocumentNode"]] = relationship("DocumentNode", back_populates="document_version", cascade="all, delete-orphan")
    references: Mapped[list["DocumentReference"]] = relationship("DocumentReference", back_populates="document_version", cascade="all, delete-orphan")
