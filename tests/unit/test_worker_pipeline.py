from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from qanorm.db.types import JobStatus, JobType
from qanorm.indexing.indexer import ReindexResult
from qanorm.jobs.worker import ProcessedJobResult, dispatch_job, process_claimed_job, run_worker_loop
from qanorm.models import IngestionJob
from qanorm.services.document_pipeline import (
    DownloadArtifactsResult,
    ExtractedTextResult,
    OCRProcessingResult,
    StructureNormalizationPipelineResult,
    orchestrate_document_pipeline_step,
)
from qanorm.services.ingestion import process_crawl_seed_job, process_parse_list_page_job, queue_seed_crawl_jobs
from qanorm.services.refresh_service import request_document_refresh


def _mock_session() -> MagicMock:
    return MagicMock()


@pytest.mark.parametrize(
    ("job_type", "payload", "handler_name"),
    [
        (JobType.CRAWL_SEED, {"seed_url": "https://example.test/seed"}, "handle_crawl_seed_job"),
        (JobType.PARSE_LIST_PAGE, {"list_page_url": "https://example.test/list"}, "handle_parse_list_page_job"),
        (JobType.PROCESS_DOCUMENT_CARD, {"card_url": "https://example.test/card"}, "handle_process_document_card_job"),
        (JobType.DOWNLOAD_ARTIFACTS, {"document_version_id": str(uuid4())}, "handle_download_artifacts_job"),
        (JobType.EXTRACT_TEXT, {"document_version_id": str(uuid4())}, "handle_extract_text_job"),
        (JobType.RUN_OCR, {"document_version_id": str(uuid4())}, "handle_run_ocr_job"),
        (JobType.NORMALIZE_DOCUMENT, {"document_version_id": str(uuid4())}, "handle_normalize_document_job"),
        (JobType.INDEX_DOCUMENT, {"document_version_id": str(uuid4())}, "handle_index_document_job"),
        (JobType.REFRESH_DOCUMENT, {"document_code": "SP 1.0"}, "handle_refresh_document_job"),
    ],
)
def test_dispatch_job_routes_to_matching_handler(
    job_type: JobType,
    payload: dict[str, str],
    handler_name: str,
) -> None:
    session = _mock_session()
    job = IngestionJob(job_type=job_type, payload={**payload, "dedup_key": "dup"})

    with patch(f"qanorm.jobs.worker.{handler_name}", return_value={"status": "ok"}) as handler_mock:
        result = dispatch_job(session, job)

    assert result == {"status": "ok"}
    handler_mock.assert_called_once_with(session, payload)


@pytest.mark.parametrize(
    ("job_type", "patch_target", "payload", "expected_status"),
    [
        (
            JobType.DOWNLOAD_ARTIFACTS,
            "download_document_artifacts",
            {
                "document_version_id": str(uuid4()),
                "document_code": "SP 1.0",
                "card_url": "https://example.test/card",
            },
            "ok",
        ),
        (
            JobType.EXTRACT_TEXT,
            "extract_document_text",
            {"document_version_id": str(uuid4())},
            "ok",
        ),
        (
            JobType.RUN_OCR,
            "run_document_ocr",
            {"document_version_id": str(uuid4())},
            "ok",
        ),
        (
            JobType.NORMALIZE_DOCUMENT,
            "normalize_document_structure",
            {"document_version_id": str(uuid4())},
            "ok",
        ),
        (
            JobType.INDEX_DOCUMENT,
            "index_document_version",
            {"document_version_id": str(uuid4())},
            "ok",
        ),
    ],
)
def test_orchestrate_document_pipeline_step_dispatches_supported_jobs(
    job_type: JobType,
    patch_target: str,
    payload: dict[str, str],
    expected_status: str,
) -> None:
    session = _mock_session()

    if job_type is JobType.DOWNLOAD_ARTIFACTS:
        response = DownloadArtifactsResult(expected_status, [], 0, False, True, None)
    elif job_type is JobType.EXTRACT_TEXT:
        response = ExtractedTextResult(expected_status, "html", 128, False, 1, None)
    elif job_type is JobType.RUN_OCR:
        response = OCRProcessingResult(expected_status, 2, 256, 0.9, False, 1, None)
    elif job_type is JobType.NORMALIZE_DOCUMENT:
        response = StructureNormalizationPipelineResult(expected_status, "ocr_raw", 5, 1, "abc123", False, None)
    else:
        response = ReindexResult(expected_status, "single-document", "SP 1.0", str(uuid4()), 3, 0)

    with patch(f"qanorm.services.document_pipeline.{patch_target}", return_value=response) as handler_mock:
        result = orchestrate_document_pipeline_step(session, job_type=job_type, payload=payload)

    assert result["status"] == expected_status
    assert handler_mock.call_args.args[0] is session


