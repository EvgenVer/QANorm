"""Repositories for durable audit events."""

from __future__ import annotations

from sqlalchemy.orm import Session

from qanorm.models import AuditEvent


class AuditEventRepository:
    """Data access helpers for audit trail events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: AuditEvent) -> AuditEvent:
        """Insert an audit event and flush it."""

        self.session.add(event)
        self.session.flush()
        return event
