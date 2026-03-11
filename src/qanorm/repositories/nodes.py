"""Repositories for document nodes and references."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import DocumentNode, DocumentReference


class DocumentNodeRepository:
    """Data access helpers for normalized document nodes."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, node: DocumentNode) -> DocumentNode:
        """Add a node to the current session."""

        self.session.add(node)
        self.session.flush()
        return node

    def add_many(self, nodes: Iterable[DocumentNode]) -> list[DocumentNode]:
        """Add multiple nodes to the current session."""

        items = list(nodes)
        self.session.add_all(items)
        self.session.flush()
        return items

    def get(self, node_id: UUID) -> DocumentNode | None:
        """Load a node by id."""

        return self.session.get(DocumentNode, node_id)

    def list_for_document_version(self, document_version_id: UUID) -> list[DocumentNode]:
        """List nodes for a document version in document order."""

        stmt = (
            select(DocumentNode)
            .where(DocumentNode.document_version_id == document_version_id)
            .order_by(DocumentNode.order_index.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_by_locator(self, document_version_id: UUID, locator_normalized: str) -> list[DocumentNode]:
        """List nodes with one normalized locator inside a document version."""

        stmt = (
            select(DocumentNode)
            .where(
                DocumentNode.document_version_id == document_version_id,
                DocumentNode.locator_normalized == locator_normalized,
            )
            .order_by(DocumentNode.order_index.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_neighbors(self, document_version_id: UUID, *, order_index: int, window: int) -> list[DocumentNode]:
        """List neighboring nodes around one order index."""

        stmt = (
            select(DocumentNode)
            .where(
                DocumentNode.document_version_id == document_version_id,
                DocumentNode.order_index >= order_index - window,
                DocumentNode.order_index <= order_index + window,
            )
            .order_by(DocumentNode.order_index.asc())
        )
        return list(self.session.execute(stmt).scalars().all())


class DocumentReferenceRepository:
    """Data access helpers for document references."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, reference: DocumentReference) -> DocumentReference:
        """Add a document reference."""

        self.session.add(reference)
        self.session.flush()
        return reference

    def add_many(self, references: Iterable[DocumentReference]) -> list[DocumentReference]:
        """Add multiple document references."""

        items = list(references)
        self.session.add_all(items)
        self.session.flush()
        return items

    def list_for_document_version(self, document_version_id: UUID) -> list[DocumentReference]:
        """List references extracted from a document version."""

        stmt = (
            select(DocumentReference)
            .where(DocumentReference.document_version_id == document_version_id)
            .order_by(DocumentReference.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
