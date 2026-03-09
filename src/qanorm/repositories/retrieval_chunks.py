"""Repositories for retrieval chunks derived from normalized document nodes."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from qanorm.models import RetrievalChunk


class RetrievalChunkRepository:
    """Data access helpers for retrieval-optimized chunk rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_many(self, chunks: Iterable[RetrievalChunk]) -> list[RetrievalChunk]:
        """Insert multiple retrieval chunks in one flush."""

        items = list(chunks)
        self.session.add_all(items)
        self.session.flush()
        return items

    def delete_for_document_version(self, document_version_id: UUID) -> None:
        """Remove all chunks for one document version before a rebuild."""

        self.session.execute(delete(RetrievalChunk).where(RetrievalChunk.document_version_id == document_version_id))
        self.session.flush()

    def list_for_document_version(self, document_version_id: UUID) -> list[RetrievalChunk]:
        """Return chunks for one document version in deterministic order."""

        stmt = (
            select(RetrievalChunk)
            .where(RetrievalChunk.document_version_id == document_version_id)
            .order_by(RetrievalChunk.chunk_index.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_active(self, *, document_ids: list[UUID] | None = None) -> list[RetrievalChunk]:
        """Return active retrieval chunks, optionally narrowed to a document subset."""

        stmt = select(RetrievalChunk).where(RetrievalChunk.is_active.is_(True))
        if document_ids:
            stmt = stmt.where(RetrievalChunk.document_id.in_(document_ids))
        stmt = stmt.order_by(RetrievalChunk.document_id.asc(), RetrievalChunk.chunk_index.asc())
        return list(self.session.execute(stmt).scalars().all())

    def list_by_hashes(self, chunk_hashes: list[str]) -> list[RetrievalChunk]:
        """Return all chunks matching a set of chunk hashes."""

        if not chunk_hashes:
            return []
        stmt = select(RetrievalChunk).where(RetrievalChunk.chunk_hash.in_(chunk_hashes))
        return list(self.session.execute(stmt).scalars().all())
