"""Document version deduplication and activation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.models import Document, DocumentVersion, UpdateEvent
from qanorm.repositories import DocumentRepository, DocumentVersionRepository, UpdateEventRepository
from qanorm.storage.checksums import sha256_bytes
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True)
class VersionComparisonResult:
    """Comparison result for a candidate version against the current active version."""

    content_hash: str
    is_duplicate: bool
    active_version_id: UUID | None


@dataclass(slots=True)
class VersionActivationResult:
    """Activation result for a processed version."""

    status: str
    content_hash: str
    old_version_id: UUID | None
    new_version_id: UUID
    event_id: UUID | None


def find_existing_document_by_normalized_code(
    session: Session,
    *,
    normalized_code: str,
) -> Document | None:
    """Find an existing canonical document by normalized code."""

    repository = DocumentRepository(session)
    return repository.get_by_normalized_code(normalized_code)


def compute_version_content_hash(text: str) -> str:
    """Build a stable content hash from normalized text."""

    normalized_lines = [
        normalize_whitespace(line)
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if normalize_whitespace(line)
    ]
    payload = "\n".join(normalized_lines).encode("utf-8")
    return sha256_bytes(payload)


def compare_candidate_version_to_active(
    session: Session,
    *,
    document_version_id: UUID | str,
    content_text: str,
) -> VersionComparisonResult:
    """Compare a processed candidate version to the active version using content hash."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    version = _require_version(version_repository, document_version_id)
    document = _require_document(document_repository, version)
    active_version = version_repository.get_active_for_document(document.id)
    content_hash = compute_version_content_hash(content_text)

    is_duplicate = bool(
        active_version is not None
        and active_version.id != version.id
        and active_version.content_hash == content_hash
    )
    return VersionComparisonResult(
        content_hash=content_hash,
        is_duplicate=is_duplicate,
        active_version_id=active_version.id if active_version is not None else None,
    )


def skip_duplicate_version(
    session: Session,
    *,
    document_version_id: UUID | str,
    content_hash: str,
    duplicate_of_version_id: UUID | str | None,
) -> VersionActivationResult:
    """Mark a candidate version as outdated when its content matches the active version."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    event_repository = UpdateEventRepository(session)

    version = _require_version(version_repository, document_version_id)
    document = _require_document(document_repository, version)
    active_version = version_repository.get_active_for_document(document.id)

    version.content_hash = content_hash
    version.is_active = False
    version.is_outdated = True
    if active_version is not None:
        document.current_version_id = active_version.id

    event = event_repository.add(
        UpdateEvent(
            document_id=document.id,
            old_version_id=_to_uuid(duplicate_of_version_id),
            new_version_id=version.id,
            update_reason="same_content_hash",
            status="skipped_duplicate",
            details={
                "content_hash": content_hash,
                "duplicate_of_version_id": str(duplicate_of_version_id) if duplicate_of_version_id else None,
            },
        )
    )
    return VersionActivationResult(
        status="skipped_duplicate",
        content_hash=content_hash,
        old_version_id=_to_uuid(duplicate_of_version_id),
        new_version_id=version.id,
        event_id=event.id,
    )


def activate_processed_version(
    session: Session,
    *,
    document_version_id: UUID | str,
    content_hash: str,
) -> VersionActivationResult:
    """Activate a newly processed version and retire the old active version."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    event_repository = UpdateEventRepository(session)

    version = _require_version(version_repository, document_version_id)
    document = _require_document(document_repository, version)
    active_version = version_repository.get_active_for_document(document.id)

    previous_active_id: UUID | None = None
    if active_version is not None and active_version.id != version.id:
        previous_active_id = active_version.id
        active_version.is_active = False
        active_version.is_outdated = True

    version.content_hash = content_hash
    version.is_active = True
    version.is_outdated = False
    document.current_version_id = version.id

    event = event_repository.add(
        UpdateEvent(
            document_id=document.id,
            old_version_id=previous_active_id,
            new_version_id=version.id,
            update_reason="new_content_hash" if previous_active_id is not None else "initial_activation",
            status="activated",
            details={
                "content_hash": content_hash,
                "previous_active_version_id": str(previous_active_id) if previous_active_id else None,
            },
        )
    )
    return VersionActivationResult(
        status="activated",
        content_hash=content_hash,
        old_version_id=previous_active_id,
        new_version_id=version.id,
        event_id=event.id,
    )


def _require_version(repository: DocumentVersionRepository, document_version_id: UUID | str) -> DocumentVersion:
    version = repository.get(_to_uuid(document_version_id))
    if version is None:
        raise ValueError(f"Document version not found: {document_version_id}")
    return version


def _require_document(repository: DocumentRepository, version: DocumentVersion) -> Document:
    document = repository.get(version.document_id)
    if document is None:
        raise ValueError(f"Document not found for version: {version.id}")
    return document


def _to_uuid(value: UUID | str | None) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))
