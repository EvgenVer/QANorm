"""Document processing pipeline service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from qanorm.db.types import StatusNormalized, JobType
from qanorm.jobs.scheduler import create_job
from qanorm.models import Document, DocumentSource, DocumentVersion
from qanorm.normalizers.codes import normalize_document_code
from qanorm.normalizers.statuses import resolve_status_conflict
from qanorm.parsers.card_parser import DocumentCardData, fetch_document_card, parse_document_card
from qanorm.repositories import (
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
)


@dataclass(slots=True)
class DocumentCardProcessResult:
    """Result of processing a document card."""

    status: str
    skip_reason: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    queued_job_id: str | None = None


def get_pipeline_status() -> dict[str, Any]:
    """Return a minimal pipeline status snapshot."""

    return {
        "status": "ready",
        "message": "Document pipeline shell is available.",
    }


def process_document_card(
    session: Session,
    *,
    card_url: str,
    list_status_raw: str | None,
    list_page_url: str | None = None,
    seed_url: str | None = None,
) -> DocumentCardProcessResult:
    """Fetch, parse and persist one document card."""

    page_html = fetch_document_card(card_url)
    card_data = parse_document_card(card_url, page_html, source_list_status_raw=list_status_raw)
    return persist_document_card(
        session,
        card_data=card_data,
        list_page_url=list_page_url,
        seed_url=seed_url,
    )


def persist_document_card(
    session: Session,
    *,
    card_data: DocumentCardData,
    list_page_url: str | None = None,
    seed_url: str | None = None,
) -> DocumentCardProcessResult:
    """Persist parsed card metadata and queue artifact downloads for active documents."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    source_repository = DocumentSourceRepository(session)
    job_repository = IngestionJobRepository(session)

    resolved_status_raw, resolved_status = resolve_status_conflict(
        card_data.source_list_status_raw,
        card_data.card_status_raw,
    )
    if resolved_status is StatusNormalized.INACTIVE:
        return DocumentCardProcessResult(status="skipped", skip_reason="inactive")
    if resolved_status is StatusNormalized.UNKNOWN:
        return DocumentCardProcessResult(status="skipped", skip_reason="unknown_status")

    normalized_code = normalize_document_code(card_data.document_code)
    now = datetime.now(timezone.utc)
    document = document_repository.get_by_normalized_code(normalized_code)
    if document is None:
        document = Document(
            normalized_code=normalized_code,
            display_code=card_data.document_code,
            document_type=_detect_document_type(card_data.document_code),
            title=card_data.document_title,
            status_normalized=resolved_status,
        )
        document_repository.add(document)
    else:
        document.display_code = card_data.document_code
        document.document_type = document.document_type or _detect_document_type(card_data.document_code)
        document.title = card_data.document_title
        document.status_normalized = resolved_status
        document.last_seen_at = now
        session.flush()

    version = DocumentVersion(
        document_id=document.id,
        edition_label=card_data.edition_label,
        source_status_raw=resolved_status_raw,
        status_normalized=resolved_status,
        text_actualized_at=card_data.text_actualized_at,
        description_actualized_at=card_data.description_actualized_at,
        published_at=card_data.published_at,
        effective_from=card_data.effective_from,
        is_active=False,
        is_outdated=False,
    )
    version_repository.add(version)

    source = DocumentSource(
        document_id=document.id,
        document_version_id=version.id,
        seed_url=seed_url,
        list_page_url=list_page_url,
        card_url=card_data.card_url,
        html_url=card_data.html_url,
        pdf_url=card_data.pdf_url,
        print_url=card_data.print_url,
        source_type=card_data.source_type,
    )
    source_repository.add(source)

    download_job = create_job(
        job_repository,
        job_type=JobType.DOWNLOAD_ARTIFACTS,
        payload={
            "document_version_id": str(version.id),
            "card_url": card_data.card_url,
            "html_url": card_data.html_url,
            "pdf_url": card_data.pdf_url,
            "print_url": card_data.print_url,
            "has_full_html": card_data.has_full_html,
            "has_page_images": card_data.has_page_images,
        },
    )

    return DocumentCardProcessResult(
        status="queued",
        document_id=str(document.id),
        document_version_id=str(version.id),
        queued_job_id=str(download_job.id),
    )


def _detect_document_type(document_code: str) -> str:
    code_upper = document_code.upper()
    if code_upper.startswith("СП "):
        return "sp"
    if code_upper.startswith("СНИП "):
        return "snip"
    if code_upper.startswith("ФЕДЕРАЛЬНЫЙ ЗАКОН "):
        return "federal_law"
    if code_upper.startswith("ГОСТ"):
        return "gost"
    return "unknown"
