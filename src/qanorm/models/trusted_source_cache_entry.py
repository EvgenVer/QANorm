"""Bounded shared cache entries for online trusted-source retrieval."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from qanorm.db.base import Base


class TrustedSourceCacheEntry(Base):
    """One cached trusted-source search/page/extraction payload with TTL metadata."""

    __tablename__ = "trusted_source_cache_entries"
    __table_args__ = (
        UniqueConstraint("cache_kind", "cache_key", name="uq_trusted_source_cache_entries_kind_key"),
        Index("ix_trusted_source_cache_entries_source_id", "source_id"),
        Index("ix_trusted_source_cache_entries_source_domain", "source_domain"),
        Index("ix_trusted_source_cache_entries_expires_at", "expires_at"),
        Index("ix_trusted_source_cache_entries_last_accessed_at", "last_accessed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cache_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
