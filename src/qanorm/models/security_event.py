"""Security event ORM model for policy violations and warnings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import SecuritySeverity


class SecurityEvent(Base):
    """Security or policy event linked to a session or query."""

    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_query_id", "query_id"),
        Index("ix_security_events_session_id", "session_id"),
        Index("ix_security_events_severity", "severity"),
        Index("ix_security_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[SecuritySeverity] = mapped_column(
        Enum(
            SecuritySeverity,
            name="security_severity_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=SecuritySeverity.INFO,
        server_default=SecuritySeverity.INFO.value,
    )
    source_kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery | None"] = relationship("QAQuery", back_populates="security_events")
    session: Mapped["QASession | None"] = relationship("QASession", back_populates="security_events")
