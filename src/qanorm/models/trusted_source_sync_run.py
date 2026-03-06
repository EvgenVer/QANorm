"""Trusted source sync run ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import TrustedSourceSyncStatus


class TrustedSourceSyncRun(Base):
    """Single synchronization run over a trusted external source."""

    __tablename__ = "trusted_source_sync_runs"
    __table_args__ = (
        Index("ix_trusted_source_sync_runs_source_domain", "source_domain"),
        Index("ix_trusted_source_sync_runs_status", "status"),
        Index("ix_trusted_source_sync_runs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TrustedSourceSyncStatus] = mapped_column(
        Enum(
            TrustedSourceSyncStatus,
            name="trusted_source_sync_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=TrustedSourceSyncStatus.PENDING,
        server_default=TrustedSourceSyncStatus.PENDING.value,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    documents_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    documents_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    documents: Mapped[list["TrustedSourceDocument"]] = relationship(
        "TrustedSourceDocument",
        back_populates="last_sync_run",
    )
