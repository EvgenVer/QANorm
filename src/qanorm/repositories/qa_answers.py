"""Repositories for persisted final answers."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import QAAnswer


class QAAnswerRepository:
    """Data access helpers for final answer persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, answer: QAAnswer) -> QAAnswer:
        """Insert or update the answer row for a query."""

        self.session.add(answer)
        self.session.flush()
        return answer

    def get_by_query(self, query_id: UUID) -> QAAnswer | None:
        """Load the final answer for a query."""

        stmt = select(QAAnswer).where(QAAnswer.query_id == query_id).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()
