from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from qanorm.db.types import JobStatus, JobType
from qanorm.jobs.scheduler import (
    JobPayloadValidationError,
    build_job_dedup_key,
    claim_next_ready_job,
    create_job,
    get_next_ready_job,
    mark_job_completed,
    mark_job_failed,
    mark_job_running,
    retry_job_after_temporary_error,
    validate_job_payload,
)
from qanorm.models import IngestionJob
from qanorm.repositories.jobs import IngestionJobRepository


def _mock_session() -> MagicMock:
    return MagicMock()


def test_validate_job_payload_rejects_missing_required_fields() -> None:
    with pytest.raises(JobPayloadValidationError):
        validate_job_payload(JobType.PROCESS_DOCUMENT_CARD, {"list_title": "Only title"})


def test_build_job_dedup_key_uses_configured_fields() -> None:
    dedup_key = build_job_dedup_key(
        JobType.PROCESS_DOCUMENT_CARD,
        {"card_url": "https://example.test/card/1", "list_title": "Ignored"},
    )

    assert dedup_key == "https://example.test/card/1"


def test_create_job_returns_existing_duplicate_when_found() -> None:
    session = _mock_session()
    existing_job = IngestionJob(job_type=JobType.PROCESS_DOCUMENT_CARD, payload={"dedup_key": "dup"})
    session.execute.return_value.scalar_one_or_none.return_value = existing_job
    repository = IngestionJobRepository(session)

    created = create_job(
        repository,
        job_type=JobType.PROCESS_DOCUMENT_CARD,
        payload={"card_url": "https://example.test/card/1"},
    )

    assert created is existing_job
    session.add.assert_not_called()


def test_create_job_persists_new_job_with_dedup_key() -> None:
    session = _mock_session()
    session.execute.return_value.scalar_one_or_none.return_value = None
    repository = IngestionJobRepository(session)
    scheduled_at = datetime.now(timezone.utc)

    created = create_job(
        repository,
        job_type=JobType.CRAWL_SEED,
        payload={"seed_url": "https://example.test/seed"},
        scheduled_at=scheduled_at,
        max_attempts=5,
    )

    assert created.job_type == JobType.CRAWL_SEED
    assert created.payload["dedup_key"] == "https://example.test/seed"
    assert created.scheduled_at == scheduled_at
    assert created.max_attempts == 5
    session.add.assert_called_once()
    session.flush.assert_called_once()


def test_repository_get_next_ready_job_uses_scalar_lookup() -> None:
    session = _mock_session()
    expected_job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    session.execute.return_value.scalar_one_or_none.return_value = expected_job
    repository = IngestionJobRepository(session)

    result = get_next_ready_job(repository, now=datetime.now(timezone.utc))

    assert result is expected_job
    session.execute.assert_called_once()


def test_claim_next_ready_job_marks_job_running() -> None:
    session = _mock_session()
    now = datetime.now(timezone.utc)
    ready_job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    ready_job.status = JobStatus.PENDING
    session.execute.return_value.scalar_one_or_none.return_value = ready_job
    repository = IngestionJobRepository(session)

    claimed = claim_next_ready_job(repository, now=now)

    assert claimed is ready_job
    assert ready_job.status == JobStatus.RUNNING
    assert ready_job.started_at == now
    session.flush.assert_called_once()


def test_mark_job_running_transitions_state() -> None:
    session = _mock_session()
    repository = IngestionJobRepository(session)
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    job.status = JobStatus.PENDING
    now = datetime.now(timezone.utc)

    result = mark_job_running(repository, job, started_at=now)

    assert result is job
    assert job.status == JobStatus.RUNNING
    assert job.started_at == now


def test_mark_job_completed_transitions_state() -> None:
    session = _mock_session()
    repository = IngestionJobRepository(session)
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    job.status = JobStatus.RUNNING
    job.last_error = "previous"
    finished_at = datetime.now(timezone.utc)

    result = mark_job_completed(repository, job, finished_at=finished_at)

    assert result is job
    assert job.status == JobStatus.COMPLETED
    assert job.last_error is None
    assert job.finished_at == finished_at


def test_mark_job_failed_records_attempts_and_error() -> None:
    session = _mock_session()
    repository = IngestionJobRepository(session)
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    job.status = JobStatus.RUNNING
    job.attempt_count = 0
    finished_at = datetime.now(timezone.utc)

    result = mark_job_failed(repository, job, "boom", finished_at=finished_at)

    assert result is job
    assert job.status == JobStatus.FAILED
    assert job.attempt_count == 1
    assert job.last_error == "boom"
    assert job.finished_at == finished_at


def test_retry_job_after_temporary_error_requeues_when_attempts_remain() -> None:
    session = _mock_session()
    repository = IngestionJobRepository(session)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    job.status = JobStatus.RUNNING
    job.attempt_count = 0
    job.max_attempts = 3

    result = retry_job_after_temporary_error(
        repository,
        job,
        "temporary failure",
        retry_delay_seconds=30,
        now=now,
    )

    assert result is job
    assert job.status == JobStatus.PENDING
    assert job.attempt_count == 1
    assert job.last_error == "temporary failure"
    assert job.scheduled_at == now + timedelta(seconds=30)
    assert job.started_at is None
    assert job.finished_at is None


def test_retry_job_after_temporary_error_marks_failed_when_attempts_exhausted() -> None:
    session = _mock_session()
    repository = IngestionJobRepository(session)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test"})
    job.status = JobStatus.RUNNING
    job.attempt_count = 2
    job.max_attempts = 3

    result = retry_job_after_temporary_error(
        repository,
        job,
        "temporary failure",
        retry_delay_seconds=30,
        now=now,
    )

    assert result is job
    assert job.status == JobStatus.FAILED
    assert job.attempt_count == 3
    assert job.last_error == "temporary failure"
    assert job.finished_at == now