def test_queue_seed_crawl_jobs_supports_explicit_empty_seed_list() -> None:
    session = _mock_session()

    with patch("qanorm.services.ingestion.create_job") as create_job_mock:
        result = queue_seed_crawl_jobs(session, seed_urls=[])

    assert result.status == "ok"
    assert result.seed_count == 0
    assert result.queued_job_count == 0
    assert result.queued_job_ids == []
    create_job_mock.assert_not_called()


def test_process_crawl_seed_job_discovers_pages_and_queues_parse_jobs() -> None:
    session = _mock_session()
    queued_jobs = [IngestionJob(job_type=JobType.PARSE_LIST_PAGE, payload={"list_page_url": "a"}) for _ in range(2)]

    with patch(
        "qanorm.services.ingestion.crawl_seed_first_page",
        return_value=SimpleNamespace(page_urls=["https://example.test/list/1", "https://example.test/list/2"]),
    ):
        with patch("qanorm.services.ingestion.create_job", side_effect=queued_jobs) as create_job_mock:
            result = process_crawl_seed_job(session, seed_url="https://example.test/seed")

    assert result.status == "ok"
    assert result.discovered_page_count == 2
    assert result.queued_parse_jobs == 2
    first_payload = create_job_mock.call_args_list[0].kwargs["payload"]
    assert first_payload["seed_url"] == "https://example.test/seed"
    assert first_payload["list_page_url"] == "https://example.test/list/1"


def test_process_parse_list_page_job_discovers_cards_and_queues_card_jobs() -> None:
    session = _mock_session()
    entries = [
        SimpleNamespace(card_url="https://example.test/card/1", status_raw="active"),
        SimpleNamespace(card_url="https://example.test/card/2", status_raw="active"),
    ]
    queued_jobs = [IngestionJob(job_type=JobType.PROCESS_DOCUMENT_CARD, payload={"card_url": "x"}) for _ in range(2)]

    with patch("qanorm.services.ingestion.fetch_html_document", return_value="<html></html>"):
        with patch("qanorm.services.ingestion.parse_list_page", return_value=entries):
            with patch("qanorm.services.ingestion.create_job", side_effect=queued_jobs) as create_job_mock:
                result = process_parse_list_page_job(
                    session,
                    list_page_url="https://example.test/list/1",
                    seed_url="https://example.test/seed",
                )

    assert result.status == "ok"
    assert result.discovered_entry_count == 2
    assert result.queued_card_jobs == 2
    first_payload = create_job_mock.call_args_list[0].kwargs["payload"]
    assert first_payload["list_status_raw"] == "active"
    assert first_payload["seed_url"] == "https://example.test/seed"


def test_request_document_refresh_queues_normalized_refresh_job() -> None:
    session = _mock_session()
    queued_job = IngestionJob(job_type=JobType.REFRESH_DOCUMENT, payload={"document_code": "SP 1.0"})

    @contextmanager
    def fake_scope():
        yield session

    with patch("qanorm.services.refresh_service.session_scope", side_effect=fake_scope):
        with patch("qanorm.services.refresh_service.create_job", return_value=queued_job) as create_job_mock:
            result = request_document_refresh(" sp 1.0 ")

    assert result["status"] == "queued"
    assert result["document_code"] == "SP 1.0"
    assert result["queued_job_id"] == str(queued_job.id)
    assert create_job_mock.call_args.kwargs["payload"] == {"document_code": "SP 1.0"}


