from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import ArtifactType, JobStatus, QueryStatus, SessionChannel, SessionStatus
from qanorm.models import (
    AuditEvent,
    Document,
    DocumentNode,
    DocumentSource,
    DocumentVersion,
    FreshnessCheck,
    IngestionJob,
    QAAnswer,
    QAEvidence,
    QAMessage,
    QAQuery,
    QASession,
    QASubtask,
    RawArtifact,
    SearchEvent,
    SecurityEvent,
    ToolInvocation,
    TrustedSourceChunk,
    TrustedSourceDocument,
    TrustedSourceSyncRun,
    UpdateEvent,
    VerificationReport,
)
from qanorm.repositories import (
    AuditEventRepository,
    DocumentNodeRepository,
    DocumentRepository,
    DocumentVersionRepository,
    DocumentSourceRepository,
    FreshnessCheckRepository,
    IngestionJobRepository,
    QAAnswerRepository,
    QAEvidenceRepository,
    QAMessageRepository,
    QAQueryRepository,
    QASessionRepository,
    QASubtaskRepository,
    RawArtifactRepository,
    SearchEventRepository,
    SecurityEventRepository,
    ToolInvocationRepository,
    TrustedSourceRepository,
    UpdateEventRepository,
    VerificationReportRepository,
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


def test_qa_session_repository_get_by_channel_identifiers_uses_scalar_lookup() -> None:
    session = _mock_session()
    expected = QASession(channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = expected

    repository = QASessionRepository(session)
    result = repository.get_by_channel_identifiers(SessionChannel.WEB, external_user_id="user-1")

    assert result is expected
    session.execute.assert_called_once()


def test_qa_session_repository_update_session_state_flushes_changes() -> None:
    session = _mock_session()
    qa_session = QASession(channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)

    repository = QASessionRepository(session)
    repository.update_session_state(
        qa_session,
        status=SessionStatus.CLOSED,
        session_summary="summary",
    )

    assert qa_session.status == SessionStatus.CLOSED
    assert qa_session.session_summary == "summary"
    session.flush.assert_called_once()


def test_qa_message_repository_lists_session_history_in_order() -> None:
    session = _mock_session()
    session_id = uuid4()
    expected = [QAMessage(session_id=session_id, role="user", content="hello")]
    session.execute.return_value.scalars.return_value.all.return_value = expected

    repository = QAMessageRepository(session)
    result = repository.list_for_session(session_id)

    assert result == expected
    session.execute.assert_called_once()


def test_qa_query_repository_update_state_updates_flags() -> None:
    session = _mock_session()
    query = QAQuery(session_id=uuid4(), message_id=uuid4(), query_text="test", status=QueryStatus.PENDING)

    repository = QAQueryRepository(session)
    repository.update_state(
        query,
        status=QueryStatus.COMPLETED,
        used_open_web=True,
        used_trusted_web=True,
        requires_freshness_check=True,
    )

    assert query.status == QueryStatus.COMPLETED
    assert query.used_open_web is True
    assert query.used_trusted_web is True
    assert query.requires_freshness_check is True
    session.flush.assert_called_once()


def test_qa_subtask_repository_lists_subtasks_for_query() -> None:
    session = _mock_session()
    query_id = uuid4()
    expected = [QASubtask(query_id=query_id, subtask_type="normative", description="find")]
    session.execute.return_value.scalars.return_value.all.return_value = expected

    repository = QASubtaskRepository(session)
    result = repository.list_for_query(query_id)

    assert result == expected
    session.execute.assert_called_once()


def test_qa_evidence_repository_add_many_flushes_session() -> None:
    session = _mock_session()
    evidence = [QAEvidence(query_id=uuid4(), source_kind="normative")]

    repository = QAEvidenceRepository(session)
    result = repository.add_many(evidence)

    assert result == evidence
    session.add_all.assert_called_once_with(evidence)
    session.flush.assert_called_once()


def test_qa_answer_repository_get_by_query_uses_scalar_lookup() -> None:
    session = _mock_session()
    query_id = uuid4()
    expected = QAAnswer(query_id=query_id, answer_text="answer", answer_format="markdown")
    session.execute.return_value.scalar_one_or_none.return_value = expected

    repository = QAAnswerRepository(session)
    result = repository.get_by_query(query_id)

    assert result is expected
    session.execute.assert_called_once()


def test_supporting_stage2_repositories_flush_added_rows() -> None:
    session = _mock_session()
    query_id = uuid4()
    session_id = uuid4()
    subtask_id = uuid4()

    verification_report = VerificationReport(
        query_id=query_id,
        coverage_result="pass",
        citation_result="pass",
        hallucination_result="pass",
        source_labeling_result="pass",
    )
    freshness_check = FreshnessCheck(query_id=query_id, document_id=uuid4())
    security_event = SecurityEvent(session_id=session_id, event_type="prompt_injection")
    audit_event = AuditEvent(session_id=session_id, event_type="query_started", actor_kind="system")
    search_event = SearchEvent(provider_name="searxng", search_scope="open_web", query_text="test")
    tool_invocation = ToolInvocation(query_id=query_id, subtask_id=subtask_id, tool_name="search", tool_scope="web")

    assert VerificationReportRepository(session).add(verification_report) is verification_report
    assert FreshnessCheckRepository(session).add(freshness_check) is freshness_check
    assert SecurityEventRepository(session).add(security_event) is security_event
    assert AuditEventRepository(session).add(audit_event) is audit_event
    assert SearchEventRepository(session).add(search_event) is search_event
    assert ToolInvocationRepository(session).add(tool_invocation) is tool_invocation
    assert session.add.call_count == 6
    assert session.flush.call_count == 6


def test_freshness_check_repository_lists_results_for_query() -> None:
    session = _mock_session()
    query_id = uuid4()
    expected = [FreshnessCheck(query_id=query_id, document_id=uuid4())]
    session.execute.return_value.scalars.return_value.all.return_value = expected

    repository = FreshnessCheckRepository(session)
    result = repository.list_for_query(query_id)

    assert result == expected
    session.execute.assert_called_once()


def test_trusted_source_repository_save_document_updates_existing_row() -> None:
    session = _mock_session()
    existing = TrustedSourceDocument(source_domain="example.com", source_url="https://example.com/doc")
    session.execute.return_value.scalar_one_or_none.return_value = existing
    updated = TrustedSourceDocument(
        source_domain="example.com",
        source_url="https://example.com/doc",
        title="Updated",
        content_hash="1" * 64,
    )

    repository = TrustedSourceRepository(session)
    result = repository.save_document(updated)

    assert result is existing
    assert existing.title == "Updated"
    assert existing.content_hash == "1" * 64
    session.flush.assert_called_once()


def test_trusted_source_repository_replace_chunks_replaces_document_chunks() -> None:
    session = _mock_session()
    document_id = uuid4()
    chunks = [TrustedSourceChunk(document_id=document_id, chunk_index=0, text="chunk")]

    repository = TrustedSourceRepository(session)
    result = repository.replace_chunks(document_id, chunks)

    assert result == chunks
    session.execute.assert_called_once()
    session.add_all.assert_called_once_with(chunks)
    session.flush.assert_called_once()


def test_trusted_source_repository_save_sync_run_flushes() -> None:
    session = _mock_session()
    sync_run = TrustedSourceSyncRun(source_domain="example.com")

    repository = TrustedSourceRepository(session)
    result = repository.save_sync_run(sync_run)

    assert result is sync_run
    session.add.assert_called_once_with(sync_run)
    session.flush.assert_called_once()
