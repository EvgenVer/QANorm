"""Session ORM model for Stage 2 chat state."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import SessionChannel, SessionStatus


class QASession(Base):
    """Canonical user session for web and Telegram interactions."""

    __tablename__ = "qa_sessions"
    __table_args__ = (
        Index("ix_qa_sessions_channel_external_user_id", "channel", "external_user_id"),
        Index("ix_qa_sessions_channel_external_chat_id", "channel", "external_chat_id"),
        Index("ix_qa_sessions_status", "status"),
        Index("ix_qa_sessions_created_at", "created_at"),
        Index("ix_qa_sessions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[SessionChannel] = mapped_column(
        Enum(
            SessionChannel,
            name="session_channel_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(
            SessionStatus,
            name="session_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=SessionStatus.ACTIVE,
        server_default=SessionStatus.ACTIVE.value,
    )
    session_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["QAMessage"]] = relationship("QAMessage", back_populates="session", cascade="all, delete-orphan")
    queries: Mapped[list["QAQuery"]] = relationship("QAQuery", back_populates="session", cascade="all, delete-orphan")
    security_events: Mapped[list["SecurityEvent"]] = relationship(
        "SecurityEvent",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent",
        back_populates="session",
        cascade="all, delete-orphan",
    )
