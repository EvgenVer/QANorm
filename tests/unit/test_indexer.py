from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

from qanorm.db.types import ProcessingStatus, StatusNormalized
from qanorm.indexing.embeddings import batch_get_text_embeddings, search_nodes_by_vector_similarity, update_nodes_embeddings
from qanorm.indexing.fts import build_text_tsv, search_nodes_by_fts, update_nodes_full_text_index
from qanorm.indexing.indexer import (
    BulkReindexResult,
    ReindexResult,
    index_document_version,
    reindex,
    reindex_all_documents,
    reindex_document_by_code,
)
from qanorm.models import Document, DocumentNode, DocumentVersion


class _ScalarListResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> "_ScalarListResult":
        return self

    def all(self) -> list[object]:
        return self._values


class _ScalarOneResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


def _mock_session() -> MagicMock:
    return MagicMock()


def test_build_text_tsv_updates_nodes_and_supports_fts_search() -> None:
    nodes = [
        DocumentNode(document_version_id=uuid4(), node_type="point", text="Main requirement for concrete", order_index=1),
        DocumentNode(document_version_id=uuid4(), node_type="point", text="Secondary appendix note", order_index=2),
    ]

    lexemes = build_text_tsv("Main requirement for concrete")
    updated = update_nodes_full_text_index(nodes)
    found = search_nodes_by_fts(nodes, "concrete requirement")

    assert "main" in lexemes
    assert updated == 2
    assert nodes[0].text_tsv is not None
    assert found[0] is nodes[0]


def test_embeddings_support_batch_vectorization_and_similarity_search() -> None:
    nodes = [
        DocumentNode(document_version_id=uuid4(), node_type="point", text="Concrete load calculation", order_index=1),
        DocumentNode(document_version_id=uuid4(), node_type="point", text="Fire safety appendix", order_index=2),
    ]

    embeddings = batch_get_text_embeddings([node.text for node in nodes])
    updated = update_nodes_embeddings(nodes)
    found = search_nodes_by_vector_similarity(nodes, "load calculation for concrete")

    assert len(embeddings) == 2
    assert len(embeddings[0]) == len(nodes[0].embedding or [])
    assert updated == 2
    assert nodes[0].embedding is not None
    assert found[0] is nodes[0]


def test_index_document_version_indexes_only_active_version_and_clears_stale_nodes() -> None:
    document_id = uuid4()
    active_version_id = uuid4()
    old_version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=active_version_id,
    )
    active_version = DocumentVersion(id=active_version_id, document_id=document_id, is_active=True)
    old_version = DocumentVersion(id=old_version_id, document_id=document_id, is_active=False)
    active_nodes = [
        DocumentNode(document_version_id=active_version_id, node_type="point", text="Concrete load calculation", order_index=1),
        DocumentNode(document_version_id=active_version_id, node_type="point", text="Wind load rule", order_index=2),
    ]
    stale_node = DocumentNode(
        document_version_id=old_version_id,
        node_type="point",
        text="Old text",
        order_index=1,
        text_tsv="old text",
        embedding=[0.5, 0.5],
    )

    session = _mock_session()
    session.get.side_effect = [active_version, document]
    session.execute.side_effect = [
        _ScalarOneResult(active_version),
        _ScalarListResult(active_nodes),
        _ScalarListResult([old_version, active_version]),
        _ScalarListResult([stale_node]),
    ]

    result = index_document_version(session, document_version_id=active_version_id)

    assert result.status == "ok"
    assert result.indexed_version_id == str(active_version_id)
    assert result.indexed_node_count == 2
    assert result.cleared_node_count == 1
    assert active_version.processing_status is ProcessingStatus.INDEXED
    assert all(node.text_tsv for node in active_nodes)
    assert all(node.embedding for node in active_nodes)
    assert stale_node.text_tsv is None
    assert stale_node.embedding is None
    session.flush.assert_called_once()


