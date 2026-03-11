from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import ArtifactType, JobStatus
from qanorm.models import Document, DocumentAlias, DocumentNode, DocumentSource, DocumentVersion, IngestionJob, RawArtifact, RetrievalUnit, UpdateEvent
from qanorm.repositories import (
    DocumentAliasRepository,
    DocumentNodeRepository,
    DocumentReferenceRepository,
    DocumentRepository,
    DocumentSourceRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    RawArtifactRepository,
    RetrievalUnitRepository,
    UpdateEventRepository,
)


def _mock_session() -> MagicMock:
    return MagicMock()


def test_document_repository_get_by_normalized_code_uses_scalar_lookup() -> None:
    session = _mock_session()
    expected = Document(normalized_code="gost-1", display_code="ГОСТ 1")
    session.execute.return_value.scalar_one_or_none.return_value = expected

    repository = DocumentRepository(session)
    result = repository.get_by_normalized_code("gost-1")

    assert result is expected
    session.execute.assert_called_once()


def test_document_version_repository_get_active_for_document_returns_active_version() -> None:
    session = _mock_session()
    document_id = uuid4()
    expected = DocumentVersion(document_id=document_id, is_active=True)
    session.execute.return_value.scalar_one_or_none.return_value = expected

    repository = DocumentVersionRepository(session)
    result = repository.get_active_for_document(document_id)

    assert result is expected
    session.execute.assert_called_once()


def test_document_node_repository_add_many_flushes_session() -> None:
    session = _mock_session()
    nodes = [
        DocumentNode(document_version_id=uuid4(), node_type="section", text="A", order_index=1),
        DocumentNode(document_version_id=uuid4(), node_type="section", text="B", order_index=2),
    ]

    repository = DocumentNodeRepository(session)
    result = repository.add_many(nodes)

    assert result == nodes
    session.add_all.assert_called_once_with(nodes)
    session.flush.assert_called_once()


def test_document_node_repository_list_for_document_version_uses_ordered_query() -> None:
    session = _mock_session()
    version_id = uuid4()
    expected_nodes = [
        DocumentNode(document_version_id=version_id, node_type="point", text="One", order_index=1),
        DocumentNode(document_version_id=version_id, node_type="point", text="Two", order_index=2),
    ]
    session.execute.return_value.scalars.return_value.all.return_value = expected_nodes

    repository = DocumentNodeRepository(session)
    result = repository.list_for_document_version(version_id)

    assert result == expected_nodes
    session.execute.assert_called_once()


def test_document_alias_repository_add_many_flushes_session() -> None:
    session = _mock_session()
    document_id = uuid4()
    aliases = [
        DocumentAlias(document_id=document_id, alias_raw="SP 20.13330.2016", alias_normalized="sp 20.13330.2016", alias_type="display_code"),
        DocumentAlias(document_id=document_id, alias_raw="SP 20", alias_normalized="sp 20", alias_type="short_code"),
    ]

    repository = DocumentAliasRepository(session)
    result = repository.add_many(aliases)

    assert result == aliases
    session.add_all.assert_called_once_with(aliases)
    session.flush.assert_called_once()


def test_document_alias_repository_list_by_alias_normalized_uses_lookup_query() -> None:
    session = _mock_session()
    expected = [DocumentAlias(document_id=uuid4(), alias_raw="SP 20", alias_normalized="sp 20", alias_type="short_code")]
    session.execute.return_value.scalars.return_value.all.return_value = expected

    repository = DocumentAliasRepository(session)
    result = repository.list_by_alias_normalized("sp 20")

    assert result == expected
    session.execute.assert_called_once()


def test_document_reference_repository_add_many_flushes_session() -> None:
    session = _mock_session()
    repository = DocumentReferenceRepository(session)
    references = []

    assert repository.add_many(references) == []
    session.add_all.assert_called_once_with(references)
    session.flush.assert_called_once()


