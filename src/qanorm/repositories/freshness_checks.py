"""Repositories for document freshness checks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import FreshnessCheckStatus
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

    def get(self, check_id: UUID) -> FreshnessCheck | None:
        """Load one freshness check by id."""

        return self.session.get(FreshnessCheck, check_id)

    def list_for_query(self, query_id: UUID) -> list[FreshnessCheck]:
        """Return freshness checks linked to one query."""

        stmt = (
            select(FreshnessCheck)
            .where(FreshnessCheck.query_id == query_id)
            .order_by(FreshnessCheck.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_pending(self, *, limit: int = 100) -> list[FreshnessCheck]:
        """Return freshness checks that still need background processing."""

        stmt = (
            select(FreshnessCheck)
            .where(FreshnessCheck.check_status == FreshnessCheckStatus.PENDING)
            .order_by(FreshnessCheck.created_at.asc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())
