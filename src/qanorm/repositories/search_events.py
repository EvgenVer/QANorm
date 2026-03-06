"""Repositories for trusted and open web search events."""

from __future__ import annotations

from sqlalchemy.orm import Session

from qanorm.models import SearchEvent


class SearchEventRepository:
    """Data access helpers for audited search provider calls."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: SearchEvent) -> SearchEvent:
        """Insert a search event and flush it."""

        self.session.add(event)
        self.session.flush()
        return event
