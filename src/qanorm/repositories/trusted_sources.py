"""Repositories for trusted-source documents, chunks, and sync runs."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from qanorm.models import TrustedSourceChunk, TrustedSourceDocument, TrustedSourceSyncRun


class TrustedSourceRepository:
    """Data access helpers for trusted-source storage."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_sync_run(self, sync_run: TrustedSourceSyncRun) -> TrustedSourceSyncRun:
        """Insert a sync run and flush it."""

        self.session.add(sync_run)
        self.session.flush()
        return sync_run

    def save_document(self, document: TrustedSourceDocument) -> TrustedSourceDocument:
        """Upsert a trusted-source document by canonical source URL."""

        existing = self.session.execute(
            select(TrustedSourceDocument).where(TrustedSourceDocument.source_url == document.source_url).limit(1)
        ).scalar_one_or_none()
        if existing is None:
            self.session.add(document)
            self.session.flush()
            return document

        existing.last_sync_run_id = document.last_sync_run_id
        existing.source_domain = document.source_domain
        existing.title = document.title
        existing.content_hash = document.content_hash
        existing.published_at = document.published_at
        existing.retrieved_at = document.retrieved_at
        existing.metadata_json = document.metadata_json
        self.session.flush()
        return existing

    def replace_chunks(
        self,
        document_id: UUID,
        chunks: Iterable[TrustedSourceChunk],
    ) -> list[TrustedSourceChunk]:
        """Replace all stored chunks for one trusted-source document."""

        self.session.execute(delete(TrustedSourceChunk).where(TrustedSourceChunk.document_id == document_id))
        items = list(chunks)
        self.session.add_all(items)
        self.session.flush()
        return items
