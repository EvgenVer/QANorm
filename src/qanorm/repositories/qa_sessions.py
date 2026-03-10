"""Repositories for Stage 2 chat sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import SessionChannel, SessionStatus
from qanorm.models import QASession


class QASessionRepository:
    """Data access helpers for persisted chat sessions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, qa_session: QASession) -> QASession:
        """Insert a session row and flush generated fields."""

        self.session.add(qa_session)
        self.session.flush()
        return qa_session

    def get(self, session_id: UUID) -> QASession | None:
        """Load a session by the internal primary key."""

        return self.session.get(QASession, session_id)

    def get_by_channel_identifiers(
        self,
        channel: SessionChannel,
        *,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
    ) -> QASession | None:
        """Load a session by channel-scoped external identifiers."""

        if external_user_id is None and external_chat_id is None:
            raise ValueError("At least one external identifier must be provided.")

        stmt = select(QASession).where(QASession.channel == channel)
        if external_user_id is not None:
            stmt = stmt.where(QASession.external_user_id == external_user_id)
        if external_chat_id is not None:
            stmt = stmt.where(QASession.external_chat_id == external_chat_id)
        stmt = stmt.order_by(QASession.created_at.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_by_channel_identifiers(
        self,
        channel: SessionChannel,
        *,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
    ) -> list[QASession]:
        """Load all sessions bound to one channel-scoped external identity."""

        if external_user_id is None and external_chat_id is None:
            raise ValueError("At least one external identifier must be provided.")

        stmt = select(QASession).where(QASession.channel == channel)
        if external_user_id is not None:
            stmt = stmt.where(QASession.external_user_id == external_user_id)
        if external_chat_id is not None:
            stmt = stmt.where(QASession.external_chat_id == external_chat_id)
        stmt = stmt.order_by(QASession.created_at.desc())
        return list(self.session.execute(stmt).scalars().all())

    def delete(self, qa_session: QASession) -> None:
        """Delete one session root and rely on FK cascades for child rows."""

        self.session.delete(qa_session)
        self.session.flush()

    def update_session_state(
        self,
        qa_session: QASession,
        *,
        status: SessionStatus | None = None,
        session_summary: str | None = None,
        expires_at: datetime | None = None,
    ) -> QASession:
        """Update mutable session lifecycle fields."""

        if status is not None:
            qa_session.status = status
        if session_summary is not None:
            qa_session.session_summary = session_summary
        if expires_at is not None:
            qa_session.expires_at = expires_at
        self.session.flush()
        return qa_session
