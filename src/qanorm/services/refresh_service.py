"""Document refresh service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.db.session import session_scope
from qanorm.db.types import JobType, ProcessingStatus, StatusNormalized
from qanorm.indexing.indexer import index_document_version
from qanorm.jobs.scheduler import create_job
from qanorm.logging import get_ingestion_logger
from qanorm.models import Document, DocumentSource, DocumentVersion, UpdateEvent
from qanorm.normalizers.codes import normalize_document_code
from qanorm.normalizers.statuses import resolve_status_conflict
from qanorm.parsers.card_parser import DocumentCardData, fetch_document_card, parse_document_card
from qanorm.repositories import (
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    UpdateEventRepository,
)
from qanorm.services.document_pipeline import (
    download_document_artifacts,
    extract_document_text,
    normalize_document_structure,
    persist_document_card,
    run_document_ocr,
)


logger = get_ingestion_logger()


@dataclass(slots=True)
class RefreshRequestResult:
    """Summary of a queued refresh request."""

    status: str
    document_code: str
    queued_job_id: str


@dataclass(slots=True)
class CurrentSourceMetadata:
    """Remote metadata fetched from the current source card."""

    document: Document
    current_version: DocumentVersion
    current_source: DocumentSource
    card_data: DocumentCardData
    source_status_normalized: StatusNormalized
    source_status_raw: str | None


@dataclass(slots=True)
class RefreshRequirement:
    """Decision whether the current document should be refreshed."""

    needs_refresh: bool
    status_changed: bool
    text_actualized_changed: bool
    description_actualized_changed: bool
    source_status_normalized: StatusNormalized
    source_status_raw: str | None
    reasons: list[str]


@dataclass(slots=True)
class RefreshJobResult:
    """Summary of a processed ``refresh_document`` job."""

    status: str
    document_code: str
    needs_refresh: bool
    refresh_reason: str | None
    current_version_id: str | None
    new_version_id: str | None
    event_id: str | None
    details: dict[str, Any]


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


def run_document_refresh(document_code: str) -> dict[str, Any]:
    """Run a refresh immediately in a managed session."""

    try:
        return asdict(process_refresh_document_job(document_code))
    except Exception as exc:
        normalized_code = normalize_document_code(document_code)
        return {
            "status": "failed",
            "document_code": normalized_code,
            "needs_refresh": True,
            "refresh_reason": "refresh_failed",
            "current_version_id": None,
            "new_version_id": None,
            "event_id": None,
            "details": {"error": str(exc)},
        }


def fetch_current_document_metadata(
    session: Session,
    *,
    document_code: str,
) -> CurrentSourceMetadata:
    """Fetch current remote metadata for a document from its latest known source card."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    source_repository = DocumentSourceRepository(session)

    normalized_code = normalize_document_code(document_code)
    document = document_repository.get_by_normalized_code(normalized_code)
    if document is None:
        raise ValueError(f"Document not found: {normalized_code}")

    current_version = None
    if document.current_version_id is not None:
        current_version = version_repository.get(document.current_version_id)
    if current_version is None:
        current_version = version_repository.get_active_for_document(document.id)
    if current_version is None:
        raise ValueError(f"No current version found for document: {normalized_code}")

    sources = source_repository.list_for_document_version(current_version.id)
    if not sources:
        raise ValueError(f"No source metadata found for current version: {normalized_code}")
    current_source = sources[-1]

    card_html = fetch_document_card(current_source.card_url)
    card_data = parse_document_card(
        current_source.card_url,
        card_html,
        source_list_status_raw=current_version.source_status_raw,
    )
    source_status_raw, source_status_normalized = resolve_status_conflict(
        card_data.source_list_status_raw,
        card_data.card_status_raw,
    )

    return CurrentSourceMetadata(
        document=document,
        current_version=current_version,
        current_source=current_source,
        card_data=card_data,
        source_status_normalized=source_status_normalized,
        source_status_raw=source_status_raw,
    )


def has_status_changed(
    local_status: StatusNormalized,
    source_status: StatusNormalized,
) -> bool:
    """Return whether the normalized status changed."""

    return local_status != source_status


def has_text_actualized_changed(
    local_text_actualized_at: date | None,
    source_text_actualized_at: date | None,
) -> bool:
    """Return whether source text metadata indicates a newer version."""

    return _is_newer_source_date(local_text_actualized_at, source_text_actualized_at)


