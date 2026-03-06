"""Repositories for document freshness checks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import FreshnessCheck


class FreshnessCheckRepository:
    """Data access helpers for persisted freshness check results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, check: FreshnessCheck) -> FreshnessCheck:
        """Insert a freshness check row."""

        self.session.add(check)
        self.session.flush()
        return check

    def list_for_query(self, query_id: UUID) -> list[FreshnessCheck]:
        """Return freshness checks linked to one query."""

        stmt = (
            select(FreshnessCheck)
            .where(FreshnessCheck.query_id == query_id)
            .order_by(FreshnessCheck.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
