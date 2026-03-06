"""Repositories for audited tool invocation rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from qanorm.models import ToolInvocation


class ToolInvocationRepository:
    """Data access helpers for tool invocation audit records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, invocation: ToolInvocation) -> ToolInvocation:
        """Insert a tool invocation and flush it."""

        self.session.add(invocation)
        self.session.flush()
        return invocation
