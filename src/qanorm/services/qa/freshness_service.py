"""Stage 2 freshness-check orchestration built on top of Stage 1 refresh logic."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.audit import AuditWriter
from qanorm.db.types import EvidenceSourceKind, FreshnessCheckStatus, FreshnessStatus, JobType
from qanorm.jobs.scheduler import create_job
from qanorm.models import Document, DocumentSource, DocumentVersion, FreshnessCheck, IngestionJob, QAEvidence, UpdateEvent
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import (
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    FreshnessCheckRepository,
    IngestionJobRepository,
    UpdateEventRepository,
)
from qanorm.services.refresh_service import (
    CurrentSourceMetadata,
    determine_refresh_requirement,
    fetch_current_document_metadata,
)
from qanorm.observability import increment_event, set_verification_metric
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True, frozen=True)
class LocalDocumentFreshnessState:
    """Local document snapshot used as the baseline for one freshness check."""

    document: Document
    current_version: DocumentVersion
    current_source: DocumentSource | None


@dataclass(slots=True, frozen=True)
class FreshnessEvaluationResult:
    """Normalized outcome of one freshness verification run."""

    freshness_check_id: UUID
    document_id: UUID
    document_code: str
    check_status: FreshnessCheckStatus
    freshness_status: FreshnessStatus
    local_edition_label: str | None
    remote_edition_label: str | None
    refresh_job_id: UUID | None
    reasons: list[str]
    details: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Expose a transport-friendly payload for worker jobs and APIs."""

        payload = asdict(self)
        payload["freshness_check_id"] = str(self.freshness_check_id)
        payload["document_id"] = str(self.document_id)
        payload["refresh_job_id"] = str(self.refresh_job_id) if self.refresh_job_id is not None else None
        payload["check_status"] = self.check_status.value
        payload["freshness_status"] = self.freshness_status.value
        return payload


def should_run_freshness_check(
    *,
    query_requires_freshness_check: bool,
    evidence: QAEvidence,
) -> bool:
    """Return whether one evidence row should trigger a freshness check."""

    return (
        query_requires_freshness_check
        and evidence.source_kind == EvidenceSourceKind.NORMATIVE
        and evidence.document_id is not None
    )


def schedule_freshness_checks(
    session: Session,
    *,
    query_id: UUID,
    evidence_rows: Iterable[QAEvidence],
    query_requires_freshness_check: bool,
    reason: str | None = None,
) -> list[FreshnessCheck]:
    """Persist one pending freshness check per unique normative document."""

    repository = FreshnessCheckRepository(session)
    version_repository = DocumentVersionRepository(session)
    scheduled: list[FreshnessCheck] = []
    seen_document_ids: set[UUID] = set()

    for evidence in evidence_rows:
        if not should_run_freshness_check(
            query_requires_freshness_check=query_requires_freshness_check,
            evidence=evidence,
        ):
            continue
        assert evidence.document_id is not None
        if evidence.document_id in seen_document_ids:
            continue
        seen_document_ids.add(evidence.document_id)

        current_version = evidence.document_version or version_repository.get_active_for_document(evidence.document_id)
        scheduled.append(
            repository.add(
                FreshnessCheck(
                    query_id=query_id,
                    document_id=evidence.document_id,
                    document_version_id=current_version.id if current_version is not None else evidence.document_version_id,
                    check_status=FreshnessCheckStatus.PENDING,
                    local_edition_label=current_version.edition_label if current_version is not None else evidence.edition_label,
                    details_json={
                        "reason": reason or "normative_evidence",
                        "scheduled_from_query": str(query_id),
                        "scheduled_from_evidence_id": str(evidence.id) if evidence.id is not None else None,
                    },
                )
            )
        )

    return scheduled


