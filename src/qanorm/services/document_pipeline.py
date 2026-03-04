"""Document processing pipeline service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.db.types import ArtifactType, JobType, ProcessingStatus, StatusNormalized
from qanorm.fetchers.html import fetch_html_document
from qanorm.fetchers.images import fetch_image_bytes
from qanorm.fetchers.pdf import fetch_pdf_bytes
from qanorm.jobs.scheduler import create_job
from qanorm.models import Document, DocumentSource, DocumentVersion, RawArtifact
from qanorm.normalizers.codes import normalize_document_code
from qanorm.normalizers.statuses import resolve_status_conflict
from qanorm.ocr.quality import calculate_ocr_confidence, is_low_confidence_parse
from qanorm.ocr.renderer import render_pdf_pages
from qanorm.ocr.tesseract import merge_ocr_page_texts, run_ocr_for_pages
from qanorm.parsers.card_parser import (
    DocumentCardData,
    extract_card_page_image_urls,
    fetch_document_card,
    parse_document_card,
)
from qanorm.parsers.html_document_parser import HtmlTextExtractionResult, extract_text_from_html_document
from qanorm.parsers.pdf_text_parser import PdfTextExtractionResult, extract_text_from_pdf
from qanorm.repositories import (
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    RawArtifactRepository,
)
from qanorm.storage.checksums import sha256_bytes, sha256_file
from qanorm.storage.paths import build_artifact_relative_path
from qanorm.storage.raw_store import RawFileStore


@dataclass(slots=True)
class DocumentCardProcessResult:
    """Result of processing a document card."""

    status: str
    skip_reason: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    queued_job_id: str | None = None


@dataclass(slots=True)
class DownloadArtifactsResult:
    """Result of downloading raw artifacts for a document version."""

    status: str
    saved_artifact_ids: list[str]
    saved_artifact_count: int
    html_missing: bool
    pdf_missing: bool
    queued_extract_text_job_id: str | None = None


@dataclass(slots=True)
class ExtractedTextResult:
    """Result of intermediate text extraction from raw artifacts."""

    status: str
    chosen_source: str
    text_length: int
    needs_ocr: bool
    saved_snapshot_count: int
    queued_job_id: str | None = None


@dataclass(slots=True)
class OCRProcessingResult:
    """Result of OCR fallback processing for a document version."""

    status: str
    page_count: int
    text_length: int
    parse_confidence: float
    low_confidence_parse: bool
    saved_artifact_count: int
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
            "document_code": card_data.document_code,
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


def download_document_artifacts(
    session: Session,
    *,
    document_version_id: UUID | str,
    document_code: str,
    card_url: str,
    html_url: str | None,
    pdf_url: str | None,
    print_url: str | None,
    has_full_html: bool,
    has_page_images: bool,
    raw_store: RawFileStore | None = None,
) -> DownloadArtifactsResult:
    """Download and persist raw artifacts for a document version."""

    artifact_repository = RawArtifactRepository(session)
    version_repository = DocumentVersionRepository(session)
    job_repository = IngestionJobRepository(session)
    store = raw_store or RawFileStore()
    version_uuid = UUID(str(document_version_id))
    version = version_repository.get(version_uuid)
    if version is None:
        raise ValueError(f"Document version not found: {document_version_id}")

    saved_artifacts: list[RawArtifact] = []
    html_missing = True
    pdf_missing = True

    html_candidate_url = html_url if has_full_html and html_url else print_url
    if html_candidate_url:
        html_missing = False
        saved_artifact = _download_text_artifact(
            artifact_repository=artifact_repository,
            raw_store=store,
            document_version_id=version_uuid,
            document_code=document_code,
            artifact_type=ArtifactType.HTML_RAW,
            source_url=html_candidate_url,
        )
        if saved_artifact is not None:
            saved_artifacts.append(saved_artifact)

    if pdf_url:
        pdf_missing = False
        saved_artifact = _download_binary_artifact(
            artifact_repository=artifact_repository,
            raw_store=store,
            document_version_id=version_uuid,
            document_code=document_code,
            artifact_type=ArtifactType.PDF_RAW,
            source_url=pdf_url,
        )
        if saved_artifact is not None:
            saved_artifacts.append(saved_artifact)

    if has_page_images:
        card_html = fetch_document_card(card_url)
        for page_index, image_url in enumerate(extract_card_page_image_urls(card_url, card_html), start=1):
            saved_artifact = _download_binary_artifact(
                artifact_repository=artifact_repository,
                raw_store=store,
                document_version_id=version_uuid,
                document_code=document_code,
                artifact_type=ArtifactType.PAGE_IMAGE,
                source_url=image_url,
                artifact_name=f"{ArtifactType.PAGE_IMAGE.value}_{page_index:04d}",
            )
            if saved_artifact is not None:
                saved_artifacts.append(saved_artifact)

    if saved_artifacts:
        version.processing_status = ProcessingStatus.DOWNLOADED
        session.flush()

    extract_job = create_job(
        job_repository,
        job_type=JobType.EXTRACT_TEXT,
        payload={"document_version_id": str(version_uuid)},
    )

    return DownloadArtifactsResult(
        status="ok",
        saved_artifact_ids=[str(artifact.id) for artifact in saved_artifacts],
        saved_artifact_count=len(saved_artifacts),
        html_missing=html_missing,
        pdf_missing=pdf_missing,
        queued_extract_text_job_id=str(extract_job.id),
    )


def extract_document_text(
    session: Session,
    *,
    document_version_id: UUID | str,
    raw_store: RawFileStore | None = None,
) -> ExtractedTextResult:
    """Extract intermediate text from stored HTML and/or PDF raw artifacts."""

    artifact_repository = RawArtifactRepository(session)
    version_repository = DocumentVersionRepository(session)
    document_repository = DocumentRepository(session)
    job_repository = IngestionJobRepository(session)
    store = raw_store or RawFileStore()
    version_uuid = UUID(str(document_version_id))
    version = version_repository.get(version_uuid)
    if version is None:
        raise ValueError(f"Document version not found: {document_version_id}")

    document = document_repository.get(version.document_id)
    if document is None:
        raise ValueError(f"Document not found for version: {document_version_id}")

    artifacts = artifact_repository.list_for_document_version(version_uuid)
    html_artifact = next((item for item in artifacts if item.artifact_type is ArtifactType.HTML_RAW), None)
    pdf_artifact = next((item for item in artifacts if item.artifact_type is ArtifactType.PDF_RAW), None)

    html_result: HtmlTextExtractionResult | None = None
    pdf_result: PdfTextExtractionResult | None = None
    saved_snapshots: list[RawArtifact] = []

    if html_artifact is not None:
        page_html = Path(html_artifact.storage_path).read_text(encoding="utf-8")
        html_result = extract_text_from_html_document(page_html)
        snapshot = _persist_text_snapshot_artifact(
            artifact_repository=artifact_repository,
            raw_store=store,
            document_version_id=version_uuid,
            document_code=document.display_code,
            snapshot_label="html",
            text=html_result.text,
        )
        if snapshot is not None:
            saved_snapshots.append(snapshot)

    if pdf_artifact is not None:
        pdf_result = extract_text_from_pdf(pdf_artifact.storage_path)
        snapshot = _persist_text_snapshot_artifact(
            artifact_repository=artifact_repository,
            raw_store=store,
            document_version_id=version_uuid,
            document_code=document.display_code,
            snapshot_label="pdf",
            text=pdf_result.combined_text,
        )
        if snapshot is not None:
            saved_snapshots.append(snapshot)

    chosen_source, chosen_text, confidence, needs_ocr = _choose_best_text_source(html_result, pdf_result)
    version.processing_status = ProcessingStatus.EXTRACTED
    version.parse_confidence = confidence
    session.flush()

    next_job = create_job(
        job_repository,
        job_type=JobType.RUN_OCR if needs_ocr else JobType.NORMALIZE_DOCUMENT,
        payload={"document_version_id": str(version_uuid)},
    )

    return ExtractedTextResult(
        status="ok",
        chosen_source=chosen_source,
        text_length=len(chosen_text),
        needs_ocr=needs_ocr,
        saved_snapshot_count=len(saved_snapshots),
        queued_job_id=str(next_job.id),
    )


def run_document_ocr(
    session: Session,
    *,
    document_version_id: UUID | str,
    raw_store: RawFileStore | None = None,
    render_dpi: int | None = None,
    languages: str | tuple[str, ...] | None = None,
) -> OCRProcessingResult:
    """Run OCR fallback for a stored document version and queue normalization."""

    artifact_repository = RawArtifactRepository(session)
    version_repository = DocumentVersionRepository(session)
    document_repository = DocumentRepository(session)
    job_repository = IngestionJobRepository(session)
    store = raw_store or RawFileStore()
    version_uuid = UUID(str(document_version_id))
    version = version_repository.get(version_uuid)
    if version is None:
        raise ValueError(f"Document version not found: {document_version_id}")

    document = document_repository.get(version.document_id)
    if document is None:
        raise ValueError(f"Document not found for version: {document_version_id}")

    artifacts = artifact_repository.list_for_document_version(version_uuid)
    page_image_paths = [Path(item.storage_path) for item in artifacts if item.artifact_type is ArtifactType.PAGE_IMAGE]

    if not page_image_paths:
        pdf_artifact = next((item for item in artifacts if item.artifact_type is ArtifactType.PDF_RAW), None)
        if pdf_artifact is None:
            raise ValueError(f"No OCR input artifacts found for version: {document_version_id}")

        with TemporaryDirectory(prefix="qanorm-ocr-") as temp_dir:
            rendered_pages = render_pdf_pages(
                pdf_artifact.storage_path,
                output_dir=temp_dir,
                dpi=render_dpi,
            )
            page_image_paths = [item.image_path for item in rendered_pages]
            page_results = run_ocr_for_pages(page_image_paths, languages=languages)
    else:
        page_results = run_ocr_for_pages(page_image_paths, languages=languages)

    combined_text = merge_ocr_page_texts(page_results)
    confidence = calculate_ocr_confidence([item.text for item in page_results])
    low_confidence_parse = is_low_confidence_parse(confidence)

    saved_artifacts: list[RawArtifact] = []
    ocr_artifact = _persist_artifact(
        artifact_repository=artifact_repository,
        raw_store=store,
        document_version_id=version_uuid,
        document_code=document.display_code,
        artifact_type=ArtifactType.OCR_RAW,
        payload=combined_text,
        source_url="snapshot://ocr.txt",
        artifact_name=ArtifactType.OCR_RAW.value,
        is_text=True,
    )
    if ocr_artifact is not None:
        saved_artifacts.append(ocr_artifact)

    version.has_ocr = True
    version.parse_confidence = confidence
    version.processing_status = ProcessingStatus.EXTRACTED
    session.flush()

    next_job = create_job(
        job_repository,
        job_type=JobType.NORMALIZE_DOCUMENT,
        payload={"document_version_id": str(version_uuid)},
    )

    return OCRProcessingResult(
        status="ok",
        page_count=len(page_results),
        text_length=len(combined_text),
        parse_confidence=confidence,
        low_confidence_parse=low_confidence_parse,
        saved_artifact_count=len(saved_artifacts),
        queued_job_id=str(next_job.id),
    )


def _detect_document_type(document_code: str) -> str:
    code_upper = document_code.upper()
    if code_upper.startswith("СП ") or code_upper.startswith("SP "):
        return "sp"
    if code_upper.startswith("СНИП ") or code_upper.startswith("SNIP "):
        return "snip"
    if code_upper.startswith("ФЕДЕРАЛЬНЫЙ ЗАКОН "):
        return "federal_law"
    if code_upper.startswith("ГОСТ") or code_upper.startswith("GOST"):
        return "gost"
    return "unknown"


def _choose_best_text_source(
    html_result: HtmlTextExtractionResult | None,
    pdf_result: PdfTextExtractionResult | None,
) -> tuple[str, str, float, bool]:
    html_text = html_result.text if html_result is not None else ""
    pdf_text = pdf_result.combined_text if pdf_result is not None else ""

    if html_result is not None and html_result.text_length >= 200:
        return "html", html_text, 1.0, False

    if pdf_result is not None and pdf_text and not pdf_result.needs_ocr:
        return "pdf", pdf_text, pdf_result.text_layer_score, False

    if html_result is not None and html_text:
        return "html", html_text, 0.8, False

    if pdf_result is not None:
        return "pdf", pdf_text, pdf_result.text_layer_score, pdf_result.needs_ocr

    return "none", "", 0.0, True


def _download_text_artifact(
    *,
    artifact_repository: RawArtifactRepository,
    raw_store: RawFileStore,
    document_version_id: UUID,
    document_code: str,
    artifact_type: ArtifactType,
    source_url: str,
) -> RawArtifact | None:
    payload = fetch_html_document(source_url)
    return _persist_artifact(
        artifact_repository=artifact_repository,
        raw_store=raw_store,
        document_version_id=document_version_id,
        document_code=document_code,
        artifact_type=artifact_type,
        payload=payload,
        source_url=source_url,
        is_text=True,
    )


def _persist_text_snapshot_artifact(
    *,
    artifact_repository: RawArtifactRepository,
    raw_store: RawFileStore,
    document_version_id: UUID,
    document_code: str,
    snapshot_label: str,
    text: str,
) -> RawArtifact | None:
    return _persist_artifact(
        artifact_repository=artifact_repository,
        raw_store=raw_store,
        document_version_id=document_version_id,
        document_code=document_code,
        artifact_type=ArtifactType.PARSED_TEXT_SNAPSHOT,
        payload=text,
        source_url=f"snapshot://{snapshot_label}.txt",
        artifact_name=f"{ArtifactType.PARSED_TEXT_SNAPSHOT.value}_{snapshot_label}",
        is_text=True,
    )


def _download_binary_artifact(
    *,
    artifact_repository: RawArtifactRepository,
    raw_store: RawFileStore,
    document_version_id: UUID,
    document_code: str,
    artifact_type: ArtifactType,
    source_url: str,
    artifact_name: str | None = None,
) -> RawArtifact | None:
    if artifact_type is ArtifactType.PDF_RAW:
        payload = fetch_pdf_bytes(source_url)
    else:
        payload = fetch_image_bytes(source_url)
    return _persist_artifact(
        artifact_repository=artifact_repository,
        raw_store=raw_store,
        document_version_id=document_version_id,
        document_code=document_code,
        artifact_type=artifact_type,
        payload=payload,
        source_url=source_url,
        artifact_name=artifact_name,
        is_text=False,
    )


def _persist_artifact(
    *,
    artifact_repository: RawArtifactRepository,
    raw_store: RawFileStore,
    document_version_id: UUID,
    document_code: str,
    artifact_type: ArtifactType,
    payload: str | bytes,
    source_url: str,
    artifact_name: str | None = None,
    is_text: bool,
) -> RawArtifact | None:
    extension = _infer_extension(source_url, fallback=".html" if is_text else ".bin")
    relative_path = build_artifact_relative_path(
        document_code=document_code,
        version_id=document_version_id,
        artifact_type=artifact_name or artifact_type.value,
        extension=extension,
    )

    existing = artifact_repository.get_by_version_and_relative_path(document_version_id, str(relative_path))
    if existing is not None:
        return None

    if raw_store.exists(relative_path):
        absolute_path = raw_store.base_path / relative_path
        artifact = RawArtifact(
            document_version_id=document_version_id,
            artifact_type=artifact_type,
            storage_path=str(absolute_path),
            relative_path=str(relative_path),
            mime_type=_infer_mime_type(extension),
            file_size=absolute_path.stat().st_size,
            checksum_sha256=sha256_file(absolute_path),
        )
        return artifact_repository.add(artifact)

    if is_text:
        payload_text = str(payload)
        absolute_path = raw_store.save_text(relative_path, payload_text)
        checksum = sha256_bytes(payload_text.encode("utf-8"))
    else:
        payload_bytes = bytes(payload)
        absolute_path = raw_store.save_bytes(relative_path, payload_bytes)
        checksum = sha256_bytes(payload_bytes)

    artifact = RawArtifact(
        document_version_id=document_version_id,
        artifact_type=artifact_type,
        storage_path=str(absolute_path),
        relative_path=str(relative_path),
        mime_type=_infer_mime_type(extension),
        file_size=absolute_path.stat().st_size,
        checksum_sha256=checksum,
    )
    return artifact_repository.add(artifact)


def _infer_extension(source_url: str, *, fallback: str) -> str:
    extension = Path(urlparse(source_url).path).suffix.lower()
    return extension or fallback


def _infer_mime_type(extension: str) -> str:
    normalized = extension.lower()
    if normalized == ".txt":
        return "text/plain"
    if normalized in {".html", ".htm"}:
        return "text/html"
    if normalized == ".pdf":
        return "application/pdf"
    if normalized == ".gif":
        return "image/gif"
    if normalized == ".png":
        return "image/png"
    if normalized in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "application/octet-stream"
