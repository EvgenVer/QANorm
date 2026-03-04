"""Document refresh service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from qanorm.db.session import session_scope
from qanorm.db.types import JobType
from qanorm.jobs.scheduler import create_job
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import IngestionJobRepository


@dataclass(slots=True)
class RefreshRequestResult:
    """Summary of a queued refresh request."""

    status: str
    document_code: str
    queued_job_id: str


@dataclass(slots=True)
class RefreshJobResult:
    """Summary of a processed ``refresh_document`` job."""

    status: str
    document_code: str
    message: str


def request_document_refresh(document_code: str) -> dict[str, Any]:
    """Queue a document refresh request."""

    normalized_code = normalize_document_code(document_code)
    with session_scope() as session:
        repository = IngestionJobRepository(session)
        job = create_job(
            repository,
            job_type=JobType.REFRESH_DOCUMENT,
            payload={"document_code": normalized_code},
        )
        result = RefreshRequestResult(
            status="queued",
            document_code=normalized_code,
            queued_job_id=str(job.id),
        )
    return asdict(result)


def process_refresh_document_job(document_code: str) -> RefreshJobResult:
    """Process a refresh job placeholder until block U expands the logic."""

    normalized_code = normalize_document_code(document_code)
    return RefreshJobResult(
        status="ok",
        document_code=normalized_code,
        message="Refresh document handler executed. Detailed refresh logic will be expanded in block U.",
    )