def load_local_document_freshness_state(
    session: Session,
    *,
    document_id: UUID | None = None,
    document_code: str | None = None,
) -> LocalDocumentFreshnessState:
    """Load the local active document/version/source tuple used by freshness checks."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    source_repository = DocumentSourceRepository(session)

    if document_id is not None:
        document = document_repository.get(document_id)
    elif document_code is not None:
        document = document_repository.get_by_normalized_code(normalize_document_code(document_code))
    else:
        raise ValueError("Either document_id or document_code is required.")

    if document is None:
        raise ValueError("Document was not found for freshness check.")

    current_version = None
    if document.current_version_id is not None:
        current_version = version_repository.get(document.current_version_id)
    if current_version is None:
        current_version = version_repository.get_active_for_document(document.id)
    if current_version is None:
        raise ValueError(f"No active version found for document: {document.normalized_code}")

    # Freshness checks use the latest known source card as the remote comparison anchor.
    sources = source_repository.list_for_document_version(current_version.id)
    current_source = sources[-1] if sources else None
    return LocalDocumentFreshnessState(
        document=document,
        current_version=current_version,
        current_source=current_source,
    )


def evaluate_freshness_check(
    session: Session,
    *,
    freshness_check_id: UUID,
    auto_queue_refresh: bool = False,
) -> FreshnessEvaluationResult:
    """Execute one persisted freshness check and update its stored status."""

    repository = FreshnessCheckRepository(session)
    check = repository.get(freshness_check_id)
    if check is None:
        raise ValueError(f"Freshness check not found: {freshness_check_id}")

    local_state = load_local_document_freshness_state(session, document_id=check.document_id)
    try:
        metadata = fetch_current_document_metadata(session, document_code=local_state.document.normalized_code)
    except Exception as exc:
        result = _persist_failure(
            session,
            check=check,
            local_state=local_state,
            error_message=str(exc),
        )
        increment_event("freshness_check", status=result.check_status.value)
        set_verification_metric(f"freshness_{result.check_status.value}", 1.0)
        return result

    result = _compare_local_and_remote(
        session,
        check=check,
        local_state=local_state,
        metadata=metadata,
        auto_queue_refresh=auto_queue_refresh,
    )
    AuditWriter(session).write(
        session_id=check.query.session_id if check.query is not None else None,
        query_id=check.query_id,
        event_type="freshness_evaluated",
        actor_kind="freshness_service",
        payload_json=result.to_payload(),
    )
    increment_event("freshness_check", status=result.check_status.value)
    set_verification_metric(f"freshness_{result.check_status.value}", 1.0)
    return result


def queue_refresh_for_freshness_check(
    session: Session,
    *,
    freshness_check_id: UUID,
) -> FreshnessEvaluationResult:
    """Queue a Stage 1 refresh job for one freshness check and mark it as in progress."""

    repository = FreshnessCheckRepository(session)
    check = repository.get(freshness_check_id)
    if check is None:
        raise ValueError(f"Freshness check not found: {freshness_check_id}")

    local_state = load_local_document_freshness_state(session, document_id=check.document_id)
    refresh_job = _ensure_refresh_job(session, document_code=local_state.document.normalized_code)
    check.check_status = FreshnessCheckStatus.REFRESH_IN_PROGRESS
    check.refresh_job_id = refresh_job.id
    check.local_edition_label = local_state.current_version.edition_label
    check.details_json = {
        **(check.details_json or {}),
        "refresh_job_status": refresh_job.status.value,
        "refresh_requested_for_document": local_state.document.normalized_code,
    }
    session.flush()
    result = FreshnessEvaluationResult(
        freshness_check_id=check.id,
        document_id=local_state.document.id,
        document_code=local_state.document.normalized_code,
        check_status=FreshnessCheckStatus.REFRESH_IN_PROGRESS,
        freshness_status=FreshnessStatus.REFRESH_IN_PROGRESS,
        local_edition_label=check.local_edition_label,
        remote_edition_label=check.remote_edition_label,
        refresh_job_id=refresh_job.id,
        reasons=["refresh_queued"],
        details=dict(check.details_json or {}),
    )
    AuditWriter(session).write(
        session_id=check.query.session_id if check.query is not None else None,
        query_id=check.query_id,
        event_type="refresh_queued",
        actor_kind="freshness_service",
        payload_json=result.to_payload(),
    )
    return result


def _compare_local_and_remote(
    session: Session,
    *,
    check: FreshnessCheck,
    local_state: LocalDocumentFreshnessState,
    metadata: CurrentSourceMetadata,
    auto_queue_refresh: bool,
) -> FreshnessEvaluationResult:
    """Compare local and remote metadata and persist the normalized status."""

    requirement = determine_refresh_requirement(metadata)
    local_edition_label = local_state.current_version.edition_label
    remote_edition_label = metadata.card_data.edition_label
    edition_label_changed = _normalize_label(local_edition_label) != _normalize_label(remote_edition_label)

    reasons = list(requirement.reasons)
    if edition_label_changed:
        reasons.append("edition_label_changed")

    needs_refresh = requirement.needs_refresh or edition_label_changed
    refresh_job: IngestionJob | None = None
    if needs_refresh and auto_queue_refresh:
        refresh_job = _ensure_refresh_job(session, document_code=local_state.document.normalized_code)

    check_status, freshness_status = _resolve_freshness_status(
        session,
        document=local_state.document,
        needs_refresh=needs_refresh,
        refresh_job=refresh_job,
    )
    details = {
        "local_status": local_state.document.status_normalized.value,
        "remote_status": metadata.source_status_normalized.value,
        "local_text_actualized_at": _iso_date(local_state.current_version.text_actualized_at),
        "remote_text_actualized_at": _iso_date(metadata.card_data.text_actualized_at),
        "local_description_actualized_at": _iso_date(local_state.current_version.description_actualized_at),
        "remote_description_actualized_at": _iso_date(metadata.card_data.description_actualized_at),
        "local_edition_label": local_edition_label,
        "remote_edition_label": remote_edition_label,
        "edition_label_changed": edition_label_changed,
        "status_changed": requirement.status_changed,
        "text_actualized_changed": requirement.text_actualized_changed,
        "description_actualized_changed": requirement.description_actualized_changed,
        "reasons": reasons,
    }

    check.document_version_id = local_state.current_version.id
    check.local_edition_label = local_edition_label
    check.remote_edition_label = remote_edition_label
    check.check_status = check_status
    check.refresh_job_id = refresh_job.id if refresh_job is not None else None
    check.details_json = details
    session.flush()

    return FreshnessEvaluationResult(
        freshness_check_id=check.id,
        document_id=local_state.document.id,
        document_code=local_state.document.normalized_code,
        check_status=check_status,
        freshness_status=_map_to_freshness_status(check_status),
        local_edition_label=local_edition_label,
        remote_edition_label=remote_edition_label,
        refresh_job_id=refresh_job.id if refresh_job is not None else None,
        reasons=reasons,
        details=details,
    )


def _persist_failure(
    session: Session,
    *,
    check: FreshnessCheck,
    local_state: LocalDocumentFreshnessState,
    error_message: str,
) -> FreshnessEvaluationResult:
    """Persist a terminal check failure when remote metadata could not be fetched."""

    details = {
        "error": error_message,
        "local_edition_label": local_state.current_version.edition_label,
    }
    check.document_version_id = local_state.current_version.id
    check.local_edition_label = local_state.current_version.edition_label
    check.check_status = FreshnessCheckStatus.FAILED
    check.details_json = details
    session.flush()
    return FreshnessEvaluationResult(
        freshness_check_id=check.id,
        document_id=local_state.document.id,
        document_code=local_state.document.normalized_code,
        check_status=FreshnessCheckStatus.FAILED,
        freshness_status=FreshnessStatus.UNKNOWN,
        local_edition_label=check.local_edition_label,
        remote_edition_label=None,
        refresh_job_id=None,
        reasons=["remote_metadata_failed"],
        details=details,
    )


def _resolve_freshness_status(
    session: Session,
    *,
    document: Document,
    needs_refresh: bool,
    refresh_job: IngestionJob | None,
) -> tuple[FreshnessCheckStatus, FreshnessStatus]:
    """Map comparison results plus refresh activity into persisted statuses."""

    if not needs_refresh:
        return FreshnessCheckStatus.FRESH, FreshnessStatus.FRESH
    if refresh_job is not None:
        return FreshnessCheckStatus.REFRESH_IN_PROGRESS, FreshnessStatus.REFRESH_IN_PROGRESS

    latest_event = _load_latest_update_event(session, document_id=document.id)
    if latest_event is not None and latest_event.status == "refresh_failed":
        return FreshnessCheckStatus.REFRESH_FAILED, FreshnessStatus.REFRESH_FAILED
    return FreshnessCheckStatus.STALE, FreshnessStatus.STALE


def _ensure_refresh_job(session: Session, *, document_code: str) -> IngestionJob:
    """Create or reuse a deduplicated Stage 1 refresh job for one document."""

    repository = IngestionJobRepository(session)
    payload = {"document_code": normalize_document_code(document_code)}
    return create_job(
        repository,
        job_type=JobType.REFRESH_DOCUMENT,
        payload=payload,
    )


def _load_latest_update_event(session: Session, *, document_id: UUID) -> UpdateEvent | None:
    """Return the most recent update event for one document, if any."""

    events = UpdateEventRepository(session).list_for_document(document_id)
    return events[-1] if events else None


def _map_to_freshness_status(check_status: FreshnessCheckStatus) -> FreshnessStatus:
    """Translate check-status enum into evidence-facing freshness status."""

    mapping = {
        FreshnessCheckStatus.FRESH: FreshnessStatus.FRESH,
        FreshnessCheckStatus.STALE: FreshnessStatus.STALE,
        FreshnessCheckStatus.REFRESH_IN_PROGRESS: FreshnessStatus.REFRESH_IN_PROGRESS,
        FreshnessCheckStatus.REFRESH_FAILED: FreshnessStatus.REFRESH_FAILED,
        FreshnessCheckStatus.FAILED: FreshnessStatus.UNKNOWN,
        FreshnessCheckStatus.PENDING: FreshnessStatus.UNKNOWN,
    }
    return mapping[check_status]


def _iso_date(value: Any) -> str | None:
    """Normalize optional date-like values into ISO text for persisted details."""

    if value is None:
        return None
    return value.isoformat()


def _normalize_label(value: str | None) -> str:
    """Build a stable comparison key for optional edition labels."""

    return normalize_whitespace(value or "").casefold()
