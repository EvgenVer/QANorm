"""Document ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import StatusNormalized


class Document(Base):
    """Canonical document entity."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    normalized_code: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_code: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_normalized: Mapped[StatusNormalized] = mapped_column(
        Enum(
            StatusNormalized,
            name="status_normalized_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=StatusNormalized.UNKNOWN,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True, name="fk_documents_current_version_id_document_versions"),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    versions: Mapped[list["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        foreign_keys="DocumentVersion.document_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped["DocumentVersion | None"] = relationship(
        "DocumentVersion",
        foreign_keys=[current_version_id],
        post_update=True,
    )
    sources: Mapped[list["DocumentSource"]] = relationship(
        "DocumentSource",
        back_populates="document",
        cascade="all, delete-orphan",
    )
    update_events: Mapped[list["UpdateEvent"]] = relationship(
        "UpdateEvent",
        back_populates="document",
        cascade="all, delete-orphan",
    )
