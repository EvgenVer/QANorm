"""Repositories for normalized evidence blocks."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import QAEvidence


class QAEvidenceRepository:
    """Data access helpers for evidence linked to queries and subtasks."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_many(self, evidence_blocks: Iterable[QAEvidence]) -> list[QAEvidence]:
        """Insert multiple evidence rows in one flush."""

        items = list(evidence_blocks)
        self.session.add_all(items)
        self.session.flush()
        return items

    def list_for_query(
        self,
        query_id: UUID,
        *,
        subtask_id: UUID | None = None,
    ) -> list[QAEvidence]:
        """Return evidence for a query, optionally narrowed to one subtask."""

        stmt = select(QAEvidence).where(QAEvidence.query_id == query_id)
        if subtask_id is not None:
            stmt = stmt.where(QAEvidence.subtask_id == subtask_id)
        stmt = stmt.order_by(QAEvidence.created_at.asc())
        return list(self.session.execute(stmt).scalars().all())