def has_description_actualized_changed(
    local_description_actualized_at: date | None,
    source_description_actualized_at: date | None,
) -> bool:
    """Return whether source description metadata indicates a newer version."""

    return _is_newer_source_date(local_description_actualized_at, source_description_actualized_at)


def determine_refresh_requirement(metadata: CurrentSourceMetadata) -> RefreshRequirement:
    """Evaluate whether a refresh is needed based on remote metadata."""

    status_changed = has_status_changed(metadata.document.status_normalized, metadata.source_status_normalized)
    text_actualized_changed = has_text_actualized_changed(
        metadata.current_version.text_actualized_at,
        metadata.card_data.text_actualized_at,
    )
    description_actualized_changed = has_description_actualized_changed(
        metadata.current_version.description_actualized_at,
        metadata.card_data.description_actualized_at,
    )

    reasons: list[str] = []
    if status_changed:
        reasons.append("status_changed")
    if text_actualized_changed:
        reasons.append("text_actualized_changed")
    if description_actualized_changed:
        reasons.append("description_actualized_changed")

    return RefreshRequirement(
        needs_refresh=bool(reasons),
        status_changed=status_changed,
        text_actualized_changed=text_actualized_changed,
        description_actualized_changed=description_actualized_changed,
        source_status_normalized=metadata.source_status_normalized,
        source_status_raw=metadata.source_status_raw,
        reasons=reasons,
    )


