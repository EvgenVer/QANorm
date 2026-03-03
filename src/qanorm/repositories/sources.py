"""Repositories for document sources and raw artifacts."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import DocumentSource, RawArtifact


class DocumentSourceRepository:
    """Data access helpers for document sources."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, source: DocumentSource) -> DocumentSource:
        """Add a source record."""

        self.session.add(source)
        self.session.flush()
        return source

    def add_many(self, sources: Iterable[DocumentSource]) -> list[DocumentSource]:
        """Add multiple source records."""

        items = list(sources)
        self.session.add_all(items)
        self.session.flush()
        return items

    def list_for_document_version(self, document_version_id: UUID) -> list[DocumentSource]:
        """List sources linked to a document version."""

        stmt = (
            select(DocumentSource)
            .where(DocumentSource.document_version_id == document_version_id)
            .order_by(DocumentSource.seen_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())


class RawArtifactRepository:
    """Data access helpers for raw artifacts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, artifact: RawArtifact) -> RawArtifact:
        """Add a raw artifact record."""

        self.session.add(artifact)
        self.session.flush()
        return artifact

    def add_many(self, artifacts: Iterable[RawArtifact]) -> list[RawArtifact]:
        """Add multiple raw artifact records."""

        items = list(artifacts)
        self.session.add_all(items)
        self.session.flush()
        return items

    def list_for_document_version(self, document_version_id: UUID) -> list[RawArtifact]:
        """List raw artifacts linked to a document version."""

        stmt = (
            select(RawArtifact)
            .where(RawArtifact.document_version_id == document_version_id)
            .order_by(RawArtifact.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