def test_process_claimed_job_marks_completed_on_success() -> None:
    session = _mock_session()
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test/seed"})
    job.status = JobStatus.RUNNING

    with patch("qanorm.jobs.worker.dispatch_job", return_value={"status": "ok"}) as dispatch_mock:
        with patch("qanorm.jobs.worker.mark_job_completed") as completed_mock:
            with patch("qanorm.jobs.worker.mark_job_failed") as failed_mock:
                with patch("qanorm.jobs.worker.retry_job_after_temporary_error") as retry_mock:
                    result = process_claimed_job(session, job)

    assert result.status == "completed"
    assert result.action == "completed"
    assert result.result == {"status": "ok"}
    dispatch_mock.assert_called_once_with(session, job)
    completed_mock.assert_called_once()
    failed_mock.assert_not_called()
    retry_mock.assert_not_called()


def test_process_claimed_job_retries_only_for_temporary_errors() -> None:
    session = _mock_session()
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test/seed"})
    job.status = JobStatus.RUNNING

    with patch("qanorm.jobs.worker.dispatch_job", side_effect=httpx.ConnectError("timeout")):
        with patch("qanorm.jobs.worker.retry_job_after_temporary_error") as retry_mock:
            with patch("qanorm.jobs.worker.mark_job_failed") as failed_mock:
                with patch("qanorm.jobs.worker.mark_job_completed") as completed_mock:
                    result = process_claimed_job(session, job)

    assert result.status == "retry_scheduled"
    assert result.action == "retried"
    assert result.error == "timeout"
    retry_mock.assert_called_once()
    failed_mock.assert_not_called()
    completed_mock.assert_not_called()


def test_process_claimed_job_marks_failed_for_permanent_error() -> None:
    session = _mock_session()
    job = IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test/seed"})
    job.status = JobStatus.RUNNING

    with patch("qanorm.jobs.worker.dispatch_job", side_effect=ValueError("bad payload")):
        with patch("qanorm.jobs.worker.mark_job_failed") as failed_mock:
            with patch("qanorm.jobs.worker.retry_job_after_temporary_error") as retry_mock:
                with patch("qanorm.jobs.worker.mark_job_completed") as completed_mock:
                    result = process_claimed_job(session, job)

    assert result.status == "failed"
    assert result.action == "failed"
    assert result.error == "bad payload"
    failed_mock.assert_called_once()
    retry_mock.assert_not_called()
    completed_mock.assert_not_called()


def test_run_worker_loop_smoke_processes_test_queue() -> None:
    queued_jobs = [IngestionJob(job_type=JobType.CRAWL_SEED, payload={"seed_url": "https://example.test/seed"})]
    claimed_jobs = list(queued_jobs)
    session = object()

    class FakeRepository:
        def __init__(self, _session: object) -> None:
            self._session = _session

        def claim_next_ready_job(self) -> IngestionJob | None:
            if claimed_jobs:
                return claimed_jobs.pop(0)
            return None

    @contextmanager
    def fake_scope():
        yield session

    with patch("qanorm.jobs.worker.session_scope", side_effect=fake_scope):
        with patch("qanorm.jobs.worker.IngestionJobRepository", FakeRepository):
            with patch(
                "qanorm.jobs.worker.process_claimed_job",
                return_value=ProcessedJobResult(
                    job_id=str(queued_jobs[0].id),
                    job_type=queued_jobs[0].job_type.value,
                    status="completed",
                    action="completed",
                    result={"status": "ok"},
                ),
            ) as process_mock:
                result = run_worker_loop(max_jobs=2)

    assert result["status"] == "ok"
    assert result["processed_jobs"] == 1
    assert result["jobs"][0]["status"] == "completed"
    process_mock.assert_called_once_with(session, queued_jobs[0])