def test_index_document_version_skips_non_current_inactive_version() -> None:
    document_id = uuid4()
    inactive_version_id = uuid4()
    active_version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="GOST R 1.0",
        display_code="GOST R 1.0",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=active_version_id,
    )
    inactive_version = DocumentVersion(id=inactive_version_id, document_id=document_id, is_active=False)
    active_version = DocumentVersion(id=active_version_id, document_id=document_id, is_active=True)
    stale_nodes = [
        DocumentNode(
            document_version_id=inactive_version_id,
            node_type="point",
            text="Legacy indexed text",
            order_index=1,
            text_tsv="legacy indexed text",
            embedding=[0.1, 0.2],
        )
    ]

    session = _mock_session()
    session.get.side_effect = [inactive_version, document]
    session.execute.side_effect = [
        _ScalarOneResult(active_version),
        _ScalarListResult(stale_nodes),
    ]

    result = index_document_version(session, document_version_id=inactive_version_id)

    assert result.status == "skipped_inactive_version"
    assert result.indexed_node_count == 0
    assert result.cleared_node_count == 1
    assert stale_nodes[0].text_tsv is None
    assert stale_nodes[0].embedding is None
    session.flush.assert_not_called()


def test_reindex_document_by_code_handles_not_found_and_active_document() -> None:
    session = _mock_session()
    session.execute.return_value.scalar_one_or_none.return_value = None
    missing = reindex_document_by_code(session, document_code="sp 1.0")
    assert missing.status == "document_not_found"

    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 1.0",
        display_code="SP 1.0",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=version_id,
    )
    version = DocumentVersion(id=version_id, document_id=document_id, is_active=True)

    active_session = _mock_session()
    active_session.execute.return_value.scalar_one_or_none.return_value = document
    active_session.get.return_value = version

    with patch(
        "qanorm.indexing.indexer.index_document_version",
        return_value=ReindexResult(
            status="ok",
            scope="single-document",
            document_code="SP 1.0",
            indexed_version_id=str(version_id),
            indexed_node_count=3,
            cleared_node_count=1,
        ),
    ) as index_mock:
        result = reindex_document_by_code(active_session, document_code="sp 1.0")

    assert result.status == "ok"
    index_mock.assert_called_once_with(active_session, document_version_id=version_id)


def test_reindex_dispatches_single_and_all_scopes() -> None:
    fake_session = object()

    @contextmanager
    def fake_scope():
        yield fake_session

    with patch("qanorm.indexing.indexer.session_scope", side_effect=fake_scope):
        with patch(
            "qanorm.indexing.indexer.reindex_document_by_code",
            return_value=ReindexResult(
                status="ok",
                scope="single-document",
                document_code="SP 1.0",
                indexed_version_id="ver-1",
                indexed_node_count=2,
                cleared_node_count=1,
            ),
        ) as single_mock:
            single_result = reindex(document_code="SP 1.0")

        with patch(
            "qanorm.indexing.indexer.reindex_all_documents",
            return_value=BulkReindexResult(
                status="ok",
                scope="all-documents",
                documents_processed=2,
                indexed_documents=1,
                indexed_node_count=4,
                cleared_node_count=2,
            ),
        ) as all_mock:
            all_result = reindex()

    assert single_result["scope"] == "single-document"
    assert single_result["indexed_node_count"] == 2
    single_mock.assert_called_once_with(fake_session, document_code="SP 1.0")
    assert all_result["scope"] == "all-documents"
    assert all_result["indexed_documents"] == 1
    all_mock.assert_called_once_with(fake_session)


def test_reindex_all_documents_aggregates_successful_results_only() -> None:
    documents = [
        Document(id=uuid4(), normalized_code="SP 1.0", display_code="SP 1.0", current_version_id=uuid4()),
        Document(id=uuid4(), normalized_code="GOST R 1.0", display_code="GOST R 1.0", current_version_id=None),
        Document(id=uuid4(), normalized_code="SNIP 2.0", display_code="SNIP 2.0", current_version_id=uuid4()),
    ]
    session = _mock_session()
    session.execute.return_value.scalars.return_value.all.return_value = documents

    with patch(
        "qanorm.indexing.indexer.reindex_document_by_code",
        side_effect=[
            ReindexResult("ok", "single-document", "SP 1.0", "v1", 3, 1),
            ReindexResult("document_not_found", "single-document", "SNIP 2.0", None, 0, 0),
        ],
    ) as reindex_mock:
        result = reindex_all_documents(session)

    assert result.documents_processed == 3
    assert result.indexed_documents == 1
    assert result.indexed_node_count == 3
    assert result.cleared_node_count == 1
    assert reindex_mock.call_count == 2
