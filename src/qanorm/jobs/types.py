"""Job type declarations."""

from __future__ import annotations

from qanorm.db.types import JobStatus, JobType


JOB_PAYLOAD_REQUIRED_FIELDS: dict[JobType, tuple[str, ...]] = {
    JobType.CRAWL_SEED: ("seed_url",),
    JobType.PARSE_LIST_PAGE: ("list_page_url",),
    JobType.PROCESS_DOCUMENT_CARD: ("card_url",),
    JobType.DOWNLOAD_ARTIFACTS: ("document_version_id",),
    JobType.EXTRACT_TEXT: ("document_version_id",),
    JobType.RUN_OCR: ("document_version_id",),
    JobType.NORMALIZE_DOCUMENT: ("document_version_id",),
    JobType.INDEX_DOCUMENT: ("document_version_id",),
    JobType.REFRESH_DOCUMENT: ("document_code",),
}

JOB_DEDUP_KEY_FIELDS: dict[JobType, tuple[str, ...]] = {
    JobType.CRAWL_SEED: ("seed_url",),
    JobType.PARSE_LIST_PAGE: ("list_page_url",),
    JobType.PROCESS_DOCUMENT_CARD: ("card_url",),
    JobType.DOWNLOAD_ARTIFACTS: ("document_version_id",),
    JobType.EXTRACT_TEXT: ("document_version_id",),
    JobType.RUN_OCR: ("document_version_id",),
    JobType.NORMALIZE_DOCUMENT: ("document_version_id",),
    JobType.INDEX_DOCUMENT: ("document_version_id",),
    JobType.REFRESH_DOCUMENT: ("document_code",),
}

RETRYABLE_JOB_STATUSES: tuple[JobStatus, ...] = (JobStatus.PENDING, JobStatus.RUNNING)
