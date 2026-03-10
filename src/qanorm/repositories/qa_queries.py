"""Repositories for Stage 2 query runs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import QueryStatus
from qanorm.models import QAQuery


class QAQueryRepository:
    """Data access helpers for orchestration query rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, query: QAQuery) -> QAQuery:
        """Insert a query row and flush generated fields."""

        self.session.add(query)
        self.session.flush()
        return query

    def get(self, query_id: UUID) -> QAQuery | None:
        """Load a query by id."""

        return self.session.get(QAQuery, query_id)

    def update_state(
        self,
        query: QAQuery,
        *,
        status: QueryStatus,
        finished_at: datetime | None = None,
        intent: str | None = None,
        clarification_required: bool | None = None,
        document_hints: list[str] | None = None,
        locator_hints: list[str] | None = None,
        retrieval_mode: str | None = None,
        document_resolution: dict | None = None,
        requires_freshness_check: bool | None = None,
        used_open_web: bool | None = None,
        used_trusted_web: bool | None = None,
    ) -> QAQuery:
        """Update the current query status and source-usage flags."""

        query.status = status
        if finished_at is not None:
            query.finished_at = finished_at
        if intent is not None:
            query.intent = intent
        if clarification_required is not None:
            query.clarification_required = clarification_required
        if document_hints is not None:
            query.document_hints = document_hints
        if locator_hints is not None:
            query.locator_hints = locator_hints
        if retrieval_mode is not None:
            query.retrieval_mode = retrieval_mode
        if document_resolution is not None:
            query.document_resolution = document_resolution
        if requires_freshness_check is not None:
            query.requires_freshness_check = requires_freshness_check
        if used_open_web is not None:
            query.used_open_web = used_open_web
        if used_trusted_web is not None:
            query.used_trusted_web = used_trusted_web
        self.session.flush()
        return query
