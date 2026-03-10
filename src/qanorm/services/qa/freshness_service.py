"""Stage 2 freshness-check orchestration built on top of Stage 1 refresh logic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.audit import AuditWriter
from qanorm.db.types import EvidenceSourceKind, FreshnessCheckStatus, FreshnessStatus, JobType
from qanorm.jobs.scheduler import create_job
from qanorm.models import Document, DocumentSource, DocumentVersion, FreshnessCheck, IngestionJob, QAEvidence, QAQuery, UpdateEvent
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import (
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    FreshnessCheckRepository,
    IngestionJobRepository,
    QAAnswerRepository,
    QAMessageRepository,
    UpdateEventRepository,
)
from qanorm.services.refresh_service import (
    CurrentSourceMetadata,
    determine_refresh_requirement,
    fetch_current_document_metadata,
)
from qanorm.observability import increment_event, set_verification_metric
from qanorm.utils.text import normalize_whitespace

if TYPE_CHECKING:
    from qanorm.agents.answer_synthesizer import StructuredAnswer


FreshnessJobScheduler = Callable[[FreshnessCheck], Awaitable[dict[str, Any]] | dict[str, Any] | None]


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


async def connect_freshness_branch(
    session: Session,
    *,
    query: QAQuery,
    evidence_rows: Iterable[QAEvidence],
    scheduler: FreshnessJobScheduler | None = None,
) -> list[FreshnessCheck]:
    """Schedule non-blocking freshness checks and optionally queue worker jobs."""

    scheduled = schedule_freshness_checks(
        session,
        query_id=query.id,
        evidence_rows=evidence_rows,
        query_requires_freshness_check=query.requires_freshness_check,
        reason="orchestrator_non_blocking_branch",
    )
    if not scheduled:
        return []

    AuditWriter(session).write(
        session_id=query.session_id,
        query_id=query.id,
        event_type="freshness_branch_scheduled",
        actor_kind="freshness_service",
        payload_json={
            "scheduled_check_count": len(scheduled),
            "document_ids": [str(item.document_id) for item in scheduled],
            "non_blocking": True,
        },
    )
    if scheduler is not None:
        for check in scheduled:
            maybe_result = scheduler(check)
            if maybe_result is not None and hasattr(maybe_result, "__await__"):
                await maybe_result
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


def build_freshness_warning_messages(
    checks: Iterable[FreshnessCheck | FreshnessEvaluationResult],
) -> list[str]:
    """Build user-facing stale-source warnings with local and remote editions."""

    warnings: list[str] = []
    for item in checks:
        check_status = item.check_status if isinstance(item, FreshnessEvaluationResult) else item.check_status
        if check_status == FreshnessCheckStatus.FRESH:
            continue
        document_code = item.document_code if isinstance(item, FreshnessEvaluationResult) else _resolve_check_document_code(item)
        local_edition = item.local_edition_label or "unknown"
        remote_edition = item.remote_edition_label or "unknown"
        if check_status == FreshnessCheckStatus.STALE:
            warnings.append(
                f"Freshness warning for {document_code}: the answer used local edition '{local_edition}' "
                f"while remote edition '{remote_edition}' is newer and refresh has not completed yet."
            )
        elif check_status == FreshnessCheckStatus.REFRESH_IN_PROGRESS:
            warnings.append(
                f"Freshness warning for {document_code}: local edition '{local_edition}' is being refreshed "
                f"towards remote edition '{remote_edition}'."
            )
        elif check_status == FreshnessCheckStatus.REFRESH_FAILED:
            warnings.append(
                f"Freshness warning for {document_code}: remote edition '{remote_edition}' was detected, "
                f"but refresh from local edition '{local_edition}' failed."
            )
        elif check_status == FreshnessCheckStatus.FAILED:
            warnings.append(
                f"Freshness warning for {document_code}: document freshness could not be verified; "
                f"local edition '{local_edition}' was used."
            )
    return warnings


def annotate_answer_with_freshness(
    answer: "StructuredAnswer",
    *,
    checks: Iterable[FreshnessCheck | FreshnessEvaluationResult],
) -> "StructuredAnswer":
    """Attach freshness warnings and edition annotations to a synthesized answer."""

    freshness_warnings = build_freshness_warning_messages(checks)
    if not freshness_warnings:
        return answer

    warnings = [*answer.warnings]
    for warning in freshness_warnings:
        if warning not in warnings:
            warnings.append(warning)

    freshness_block = "### Freshness\n\n" + "\n".join(f"- {item}" for item in freshness_warnings)
    markdown = answer.markdown if "### Freshness" in answer.markdown else f"{answer.markdown}\n\n{freshness_block}"
    answer_text = answer.answer_text
    if freshness_warnings:
        answer_text = f"{answer.answer_text}\n\n" + "\n".join(freshness_warnings)

    return replace(
        answer,
        answer_text=answer_text.strip(),
        markdown=markdown.strip(),
        has_stale_sources=True,
        warnings=warnings,
    )


def enrich_persisted_answer_with_freshness(
    session: Session,
    *,
    query_id: UUID,
) -> dict[str, Any]:
    """Update the persisted answer and assistant message with freshness warnings."""

    answer_repository = QAAnswerRepository(session)
    message_repository = QAMessageRepository(session)
    check_repository = FreshnessCheckRepository(session)

    query = session.get(QAQuery, query_id)
    if query is None:
        raise ValueError(f"Query not found for freshness enrichment: {query_id}")

    answer_row = answer_repository.get_by_query(query.id)
    if answer_row is None:
        return {"status": "skipped", "reason": "answer_not_found", "query_id": str(query.id)}

    checks = check_repository.list_for_query(query.id)
    warnings = build_freshness_warning_messages(checks)
    if not warnings:
        return {"status": "skipped", "reason": "no_actionable_freshness_results", "query_id": str(query.id)}

    freshness_block = "### Freshness\n\n" + "\n".join(f"- {item}" for item in warnings)
    if "### Freshness" not in answer_row.answer_text:
        answer_row.answer_text = f"{answer_row.answer_text}\n\n{freshness_block}".strip()
    answer_row.has_stale_sources = True
    answer_repository.save(answer_row)

    assistant_message = message_repository.get_latest_assistant_for_session(query.session_id)
    if assistant_message is not None:
        if "### Freshness" not in assistant_message.content:
            assistant_message.content = f"{assistant_message.content}\n\n{freshness_block}".strip()
        metadata = dict(assistant_message.metadata_json or {})
        metadata["has_stale_sources"] = True
        metadata["freshness_warnings"] = warnings
        assistant_message.metadata_json = metadata
        message_repository.save(assistant_message)

    AuditWriter(session).write(
        session_id=query.session_id,
        query_id=query.id,
        event_type="post_answer_enrichment_completed",
        actor_kind="freshness_service",
        payload_json={"warning_count": len(warnings)},
    )
    return {
        "status": "ok",
        "query_id": str(query.id),
        "warning_count": len(warnings),
        "assistant_message_updated": assistant_message is not None,
    }


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


def _resolve_check_document_code(check: FreshnessCheck) -> str:
    """Best-effort document label for warning messages without extra joins."""

    if check.document is not None and check.document.normalized_code:
        return check.document.normalized_code
    details = check.details_json or {}
    return str(details.get("document_code") or check.document_id)


def _iso_date(value: Any) -> str | None:
    """Normalize optional date-like values into ISO text for persisted details."""

    if value is None:
        return None
    return value.isoformat()


def _normalize_label(value: str | None) -> str:
    """Build a stable comparison key for optional edition labels."""

    return normalize_whitespace(value or "").casefold()
