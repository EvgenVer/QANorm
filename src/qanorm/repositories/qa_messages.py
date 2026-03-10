"""Repositories for Stage 2 session messages."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import MessageRole
from qanorm.models import QAMessage


class QAMessageRepository:
    """Data access helpers for session message history."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, message: QAMessage) -> QAMessage:
        """Insert a message and flush its identifier."""

        self.session.add(message)
        self.session.flush()
        return message

    def save(self, message: QAMessage) -> QAMessage:
        """Persist updates to an existing session message."""

        self.session.add(message)
        self.session.flush()
        return message

    def list_for_session(self, session_id: UUID) -> list[QAMessage]:
        """Return session history in chronological order."""

        stmt = (
            select(QAMessage)
            .where(QAMessage.session_id == session_id)
            .order_by(QAMessage.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_assistant_for_session(self, session_id: UUID) -> QAMessage | None:
        """Return the latest persisted assistant message for one session."""

        stmt = (
            select(QAMessage)
            .where(QAMessage.session_id == session_id, QAMessage.role == MessageRole.ASSISTANT)
            .order_by(QAMessage.created_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()