def test_retrieval_unit_repository_add_many_flushes_session() -> None:
    session = _mock_session()
    version_id = uuid4()
    units = [
        RetrievalUnit(document_version_id=version_id, unit_type="document_card", text="Card summary", chunk_hash="1" * 64),
        RetrievalUnit(
            document_version_id=version_id,
            unit_type="semantic_block",
            start_order_index=1,
            end_order_index=2,
            text="Block text",
            chunk_hash="2" * 64,
        ),
    ]

    repository = RetrievalUnitRepository(session)
    result = repository.add_many(units)

    assert result == units
    session.add_all.assert_called_once_with(units)
    session.flush.assert_called_once()


def test_retrieval_unit_repository_lists_units_for_document_version() -> None:
    session = _mock_session()
    version_id = uuid4()
    expected = [
        RetrievalUnit(document_version_id=version_id, unit_type="semantic_block", start_order_index=1, text="One", chunk_hash="1" * 64),
        RetrievalUnit(document_version_id=version_id, unit_type="semantic_block", start_order_index=2, text="Two", chunk_hash="2" * 64),
    ]
    session.execute.return_value.scalars.return_value.all.return_value = expected

    repository = RetrievalUnitRepository(session)
    result = repository.list_for_document_version(version_id)

    assert result == expected
    session.execute.assert_called_once()


def test_ingestion_job_repository_claim_next_ready_job_marks_job_running() -> None:
    session = _mock_session()
    claimed_at = datetime.now(timezone.utc)
    job = IngestionJob(payload={}, scheduled_at=claimed_at)
    job.status = JobStatus.PENDING
    session.execute.return_value.scalar_one_or_none.return_value = job

    repository = IngestionJobRepository(session)
    result = repository.claim_next_ready_job(now=claimed_at)

    assert result is job
    assert job.status == JobStatus.RUNNING
    assert job.started_at == claimed_at
    session.flush.assert_called_once()


def test_ingestion_job_repository_claim_next_ready_job_returns_none_when_queue_is_empty() -> None:
    session = _mock_session()
    session.execute.return_value.scalar_one_or_none.return_value = None

    repository = IngestionJobRepository(session)
    result = repository.claim_next_ready_job(now=datetime.now(timezone.utc))

    assert result is None
    session.flush.assert_not_called()


def test_ingestion_job_repository_mark_failed_updates_status_attempts_and_error() -> None:
    session = _mock_session()
    finished_at = datetime.now(timezone.utc)
    job = IngestionJob(payload={})
    job.status = JobStatus.RUNNING
    job.attempt_count = 0

    repository = IngestionJobRepository(session)
    repository.mark_failed(job, "boom", finished_at=finished_at)

    assert job.status == JobStatus.FAILED
    assert job.attempt_count == 1
    assert job.last_error == "boom"
    assert job.finished_at == finished_at
    session.flush.assert_called_once()


def test_source_and_artifact_repositories_add_many_flush_session() -> None:
    session = _mock_session()
    sources = [DocumentSource(document_id=uuid4(), document_version_id=uuid4(), card_url="https://example.com")]
    artifacts = [
        RawArtifact(
            document_version_id=uuid4(),
            artifact_type=ArtifactType.HTML_RAW,
            storage_path="data/raw/doc.html",
            relative_path="doc.html",
            checksum_sha256="0" * 64,
        )
    ]

    source_repository = DocumentSourceRepository(session)
    artifact_repository = RawArtifactRepository(session)

    assert source_repository.add_many(sources) == sources
    assert artifact_repository.add_many(artifacts) == artifacts
    assert session.add_all.call_count == 2
    assert session.flush.call_count == 2


def test_update_event_repository_lists_events_for_document() -> None:
    session = _mock_session()
    document_id = uuid4()
    expected = [UpdateEvent(document_id=document_id, status="success")]
    session.execute.return_value.scalars.return_value.all.return_value = expected

    repository = UpdateEventRepository(session)
    result = repository.list_for_document(document_id)

    assert result == expected
    session.execute.assert_called_once()
