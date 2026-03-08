"""Document indexing workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.db.session import session_scope
from qanorm.db.types import ProcessingStatus
from qanorm.indexing.embeddings import search_nodes_by_vector_similarity
from qanorm.indexing.fts import search_nodes_by_fts, update_nodes_full_text_index
from qanorm.models import DocumentNode
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import DocumentNodeRepository, DocumentRepository, DocumentVersionRepository


@dataclass(slots=True)
class ReindexResult:
    """Result of indexing one logical document scope."""

    status: str
    scope: str
    document_code: str | None
    indexed_version_id: str | None
    indexed_node_count: int
    cleared_node_count: int


@dataclass(slots=True)
class BulkReindexResult:
    """Result of a full reindex run."""

    status: str
    scope: str
    documents_processed: int
    indexed_documents: int
    indexed_node_count: int
    cleared_node_count: int


def index_document_version(
    session: Session,
    *,
    document_version_id: UUID | str,
) -> ReindexResult:
    """Index one document version if and only if it is the active version."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    node_repository = DocumentNodeRepository(session)

    version_uuid = UUID(str(document_version_id))
    version = version_repository.get(version_uuid)
    if version is None:
        raise ValueError(f"Document version not found: {document_version_id}")

    document = document_repository.get(version.document_id)
    if document is None:
        raise ValueError(f"Document not found for version: {document_version_id}")

    active_version = version_repository.get_active_for_document(document.id)
    if active_version is None or active_version.id != version.id or document.current_version_id != version.id:
        cleared_node_count = _clear_version_index(node_repository.list_for_document_version(version.id))
        return ReindexResult(
            status="skipped_inactive_version",
            scope="single-document",
            document_code=document.normalized_code,
            indexed_version_id=None,
            indexed_node_count=0,
            cleared_node_count=cleared_node_count,
        )

    indexed_nodes = node_repository.list_for_document_version(version.id)
    update_nodes_full_text_index(indexed_nodes)
    _clear_node_embeddings(indexed_nodes)

    cleared_node_count = 0
    for candidate_version in version_repository.list_for_document(document.id):
        if candidate_version.id == version.id:
            continue
        stale_nodes = node_repository.list_for_document_version(candidate_version.id)
        cleared_node_count += _clear_version_index(stale_nodes)

    version.processing_status = ProcessingStatus.INDEXED
    session.flush()
    return ReindexResult(
        status="ok",
        scope="single-document",
        document_code=document.normalized_code,
        indexed_version_id=str(version.id),
        indexed_node_count=len(indexed_nodes),
        cleared_node_count=cleared_node_count,
    )


def reindex_document_by_code(session: Session, *, document_code: str) -> ReindexResult:
    """Reindex the active version for one canonical document code."""

    document_repository = DocumentRepository(session)
    version_repository = DocumentVersionRepository(session)
    normalized_code = normalize_document_code(document_code)
    document = document_repository.get_by_normalized_code(normalized_code)
    if document is None:
        return ReindexResult(
            status="document_not_found",
            scope="single-document",
            document_code=normalized_code,
            indexed_version_id=None,
            indexed_node_count=0,
            cleared_node_count=0,
        )

    target_version = None
    if document.current_version_id is not None:
        target_version = version_repository.get(document.current_version_id)
    if target_version is None:
        target_version = version_repository.get_active_for_document(document.id)
    if target_version is None:
        return ReindexResult(
            status="no_active_version",
            scope="single-document",
            document_code=document.normalized_code,
            indexed_version_id=None,
            indexed_node_count=0,
            cleared_node_count=0,
        )

    return index_document_version(session, document_version_id=target_version.id)


def reindex_all_documents(session: Session) -> BulkReindexResult:
    """Reindex all canonical documents that have an active version."""

    document_repository = DocumentRepository(session)
    documents = document_repository.list_all()
    indexed_documents = 0
    indexed_node_count = 0
    cleared_node_count = 0

    for document in documents:
        if document.current_version_id is None:
            continue
        result = reindex_document_by_code(session, document_code=document.normalized_code)
        if result.status != "ok":
            continue
        indexed_documents += 1
        indexed_node_count += result.indexed_node_count
        cleared_node_count += result.cleared_node_count

    return BulkReindexResult(
        status="ok",
        scope="all-documents",
        documents_processed=len(documents),
        indexed_documents=indexed_documents,
        indexed_node_count=indexed_node_count,
        cleared_node_count=cleared_node_count,
    )


def reindex(document_code: str | None = None) -> dict[str, Any]:
    """Run reindexing through a managed database session."""

    with session_scope() as session:
        if document_code:
            result = reindex_document_by_code(session, document_code=document_code)
        else:
            result = reindex_all_documents(session)
    return asdict(result)


def search_indexed_nodes_by_text(
    nodes: list[DocumentNode],
    *,
    query: str,
    limit: int = 10,
) -> list[DocumentNode]:
    """Search a node collection using the prepared full-text payload."""

    return search_nodes_by_fts(nodes, query, limit=limit)


def search_indexed_nodes_by_vector(
    nodes: list[DocumentNode],
    *,
    query: str,
    limit: int = 10,
) -> list[DocumentNode]:
    """Search a node collection by vector similarity."""

    return search_nodes_by_vector_similarity(nodes, query, limit=limit)


def _clear_version_index(nodes: list[DocumentNode]) -> int:
    for node in nodes:
        node.text_tsv = None
        node.embedding = None
    return len(nodes)


def _clear_node_embeddings(nodes: list[DocumentNode]) -> None:
    """Drop transitional node-level embeddings after refreshing the text index."""

    # Stage 2 retrieval moves dense vectors to chunk-level storage, so node-level
    # placeholder embeddings must not be materialized again during reindex.
    for node in nodes:
        node.embedding = None
