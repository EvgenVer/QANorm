"""Repositories for security events."""

from __future__ import annotations

from sqlalchemy.orm import Session

from qanorm.models import SecurityEvent


class SecurityEventRepository:
    """Data access helpers for persisted security events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: SecurityEvent) -> SecurityEvent:
        """Insert a security event and flush it."""

        self.session.add(event)
        self.session.flush()
        return event
