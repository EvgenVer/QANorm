"""Repositories for Stage 2A retrieval entities."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from qanorm.models import DocumentAlias, RetrievalUnit


class DocumentAliasRepository:
    """Data access helpers for alternative document aliases."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, alias: DocumentAlias) -> DocumentAlias:
        """Add an alias to the current session."""

        self.session.add(alias)
        self.session.flush()
        return alias

    def add_many(self, aliases: Iterable[DocumentAlias]) -> list[DocumentAlias]:
        """Add multiple aliases to the current session."""

        items = list(aliases)
        self.session.add_all(items)
        self.session.flush()
        return items

    def get(self, alias_id: UUID) -> DocumentAlias | None:
        """Load one alias by id."""

        return self.session.get(DocumentAlias, alias_id)

    def list_for_document(self, document_id: UUID) -> list[DocumentAlias]:
        """List aliases registered for one document."""

        stmt = (
            select(DocumentAlias)
            .where(DocumentAlias.document_id == document_id)
            .order_by(DocumentAlias.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_by_alias_normalized(self, alias_normalized: str) -> list[DocumentAlias]:
        """List candidate aliases by normalized lookup key."""

        stmt = (
            select(DocumentAlias)
            .where(DocumentAlias.alias_normalized == alias_normalized)
            .order_by(DocumentAlias.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def delete_for_document(self, document_id: UUID) -> int:
        """Delete all aliases for one canonical document."""

        stmt = delete(DocumentAlias).where(DocumentAlias.document_id == document_id)
        return int(self.session.execute(stmt).rowcount or 0)


class RetrievalUnitRepository:
    """Data access helpers for derived retrieval units."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, unit: RetrievalUnit) -> RetrievalUnit:
        """Add one retrieval unit to the current session."""

        self.session.add(unit)
        self.session.flush()
        return unit

    def add_many(self, units: Iterable[RetrievalUnit]) -> list[RetrievalUnit]:
        """Add multiple retrieval units to the current session."""

        items = list(units)
        self.session.add_all(items)
        self.session.flush()
        return items

    def get(self, unit_id: UUID) -> RetrievalUnit | None:
        """Load one retrieval unit by id."""

        return self.session.get(RetrievalUnit, unit_id)

    def list_for_document_version(self, document_version_id: UUID) -> list[RetrievalUnit]:
        """List retrieval units for one document version in document order."""

        stmt = (
            select(RetrievalUnit)
            .where(RetrievalUnit.document_version_id == document_version_id)
            .order_by(
                RetrievalUnit.start_order_index.asc().nullsfirst(),
                RetrievalUnit.created_at.asc(),
            )
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_for_document_version_and_type(self, document_version_id: UUID, unit_type: str) -> list[RetrievalUnit]:
        """List retrieval units for one document version filtered by type."""

        stmt = (
            select(RetrievalUnit)
            .where(
                RetrievalUnit.document_version_id == document_version_id,
                RetrievalUnit.unit_type == unit_type,
            )
            .order_by(
                RetrievalUnit.start_order_index.asc().nullsfirst(),
                RetrievalUnit.created_at.asc(),
            )
        )
        return list(self.session.execute(stmt).scalars().all())

    def delete_for_document_version(self, document_version_id: UUID) -> int:
        """Delete all retrieval units for one document version."""

        stmt = delete(RetrievalUnit).where(RetrievalUnit.document_version_id == document_version_id)
        return int(self.session.execute(stmt).rowcount or 0)

    def count_embeddings_pending(self) -> int:
        """Count retrieval units without generated embeddings."""

        stmt = select(func.count()).select_from(RetrievalUnit).where(RetrievalUnit.embedding.is_(None))
        return int(self.session.execute(stmt).scalar_one())

    def list_pending_embeddings(self, *, limit: int) -> list[RetrievalUnit]:
        """List retrieval units that still need embeddings."""

        stmt = (
            select(RetrievalUnit)
            .where(RetrievalUnit.embedding.is_(None))
            .order_by(
                RetrievalUnit.created_at.asc(),
                RetrievalUnit.start_order_index.asc().nullsfirst(),
                RetrievalUnit.id.asc(),
            )
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())
