"""Background worker implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.exc import DBAPIError, OperationalError

from qanorm.db.session import session_scope
from qanorm.db.types import JobType
from qanorm.jobs.scheduler import mark_job_completed, mark_job_failed, retry_job_after_temporary_error
from qanorm.logging import get_worker_logger
from qanorm.models import IngestionJob
from qanorm.repositories import IngestionJobRepository
from qanorm.services.document_pipeline import orchestrate_document_pipeline_step
from qanorm.services.ingestion import process_crawl_seed_job, process_parse_list_page_job
from qanorm.services.refresh_service import process_refresh_document_job


logger = get_worker_logger()


class TemporaryJobError(RuntimeError):
    """A retryable processing failure."""


@dataclass(slots=True)
class ProcessedJobResult:
    """Summary of one processed job."""

    job_id: str
    job_type: str
    status: str
    action: str
    result: dict[str, Any] | None = None
    error: str | None = None


def dispatch_job(session: Any, job: IngestionJob) -> dict[str, Any]:
    """Dispatch one claimed job to the matching handler."""

    payload = dict(job.payload)
    payload.pop("dedup_key", None)

    if job.job_type is JobType.CRAWL_SEED:
        return handle_crawl_seed_job(session, payload)
    if job.job_type is JobType.PARSE_LIST_PAGE:
        return handle_parse_list_page_job(session, payload)
    if job.job_type is JobType.PROCESS_DOCUMENT_CARD:
        return handle_process_document_card_job(session, payload)
    if job.job_type is JobType.DOWNLOAD_ARTIFACTS:
        return handle_download_artifacts_job(session, payload)
    if job.job_type is JobType.EXTRACT_TEXT:
        return handle_extract_text_job(session, payload)
    if job.job_type is JobType.RUN_OCR:
        return handle_run_ocr_job(session, payload)
    if job.job_type is JobType.NORMALIZE_DOCUMENT:
        return handle_normalize_document_job(session, payload)
    if job.job_type is JobType.INDEX_DOCUMENT:
        return handle_index_document_job(session, payload)
    if job.job_type is JobType.REFRESH_DOCUMENT:
        return handle_refresh_document_job(session, payload)

    raise ValueError(f"Unsupported job type: {job.job_type.value}")


def handle_crawl_seed_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``crawl_seed`` job."""

    return _to_dict(process_crawl_seed_job(session, seed_url=payload["seed_url"]))


def handle_parse_list_page_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``parse_list_page`` job."""

    return _to_dict(
        process_parse_list_page_job(
            session,
            list_page_url=payload["list_page_url"],
            seed_url=payload.get("seed_url"),
        )
    )


def handle_process_document_card_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``process_document_card`` job."""

    from qanorm.services.document_pipeline import process_document_card

    return _to_dict(
        process_document_card(
            session,
            card_url=payload["card_url"],
            list_status_raw=payload.get("list_status_raw"),
            list_page_url=payload.get("list_page_url") or payload.get("source_list_url"),
            seed_url=payload.get("seed_url"),
        )
    )


def handle_download_artifacts_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``download_artifacts`` job."""

    return orchestrate_document_pipeline_step(session, job_type=JobType.DOWNLOAD_ARTIFACTS, payload=payload)


def handle_extract_text_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle an ``extract_text`` job."""

    return orchestrate_document_pipeline_step(session, job_type=JobType.EXTRACT_TEXT, payload=payload)


def handle_run_ocr_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``run_ocr`` job."""

    return orchestrate_document_pipeline_step(session, job_type=JobType.RUN_OCR, payload=payload)


def handle_normalize_document_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``normalize_document`` job."""

    return orchestrate_document_pipeline_step(session, job_type=JobType.NORMALIZE_DOCUMENT, payload=payload)


def handle_index_document_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle an ``index_document`` job."""

    return orchestrate_document_pipeline_step(session, job_type=JobType.INDEX_DOCUMENT, payload=payload)


def handle_refresh_document_job(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``refresh_document`` job."""

    return _to_dict(process_refresh_document_job(payload["document_code"], session=session))


def process_claimed_job(session: Any, job: IngestionJob) -> ProcessedJobResult:
    """Process one claimed job and update its queue state."""

    repository = IngestionJobRepository(session)
    logger.info("Processing job %s (%s)", job.id, job.job_type.value)

    try:
        result = dispatch_job(session, job)
    except Exception as exc:
        error_message = str(exc)
        session.rollback()
        if _is_temporary_error(exc):
            retry_job_after_temporary_error(repository, job, error_message)
            logger.warning("Retrying job %s (%s): %s", job.id, job.job_type.value, error_message)
            return ProcessedJobResult(
                job_id=str(job.id),
                job_type=job.job_type.value,
                status="retry_scheduled",
                action="retried",
                error=error_message,
            )

        mark_job_failed(repository, job, error_message)
        logger.exception("Marking job %s (%s) as failed", job.id, job.job_type.value)
        return ProcessedJobResult(
            job_id=str(job.id),
            job_type=job.job_type.value,
            status="failed",
            action="failed",
            error=error_message,
        )

    mark_job_completed(repository, job)
    logger.info("Completed job %s (%s)", job.id, job.job_type.value)
    return ProcessedJobResult(
        job_id=str(job.id),
        job_type=job.job_type.value,
        status="completed",
        action="completed",
        result=result,
    )


def run_worker_loop(*, max_jobs: int = 1) -> dict[str, Any]:
    """Claim and process up to ``max_jobs`` pending jobs."""

    processed: list[dict[str, Any]] = []
    for _ in range(max_jobs):
        with session_scope() as session:
            repository = IngestionJobRepository(session)
            job = repository.claim_next_ready_job()
            if job is None:
                break
            result = process_claimed_job(session, job)
            processed.append(_to_dict(result))

    return {
        "status": "ok",
        "processed_jobs": len(processed),
        "jobs": processed,
    }


def _is_temporary_error(exc: Exception) -> bool:
    if isinstance(exc, TemporaryJobError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    if isinstance(exc, OperationalError):
        message = str(exc).lower()
        return "deadlock detected" in message or "could not serialize access" in message
    if isinstance(exc, DBAPIError):
        return bool(exc.connection_invalidated)
    return isinstance(exc, (httpx.RequestError, TimeoutError, ConnectionError, OSError))


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return {key: getattr(value, key) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"Unsupported worker result type: {type(value)!r}")
