"""Repositories for ingestion jobs and update events."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import JobStatus
from qanorm.models import IngestionJob, UpdateEvent


class IngestionJobRepository:
    """Data access helpers for ingestion jobs."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, job: IngestionJob) -> IngestionJob:
        """Add a job to the current session."""

        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: UUID) -> IngestionJob | None:
        """Load a job by id."""

        return self.session.get(IngestionJob, job_id)

    def claim_next_ready_job(self, now: datetime | None = None) -> IngestionJob | None:
        """Atomically claim the next pending scheduled job."""

        claimed_at = now or datetime.now(timezone.utc)
        stmt = (
            select(IngestionJob)
            .where(
                IngestionJob.status == JobStatus.PENDING,
                IngestionJob.scheduled_at <= claimed_at,
            )
            .order_by(IngestionJob.scheduled_at.asc(), IngestionJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = self.session.execute(stmt).scalar_one_or_none()
        if job is None:
            return None

        job.status = JobStatus.RUNNING
        job.started_at = claimed_at
        self.session.flush()
        return job

    def mark_completed(self, job: IngestionJob, finished_at: datetime | None = None) -> IngestionJob:
        """Mark a job as completed."""

        job.status = JobStatus.COMPLETED
        job.finished_at = finished_at or datetime.now(timezone.utc)
        self.session.flush()
        return job

    def mark_failed(
        self,
        job: IngestionJob,
        error_message: str,
        finished_at: datetime | None = None,
    ) -> IngestionJob:
        """Mark a job as failed and increment attempts."""

        job.status = JobStatus.FAILED
        job.last_error = error_message
        job.attempt_count += 1
        job.finished_at = finished_at or datetime.now(timezone.utc)
        self.session.flush()
        return job


class UpdateEventRepository:
    """Data access helpers for update events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: UpdateEvent) -> UpdateEvent:
        """Add an update event."""

        self.session.add(event)
        self.session.flush()
        return event

    def list_for_document(self, document_id: UUID) -> list[UpdateEvent]:
        """List update events for a document."""

        stmt = (
            select(UpdateEvent)
            .where(UpdateEvent.document_id == document_id)
            .order_by(UpdateEvent.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
