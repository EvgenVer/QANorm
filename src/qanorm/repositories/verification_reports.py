"""Repositories for answer verification reports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from qanorm.models import VerificationReport


class VerificationReportRepository:
    """Data access helpers for verification results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, report: VerificationReport) -> VerificationReport:
        """Insert a verification report and flush it."""

        self.session.add(report)
        self.session.flush()
        return report
