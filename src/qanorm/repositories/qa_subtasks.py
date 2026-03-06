"""Repositories for decomposed query subtasks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import QASubtask


class QASubtaskRepository:
    """Data access helpers for stored subtask trees."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, subtask: QASubtask) -> QASubtask:
        """Insert a subtask row and flush it."""

        self.session.add(subtask)
        self.session.flush()
        return subtask

    def list_for_query(self, query_id: UUID) -> list[QASubtask]:
        """Return subtasks ordered for deterministic tree reconstruction."""

        stmt = (
            select(QASubtask)
            .where(QASubtask.query_id == query_id)
            .order_by(QASubtask.priority.asc(), QASubtask.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
