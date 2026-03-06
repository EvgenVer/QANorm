"""Session lifecycle services for Stage 2 chat flows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import SessionChannel, SessionStatus
from qanorm.models import QASession
from qanorm.repositories.qa_sessions import QASessionRepository
from qanorm.settings import QAFileConfig, get_qa_config


class SessionService:
    """Manage chat sessions, TTL, and retention cleanup."""

    def __init__(
        self,
        session: Session,
        *,
        qa_config: QAFileConfig | None = None,
        repository: QASessionRepository | None = None,
    ) -> None:
        self.session = session
        self.qa_config = qa_config or get_qa_config()
        self.repository = repository or QASessionRepository(session)

    def create_session(
        self,
        *,
        channel: SessionChannel,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
        now: datetime | None = None,
    ) -> QASession:
        """Create a new active session with a calculated expiration deadline."""

        session_created_at = now or datetime.now(timezone.utc)
        qa_session = QASession(
            id=uuid4(),
            channel=channel,
            external_user_id=external_user_id,
            external_chat_id=external_chat_id,
            status=SessionStatus.ACTIVE,
            expires_at=self._calculate_expiration(session_created_at),
        )
        return self.repository.add(qa_session)

    def resume_session(
        self,
        *,
        channel: SessionChannel,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
        now: datetime | None = None,
    ) -> QASession | None:
        """Load an existing session by external identifiers and extend its TTL."""

        qa_session = self.repository.get_by_channel_identifiers(
            channel,
            external_user_id=external_user_id,
            external_chat_id=external_chat_id,
        )
        if qa_session is None:
            return None

        session_now = now or datetime.now(timezone.utc)
        return self.repository.update_session_state(
            qa_session,
            status=SessionStatus.ACTIVE,
            expires_at=self._calculate_expiration(session_now),
        )

    def update_summary(self, session_id: UUID, summary: str) -> QASession | None:
        """Persist the compacted session summary."""

        qa_session = self.repository.get(session_id)
        if qa_session is None:
            return None
        return self.repository.update_session_state(qa_session, session_summary=summary)

    def cleanup_expired_sessions(self, now: datetime | None = None) -> int:
        """Delete expired sessions and rely on FK cascades for related query data."""

        retention_cutoff = now or datetime.now(timezone.utc)
        stmt = select(QASession).where(QASession.expires_at.is_not(None), QASession.expires_at <= retention_cutoff)
        expired_sessions = list(self.session.execute(stmt).scalars().all())
        for qa_session in expired_sessions:
            # Delete the session root so messages, queries, and all descendants cascade.
            self.session.delete(qa_session)
        if expired_sessions:
            self.session.flush()
        return len(expired_sessions)

    def _calculate_expiration(self, base_time: datetime) -> datetime:
        """Calculate the session expiration deadline from runtime config."""

        return base_time + timedelta(hours=self.qa_config.session.ttl_hours)