def process_refresh_document_job(
    document_code: str,
    *,
    session: Session | None = None,
) -> RefreshJobResult:
    """Process a refresh job by re-fetching metadata and re-running the pipeline if needed."""

    if session is None:
        with session_scope() as managed_session:
            return process_refresh_document_job(document_code, session=managed_session)

    normalized_code = normalize_document_code(document_code)
    metadata = fetch_current_document_metadata(session, document_code=normalized_code)
    requirement = determine_refresh_requirement(metadata)

    if not requirement.needs_refresh:
        logger.info("Refresh skipped for %s: source metadata unchanged", normalized_code)
        event = _record_refresh_event(
            session,
            document=metadata.document,
            old_version_id=metadata.current_version.id,
            new_version_id=None,
            status="skipped_up_to_date",
            update_reason="metadata_unchanged",
            details=_build_comparison_details(requirement),
        )
        return RefreshJobResult(
            status="skipped_up_to_date",
            document_code=normalized_code,
            needs_refresh=False,
            refresh_reason=None,
            current_version_id=str(metadata.current_version.id),
            new_version_id=None,
            event_id=str(event.id),
            details=_build_comparison_details(requirement),
        )

    metadata.document.last_seen_at = datetime.now(timezone.utc)
    metadata.document.status_normalized = requirement.source_status_normalized
    session.flush()

    if requirement.source_status_normalized is not StatusNormalized.ACTIVE:
        final_status = (
            "skipped_inactive_source"
            if requirement.source_status_normalized is StatusNormalized.INACTIVE
            else "skipped_unknown_source"
        )
        logger.info(
            "Refresh skipped for %s: source status resolved to '%s'",
            normalized_code,
            requirement.source_status_normalized.value,
        )
        event = _record_refresh_event(
            session,
            document=metadata.document,
            old_version_id=metadata.current_version.id,
            new_version_id=None,
            status=final_status,
            update_reason=",".join(requirement.reasons),
            details=_build_comparison_details(requirement),
        )
        return RefreshJobResult(
            status=final_status,
            document_code=normalized_code,
            needs_refresh=True,
            refresh_reason=",".join(requirement.reasons),
            current_version_id=str(metadata.current_version.id),
            new_version_id=None,
            event_id=str(event.id),
            details=_build_comparison_details(requirement),
        )

    created_version_id: UUID | None = None
    try:
        creation_result = persist_document_card(
            session,
            card_data=metadata.card_data,
            list_page_url=metadata.current_source.list_page_url,
            seed_url=metadata.current_source.seed_url,
            queue_download_job=False,
        )
        if creation_result.document_version_id is None:
            raise ValueError(f"Refresh did not create a new version for document: {normalized_code}")
        created_version_id = UUID(creation_result.document_version_id)

        download_result = download_document_artifacts(
            session,
            document_version_id=created_version_id,
            document_code=metadata.card_data.document_code,
            card_url=metadata.card_data.card_url,
            html_url=metadata.card_data.html_url,
            pdf_url=metadata.card_data.pdf_url,
            print_url=metadata.card_data.print_url,
            has_full_html=metadata.card_data.has_full_html,
            has_page_images=metadata.card_data.has_page_images,
            queue_next_job=False,
        )
        extract_result = extract_document_text(
            session,
            document_version_id=created_version_id,
            queue_next_job=False,
        )
        ocr_result = None
        if extract_result.needs_ocr:
            ocr_result = run_document_ocr(
                session,
                document_version_id=created_version_id,
                queue_next_job=False,
            )
        normalize_result = normalize_document_structure(
            session,
            document_version_id=created_version_id,
            queue_next_job=False,
        )
        index_result = None
        if not normalize_result.deduplicated:
            index_result = index_document_version(
                session,
                document_version_id=created_version_id,
            )

        comparison_details = _build_comparison_details(requirement)
        pipeline_details = {
            "download_status": download_result.status,
            "extract_status": extract_result.status,
            "needs_ocr": extract_result.needs_ocr,
            "ocr_status": ocr_result.status if ocr_result is not None else None,
            "normalize_status": normalize_result.status,
            "deduplicated": normalize_result.deduplicated,
            "index_status": index_result.status if index_result is not None else None,
        }
        refresh_status = "skipped_duplicate" if normalize_result.deduplicated else "refresh_completed"
        logger.info(
            "Refresh finished for %s with status '%s' (new_version_id=%s)",
            normalized_code,
            refresh_status,
            created_version_id,
        )
        event = _record_refresh_event(
            session,
            document=metadata.document,
            old_version_id=metadata.current_version.id,
            new_version_id=created_version_id,
            status=refresh_status,
            update_reason=",".join(requirement.reasons),
            details={**comparison_details, **pipeline_details},
        )
        return RefreshJobResult(
            status=refresh_status,
            document_code=normalized_code,
            needs_refresh=True,
            refresh_reason=",".join(requirement.reasons),
            current_version_id=str(metadata.document.current_version_id or metadata.current_version.id),
            new_version_id=str(created_version_id),
            event_id=str(event.id),
            details={**comparison_details, **pipeline_details},
        )
    except Exception as exc:
        logger.exception("Refresh failed for %s", normalized_code)
        if created_version_id is not None:
            failed_version = DocumentVersionRepository(session).get(created_version_id)
            if failed_version is not None:
                failed_version.processing_status = ProcessingStatus.FAILED
                failed_version.is_active = False
                failed_version.is_outdated = False
        metadata.current_version.is_active = True
        metadata.current_version.is_outdated = False
        metadata.document.current_version_id = metadata.current_version.id
        session.flush()
        _record_refresh_event(
            session,
            document=metadata.document,
            old_version_id=metadata.current_version.id,
            new_version_id=created_version_id,
            status="refresh_failed",
            update_reason=",".join(requirement.reasons),
            details={
                **_build_comparison_details(requirement),
                "error": str(exc),
            },
        )
        raise


def _build_comparison_details(requirement: RefreshRequirement) -> dict[str, Any]:
    return {
        "status_changed": requirement.status_changed,
        "text_actualized_changed": requirement.text_actualized_changed,
        "description_actualized_changed": requirement.description_actualized_changed,
        "source_status": requirement.source_status_normalized.value,
        "source_status_raw": requirement.source_status_raw,
        "reasons": list(requirement.reasons),
    }


def _record_refresh_event(
    session: Session,
    *,
    document: Document,
    old_version_id: UUID | None,
    new_version_id: UUID | None,
    status: str,
    update_reason: str | None,
    details: dict[str, Any],
):
    repository = UpdateEventRepository(session)
    return repository.add(
        UpdateEvent(
            document_id=document.id,
            old_version_id=old_version_id,
            new_version_id=new_version_id,
            update_reason=update_reason,
            status=status,
            details=details,
        )
    )


def _is_newer_source_date(local_date: date | None, source_date: date | None) -> bool:
    if source_date is None:
        return False
    if local_date is None:
        return True
    return source_date > local_date
