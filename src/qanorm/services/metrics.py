"""Quality metrics and readiness reporting for Stage 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from qanorm.db.session import session_scope
from qanorm.db.types import ArtifactType, JobStatus, JobType, ProcessingStatus, StatusNormalized
from qanorm.repositories import (
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    RawArtifactRepository,
    UpdateEventRepository,
)
from qanorm.settings import get_settings


MVP_TARGETS = {
    "list_pages_success_rate_min": 0.95,
    "active_documents_reaching_card_rate_min": 0.95,
    "active_documents_with_raw_rate_min": 0.90,
    "active_documents_with_text_rate_min": 0.85,
    "active_documents_structured_rate_min": 0.80,
    "active_index_correct_status_rate_min": 1.0,
    "inactive_documents_in_active_index_max": 0,
}

SUCCESSFUL_UPDATE_STATUSES = {
    "refresh_completed",
    "skipped_duplicate",
    "skipped_inactive_source",
    "skipped_unknown_source",
}
FAILED_UPDATE_STATUSES = {"refresh_failed"}
DETECTED_UPDATE_STATUSES = SUCCESSFUL_UPDATE_STATUSES | FAILED_UPDATE_STATUSES


@dataclass(slots=True)
class IngestionMetrics:
    status: str
    list_pages_processed_successfully: int
    list_pages_total: int
    list_pages_success_rate: float
    document_cards_discovered: int
    documents_total: int
    documents_active: int
    documents_inactive: int
    documents_unknown: int
    documents_with_raw_artifacts: int
    documents_with_extracted_text: int
    documents_with_ocr: int
    documents_with_low_confidence_parse: int
    documents_structured: int
    documents_indexed: int
    updates_detected: int
    updates_successful: int
    updates_failed: int
    active_documents_reaching_card_rate: float
    active_documents_with_raw_rate: float
    active_documents_with_text_rate: float
    active_documents_structured_rate: float
    active_index_documents_total: int
    active_index_documents_with_correct_status: int
    active_index_documents_with_correct_status_rate: float
    inactive_documents_in_active_index: int
    active_documents_without_active_version: int
    active_versions_without_text_source: int


def get_ingestion_metrics() -> dict[str, Any]:
    """Collect ingestion metrics in a managed session."""

    with session_scope() as session:
        metrics = collect_ingestion_metrics(session)
    return asdict(metrics)


def get_ingestion_target_comparison() -> dict[str, Any]:
    """Compare the current metrics against Stage 1 MVP targets."""

    with session_scope() as session:
        metrics = collect_ingestion_metrics(session)
    return compare_metrics_to_mvp_targets(metrics)


def get_stage1_readiness_checklist() -> dict[str, Any]:
    """Return the final Stage 1 readiness checklist."""

    with session_scope() as session:
        metrics = collect_ingestion_metrics(session)
    return build_stage1_readiness_checklist(metrics)


def get_ingestion_test_run_report() -> dict[str, Any]:
    """Build the test ingestion report in a managed session."""

    with session_scope() as session:
        metrics = collect_ingestion_metrics(session)
    return build_ingestion_test_run_report(metrics)


def collect_ingestion_metrics(session: Session) -> IngestionMetrics:
    """Aggregate the current ingestion metrics from persisted entities."""

    settings = get_settings()
    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    source_repository = DocumentSourceRepository(session)
    artifact_repository = RawArtifactRepository(session)
    job_repository = IngestionJobRepository(session)
    event_repository = UpdateEventRepository(session)

    documents = document_repository.list_all()
    jobs = job_repository.list_all()
    events = event_repository.list_all()
    low_confidence_threshold = settings.app.ocr_low_confidence_threshold

    document_rows: list[dict[str, Any]] = []
    for document in documents:
        versions = version_repository.list_for_document(document.id)
        active_version = _resolve_active_version(document, versions)
        active_sources = source_repository.list_for_document_version(active_version.id) if active_version is not None else []
        active_artifacts = (
            artifact_repository.list_for_document_version(active_version.id) if active_version is not None else []
        )
        document_rows.append(
            {
                "document": document,
                "versions": versions,
                "active_version": active_version,
                "active_sources": active_sources,
                "active_artifacts": active_artifacts,
            }
        )

    list_page_jobs = [job for job in jobs if job.job_type is JobType.PARSE_LIST_PAGE]
    card_jobs = [job for job in jobs if job.job_type is JobType.PROCESS_DOCUMENT_CARD]

    unique_list_pages = _distinct_payload_values(list_page_jobs, "list_page_url")
    successful_list_pages = {
        job.payload.get("list_page_url")
        for job in list_page_jobs
        if job.status is JobStatus.COMPLETED and job.payload.get("list_page_url")
    }
    unique_card_urls = _distinct_payload_values(card_jobs, "card_url")

    documents_active_rows = [
        row for row in document_rows if row["document"].status_normalized is StatusNormalized.ACTIVE
    ]
    documents_active = len(documents_active_rows)
    documents_inactive = sum(
        1 for row in document_rows if row["document"].status_normalized is StatusNormalized.INACTIVE
    )
    documents_unknown = sum(
        1 for row in document_rows if row["document"].status_normalized is StatusNormalized.UNKNOWN
    )

    documents_with_raw_artifacts = sum(1 for row in document_rows if _has_raw_artifacts(row["active_artifacts"]))
    documents_with_extracted_text = sum(
        1 for row in document_rows if _has_extracted_text(row["active_version"], row["active_artifacts"])
    )
    documents_with_ocr = sum(
        1
        for row in document_rows
        if row["active_version"] is not None and bool(row["active_version"].has_ocr)
    )
    documents_with_low_confidence_parse = sum(
        1
        for row in document_rows
        if row["active_version"] is not None
        and row["active_version"].parse_confidence is not None
        and row["active_version"].parse_confidence < low_confidence_threshold
    )
    documents_structured = sum(
        1
        for row in document_rows
        if row["active_version"] is not None
        and row["active_version"].processing_status in (ProcessingStatus.NORMALIZED, ProcessingStatus.INDEXED)
    )
    documents_indexed = sum(
        1
        for row in document_rows
        if row["active_version"] is not None and row["active_version"].processing_status is ProcessingStatus.INDEXED
    )

    active_documents_with_card = sum(1 for row in documents_active_rows if row["active_sources"])
    active_documents_with_raw = sum(
        1 for row in documents_active_rows if _has_raw_artifacts(row["active_artifacts"])
    )
    active_documents_with_text = sum(
        1
        for row in documents_active_rows
        if _has_extracted_text(row["active_version"], row["active_artifacts"])
    )
    active_documents_structured = sum(
        1
        for row in documents_active_rows
        if row["active_version"] is not None
        and row["active_version"].processing_status in (ProcessingStatus.NORMALIZED, ProcessingStatus.INDEXED)
    )

    indexed_rows = [
        row
        for row in document_rows
        if row["active_version"] is not None and row["active_version"].processing_status is ProcessingStatus.INDEXED
    ]
    active_index_documents_with_correct_status = sum(
        1 for row in indexed_rows if row["document"].status_normalized is StatusNormalized.ACTIVE
    )
    inactive_documents_in_active_index = sum(
        1 for row in indexed_rows if row["document"].status_normalized is StatusNormalized.INACTIVE
    )

    active_documents_without_active_version = sum(
        1
        for row in documents_active_rows
        if row["active_version"] is None or not bool(row["active_version"].is_active)
    )
    active_versions_without_text_source = sum(
        1
        for row in documents_active_rows
        if row["active_version"] is not None
        and not _has_extracted_text(row["active_version"], row["active_artifacts"])
    )

    updates_detected = sum(1 for event in events if event.status in DETECTED_UPDATE_STATUSES)
    updates_successful = sum(1 for event in events if event.status in SUCCESSFUL_UPDATE_STATUSES)
    updates_failed = sum(1 for event in events if event.status in FAILED_UPDATE_STATUSES)

    return IngestionMetrics(
        status="ok",
        list_pages_processed_successfully=len(successful_list_pages),
        list_pages_total=len(unique_list_pages),
        list_pages_success_rate=_safe_rate(len(successful_list_pages), len(unique_list_pages)),
        document_cards_discovered=len(unique_card_urls),
        documents_total=len(document_rows),
        documents_active=documents_active,
        documents_inactive=documents_inactive,
        documents_unknown=documents_unknown,
        documents_with_raw_artifacts=documents_with_raw_artifacts,
        documents_with_extracted_text=documents_with_extracted_text,
        documents_with_ocr=documents_with_ocr,
        documents_with_low_confidence_parse=documents_with_low_confidence_parse,
        documents_structured=documents_structured,
        documents_indexed=documents_indexed,
        updates_detected=updates_detected,
        updates_successful=updates_successful,
        updates_failed=updates_failed,
        active_documents_reaching_card_rate=_safe_rate(active_documents_with_card, documents_active),
        active_documents_with_raw_rate=_safe_rate(active_documents_with_raw, documents_active),
        active_documents_with_text_rate=_safe_rate(active_documents_with_text, documents_active),
        active_documents_structured_rate=_safe_rate(active_documents_structured, documents_active),
        active_index_documents_total=len(indexed_rows),
        active_index_documents_with_correct_status=active_index_documents_with_correct_status,
        active_index_documents_with_correct_status_rate=_safe_rate(
            active_index_documents_with_correct_status,
            len(indexed_rows),
        ),
        inactive_documents_in_active_index=inactive_documents_in_active_index,
        active_documents_without_active_version=active_documents_without_active_version,
        active_versions_without_text_source=active_versions_without_text_source,
    )


def compare_metrics_to_mvp_targets(metrics: IngestionMetrics) -> dict[str, Any]:
    """Compare current metrics to MVP baseline targets from Plan.md."""

    checks = {
        "list_pages_success_rate": {
            "actual": metrics.list_pages_success_rate,
            "target_min": MVP_TARGETS["list_pages_success_rate_min"],
            "passed": metrics.list_pages_success_rate >= MVP_TARGETS["list_pages_success_rate_min"],
        },
        "active_documents_reaching_card_rate": {
            "actual": metrics.active_documents_reaching_card_rate,
            "target_min": MVP_TARGETS["active_documents_reaching_card_rate_min"],
            "passed": metrics.active_documents_reaching_card_rate
            >= MVP_TARGETS["active_documents_reaching_card_rate_min"],
        },
        "active_documents_with_raw_rate": {
            "actual": metrics.active_documents_with_raw_rate,
            "target_min": MVP_TARGETS["active_documents_with_raw_rate_min"],
            "passed": metrics.active_documents_with_raw_rate >= MVP_TARGETS["active_documents_with_raw_rate_min"],
        },
        "active_documents_with_text_rate": {
            "actual": metrics.active_documents_with_text_rate,
            "target_min": MVP_TARGETS["active_documents_with_text_rate_min"],
            "passed": metrics.active_documents_with_text_rate >= MVP_TARGETS["active_documents_with_text_rate_min"],
        },
        "active_documents_structured_rate": {
            "actual": metrics.active_documents_structured_rate,
            "target_min": MVP_TARGETS["active_documents_structured_rate_min"],
            "passed": metrics.active_documents_structured_rate
            >= MVP_TARGETS["active_documents_structured_rate_min"],
        },
        "active_index_documents_with_correct_status_rate": {
            "actual": metrics.active_index_documents_with_correct_status_rate,
            "target_min": MVP_TARGETS["active_index_correct_status_rate_min"],
            "passed": metrics.active_index_documents_with_correct_status_rate
            >= MVP_TARGETS["active_index_correct_status_rate_min"],
        },
        "inactive_documents_in_active_index": {
            "actual": metrics.inactive_documents_in_active_index,
            "target_max": MVP_TARGETS["inactive_documents_in_active_index_max"],
            "passed": metrics.inactive_documents_in_active_index
            <= MVP_TARGETS["inactive_documents_in_active_index_max"],
        },
    }
    return {
        "status": "ok",
        "passed": all(item["passed"] for item in checks.values()),
        "checks": checks,
    }


def build_stage1_readiness_checklist(metrics: IngestionMetrics) -> dict[str, Any]:
    """Build the final Stage 1 readiness checklist."""

    target_comparison = compare_metrics_to_mvp_targets(metrics)
    checks = {
        "report_generated": {
            "passed": True,
            "details": "Metrics snapshot can be produced on demand.",
        },
        "metrics_target_comparison_passed": {
            "passed": target_comparison["passed"],
            "details": "Current snapshot satisfies the numeric MVP targets from Plan.md.",
        },
        "limited_sample_run_passed": {
            "passed": (
                metrics.list_pages_total > 0
                and metrics.documents_total > 0
                and metrics.documents_indexed > 0
                and metrics.documents_with_extracted_text > 0
            ),
            "details": "At least one limited-sample ingestion path reached indexing with extracted text.",
        },
        "inactive_documents_excluded_from_active_index": {
            "passed": metrics.inactive_documents_in_active_index == 0,
            "details": {
                "inactive_documents_in_active_index": metrics.inactive_documents_in_active_index,
            },
        },
        "active_documents_have_active_version": {
            "passed": metrics.active_documents_without_active_version == 0,
            "details": {
                "active_documents_without_active_version": metrics.active_documents_without_active_version,
            },
        },
        "active_versions_have_text_or_ocr": {
            "passed": metrics.active_versions_without_text_source == 0,
            "details": {
                "active_versions_without_text_source": metrics.active_versions_without_text_source,
            },
        },
    }
    return {
        "status": "ok",
        "ready": all(item["passed"] for item in checks.values()),
        "checks": checks,
    }


def build_ingestion_test_run_report(metrics: IngestionMetrics) -> dict[str, Any]:
    """Build a consolidated report for a test ingestion run."""

    target_comparison = compare_metrics_to_mvp_targets(metrics)
    readiness = build_stage1_readiness_checklist(metrics)
    return {
        "status": "ok",
        "metrics": asdict(metrics),
        "target_comparison": target_comparison,
        "readiness_checklist": readiness,
        "summary": {
            "documents_observed": metrics.documents_total,
            "active_documents": metrics.documents_active,
            "updates_detected": metrics.updates_detected,
            "updates_successful": metrics.updates_successful,
            "updates_failed": metrics.updates_failed,
            "low_confidence_documents": metrics.documents_with_low_confidence_parse,
            "ready_for_stage_1_exit": readiness["ready"],
        },
    }


def _resolve_active_version(document, versions):
    if document.current_version_id is not None:
        current = next((item for item in versions if item.id == document.current_version_id), None)
        if current is not None:
            return current
    active = [item for item in versions if item.is_active]
    return active[-1] if active else None


def _distinct_payload_values(jobs, payload_key: str) -> set[str]:
    return {job.payload.get(payload_key) for job in jobs if job.payload.get(payload_key)}


def _has_raw_artifacts(artifacts) -> bool:
    return any(
        artifact.artifact_type in (ArtifactType.HTML_RAW, ArtifactType.PDF_RAW, ArtifactType.PAGE_IMAGE)
        for artifact in artifacts
    )


def _has_extracted_text(version, artifacts) -> bool:
    if version is None:
        return False
    if version.has_ocr:
        return True
    if version.processing_status in (ProcessingStatus.EXTRACTED, ProcessingStatus.NORMALIZED, ProcessingStatus.INDEXED):
        return True
    return any(
        artifact.artifact_type in (ArtifactType.PARSED_TEXT_SNAPSHOT, ArtifactType.OCR_RAW)
        for artifact in artifacts
    )


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return numerator / denominator
