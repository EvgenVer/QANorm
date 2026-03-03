"""Job scheduling helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from qanorm.db.types import JobType
from qanorm.models import IngestionJob
from qanorm.repositories.jobs import IngestionJobRepository
from qanorm.jobs.types import JOB_DEDUP_KEY_FIELDS, JOB_PAYLOAD_REQUIRED_FIELDS


class JobPayloadValidationError(ValueError):
    """Raised when a job payload is missing required fields."""


def validate_job_payload(job_type: JobType | str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a job payload."""

    normalized_job_type = JobType(job_type)
    required_fields = JOB_PAYLOAD_REQUIRED_FIELDS[normalized_job_type]
    missing_fields = [field for field in required_fields if field not in payload or payload[field] in (None, "")]
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise JobPayloadValidationError(
            f"Payload for '{normalized_job_type.value}' is missing required fields: {missing}"
        )
    return dict(payload)


def build_job_dedup_key(job_type: JobType | str, payload: dict[str, Any]) -> str:
    """Build a deterministic deduplication key for a job payload."""

    normalized_job_type = JobType(job_type)
    validated_payload = validate_job_payload(normalized_job_type, payload)
    fields = JOB_DEDUP_KEY_FIELDS[normalized_job_type]
    return "|".join(str(validated_payload[field]).strip() for field in fields)


def create_job(
    repository: IngestionJobRepository,
    *,
    job_type: JobType | str,
    payload: dict[str, Any],
    scheduled_at: datetime | None = None,
    max_attempts: int = 3,
) -> IngestionJob:
    """Create and persist a new queued job unless an active duplicate already exists."""

    normalized_job_type = JobType(job_type)
    validated_payload = validate_job_payload(normalized_job_type, payload)
    dedup_key = build_job_dedup_key(normalized_job_type, validated_payload)

    existing_job = repository.get_duplicate_pending_or_running(normalized_job_type, dedup_key)
    if existing_job is not None:
        return existing_job

    job_payload = {**validated_payload, "dedup_key": dedup_key}
    job = IngestionJob(
        job_type=normalized_job_type,
        payload=job_payload,
        max_attempts=max_attempts,
    )
    if scheduled_at is not None:
        job.scheduled_at = scheduled_at
    return repository.add(job)


def get_next_ready_job(
    repository: IngestionJobRepository,
    *,
    now: datetime | None = None,
) -> IngestionJob | None:
    """Load the next ready job without changing its state."""

    return repository.get_next_ready_job(now=now)


def claim_next_ready_job(
    repository: IngestionJobRepository,
    *,
    now: datetime | None = None,
) -> IngestionJob | None:
    """Atomically claim the next ready job for a worker."""

    return repository.claim_next_ready_job(now=now)


def mark_job_running(
    repository: IngestionJobRepository,
    job: IngestionJob,
    *,
    started_at: datetime | None = None,
) -> IngestionJob:
    """Move a job into the running state."""

    return repository.mark_running(job, started_at=started_at)


def mark_job_completed(
    repository: IngestionJobRepository,
    job: IngestionJob,
    *,
    finished_at: datetime | None = None,
) -> IngestionJob:
    """Move a job into the completed state."""

    return repository.mark_completed(job, finished_at=finished_at)


def mark_job_failed(
    repository: IngestionJobRepository,
    job: IngestionJob,
    error_message: str,
    *,
    finished_at: datetime | None = None,
) -> IngestionJob:
    """Move a job into the failed state."""

    return repository.mark_failed(job, error_message, finished_at=finished_at)


def retry_job_after_temporary_error(
    repository: IngestionJobRepository,
    job: IngestionJob,
    error_message: str,
    *,
    retry_delay_seconds: int = 60,
    now: datetime | None = None,
) -> IngestionJob:
    """Requeue a job after a temporary error when retries remain."""

    return repository.retry_after_temporary_error(
        job,
        error_message,
        retry_delay_seconds=retry_delay_seconds,
        now=now,
    )
