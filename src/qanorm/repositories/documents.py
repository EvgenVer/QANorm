"""Repositories for document and document version entities."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import Document, DocumentVersion


class DocumentRepository:
    """Data access helpers for canonical documents."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, document: Document) -> Document:
        """Add a document to the current session."""

        self.session.add(document)
        self.session.flush()
        return document

    def get(self, document_id: UUID) -> Document | None:
        """Load a document by id."""

        return self.session.get(Document, document_id)

    def get_by_normalized_code(self, normalized_code: str) -> Document | None:
        """Load a document by canonical code."""

        stmt = select(Document).where(Document.normalized_code == normalized_code)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_all(self) -> list[Document]:
        """Return all documents."""

        stmt = select(Document).order_by(Document.created_at.asc())
        return list(self.session.execute(stmt).scalars().all())


class DocumentVersionRepository:
    """Data access helpers for document versions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, version: DocumentVersion) -> DocumentVersion:
        """Add a document version to the current session."""

        self.session.add(version)
        self.session.flush()
        return version

    def add_many(self, versions: Iterable[DocumentVersion]) -> list[DocumentVersion]:
        """Add multiple document versions."""

        items = list(versions)
        self.session.add_all(items)
        self.session.flush()
        return items

    def get(self, version_id: UUID) -> DocumentVersion | None:
        """Load a document version by id."""

        return self.session.get(DocumentVersion, version_id)

    def get_active_for_document(self, document_id: UUID) -> DocumentVersion | None:
        """Load the active version for a document."""

        stmt = (
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.is_active.is_(True),
            )
            .order_by(DocumentVersion.created_at.desc())
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_document(self, document_id: UUID) -> list[DocumentVersion]:
        """List all versions for a document."""

        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
