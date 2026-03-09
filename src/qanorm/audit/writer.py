"""Generic audit writer used across API, tools, agents, and integrations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.models import AuditEvent
from qanorm.repositories import AuditEventRepository


class AuditWriter:
    """Persist a normalized audit trail record with minimal boilerplate."""

    def __init__(self, session: Session, *, repository: AuditEventRepository | None = None) -> None:
        self.session = session
        self.repository = repository or AuditEventRepository(session)

    def write(
        self,
        *,
        event_type: str,
        actor_kind: str,
        payload_json: dict | None = None,
        session_id: UUID | None = None,
        query_id: UUID | None = None,
        subtask_id: UUID | None = None,
    ) -> AuditEvent:
        """Insert one audit event and flush it immediately."""

        return self.repository.add(
            AuditEvent(
                session_id=session_id,
                query_id=query_id,
                subtask_id=subtask_id,
                event_type=event_type,
                actor_kind=actor_kind,
                payload_json=payload_json,
            )
        )
