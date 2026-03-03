"""Update event ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base


class UpdateEvent(Base):
    """Document version update event."""

    __tablename__ = "update_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    old_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True)
    new_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True)
    update_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped["Document"] = relationship("Document", back_populates="update_events")
    old_version: Mapped["DocumentVersion | None"] = relationship("DocumentVersion", foreign_keys=[old_version_id])
    new_version: Mapped["DocumentVersion | None"] = relationship("DocumentVersion", foreign_keys=[new_version_id])
