from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.agents.answer_synthesizer import StructuredAnswer
from qanorm.db.types import AnswerMode, EvidenceSourceKind, FreshnessCheckStatus, FreshnessStatus, JobStatus, JobType, StatusNormalized
from qanorm.models import Document, DocumentSource, DocumentVersion, FreshnessCheck, IngestionJob, QAEvidence, QAMessage, QAQuery, UpdateEvent
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.parsers.card_parser import DocumentCardData
from qanorm.services.qa.freshness_service import (
    annotate_answer_with_freshness,
    build_freshness_warning_messages,
    connect_freshness_branch,
    enrich_persisted_answer_with_freshness,
    evaluate_freshness_check,
    load_local_document_freshness_state,
    schedule_freshness_checks,
    should_run_freshness_check,
)
from qanorm.workers.stage2 import document_refresh_job, freshness_check_job, post_answer_enrichment_job


def test_should_run_freshness_check_only_for_normative_rows() -> None:
    normative = QAEvidence(query_id=uuid4(), source_kind=EvidenceSourceKind.NORMATIVE, document_id=uuid4())
    open_web = QAEvidence(query_id=uuid4(), source_kind=EvidenceSourceKind.OPEN_WEB, document_id=None)

    assert should_run_freshness_check(query_requires_freshness_check=True, evidence=normative) is True
    assert should_run_freshness_check(query_requires_freshness_check=False, evidence=normative) is False
    assert should_run_freshness_check(query_requires_freshness_check=True, evidence=open_web) is False


def test_schedule_freshness_checks_deduplicates_documents(monkeypatch) -> None:
    session = MagicMock()
    document_id = uuid4()
    version_id = uuid4()
    evidence_rows = [
        QAEvidence(query_id=uuid4(), source_kind=EvidenceSourceKind.NORMATIVE, document_id=document_id, document_version_id=version_id),
        QAEvidence(query_id=uuid4(), source_kind=EvidenceSourceKind.NORMATIVE, document_id=document_id, document_version_id=version_id),
        QAEvidence(query_id=uuid4(), source_kind=EvidenceSourceKind.OPEN_WEB, document_id=None),
    ]

    class _FakeVersionRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def get_active_for_document(self, _document_id):
            return DocumentVersion(id=version_id, document_id=document_id, edition_label="2024")

    monkeypatch.setattr("qanorm.services.qa.freshness_service.DocumentVersionRepository", _FakeVersionRepository)

    checks = schedule_freshness_checks(
        session,
        query_id=uuid4(),
        evidence_rows=evidence_rows,
        query_requires_freshness_check=True,
    )

    assert len(checks) == 1
    assert checks[0].document_id == document_id
    assert checks[0].check_status == FreshnessCheckStatus.PENDING


def test_load_local_document_freshness_state_uses_current_version_and_latest_source(monkeypatch) -> None:
    session = MagicMock()
    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 1",
        display_code="SP 1",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=version_id,
    )
    version = DocumentVersion(id=version_id, document_id=document_id, is_active=True)
    older_source = DocumentSource(id=uuid4(), document_id=document_id, document_version_id=version_id, card_url="https://old")
    latest_source = DocumentSource(id=uuid4(), document_id=document_id, document_version_id=version_id, card_url="https://latest")

    class _FakeDocumentRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def get(self, value):
            assert value == document_id
            return document

        def get_by_normalized_code(self, _value):
            return None

    class _FakeVersionRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def get(self, value):
            assert value == version_id
            return version

        def get_active_for_document(self, _document_id):
            return version

    class _FakeSourceRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def list_for_document_version(self, _document_version_id):
            return [older_source, latest_source]

    monkeypatch.setattr("qanorm.services.qa.freshness_service.DocumentRepository", _FakeDocumentRepository)
    monkeypatch.setattr("qanorm.services.qa.freshness_service.DocumentVersionRepository", _FakeVersionRepository)
    monkeypatch.setattr("qanorm.services.qa.freshness_service.DocumentSourceRepository", _FakeSourceRepository)

    state = load_local_document_freshness_state(session, document_id=document_id)

    assert state.document is document
    assert state.current_version is version
    assert state.current_source is latest_source


def test_evaluate_freshness_check_marks_document_fresh(monkeypatch) -> None:
    session = MagicMock()
    check_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 1",
        display_code="SP 1",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=version_id,
    )
    version = DocumentVersion(id=version_id, document_id=document_id, edition_label="2024", is_active=True)
    check = FreshnessCheck(id=check_id, query_id=uuid4(), document_id=document_id, document_version_id=version_id)

    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.FreshnessCheckRepository",
        lambda _session: SimpleNamespace(get=lambda _check_id: check),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.load_local_document_freshness_state",
        lambda _session, document_id=None, document_code=None: SimpleNamespace(
            document=document,
            current_version=version,
            current_source=None,
        ),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.fetch_current_document_metadata",
        lambda _session, document_code: _metadata(document=document, current_version=version),
    )

    result = evaluate_freshness_check(session, freshness_check_id=check_id)

    assert result.check_status == FreshnessCheckStatus.FRESH
    assert result.freshness_status == FreshnessStatus.FRESH
    assert check.remote_edition_label == version.edition_label


def test_evaluate_freshness_check_queues_refresh_for_stale_document(monkeypatch) -> None:
    session = MagicMock()
    check_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 1",
        display_code="SP 1",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=version_id,
    )
    version = DocumentVersion(id=version_id, document_id=document_id, edition_label="2023", is_active=True)
    check = FreshnessCheck(id=check_id, query_id=uuid4(), document_id=document_id, document_version_id=version_id)
    job = IngestionJob(id=uuid4(), job_type=JobType.REFRESH_DOCUMENT, payload={"document_code": "SP 1"}, status=JobStatus.PENDING)

    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.FreshnessCheckRepository",
        lambda _session: SimpleNamespace(get=lambda _check_id: check),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.load_local_document_freshness_state",
        lambda _session, document_id=None, document_code=None: SimpleNamespace(
            document=document,
            current_version=version,
            current_source=None,
        ),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.fetch_current_document_metadata",
        lambda _session, document_code: _metadata(
            document=document,
            current_version=version,
            remote_edition_label="2024",
            remote_text_actualized_at=date(2025, 1, 1),
        ),
    )
    monkeypatch.setattr("qanorm.services.qa.freshness_service.create_job", lambda repository, **kwargs: job)

    result = evaluate_freshness_check(session, freshness_check_id=check_id, auto_queue_refresh=True)

    assert result.check_status == FreshnessCheckStatus.REFRESH_IN_PROGRESS
    assert result.refresh_job_id == job.id
    assert check.refresh_job_id == job.id


def test_evaluate_freshness_check_marks_refresh_failed_when_latest_refresh_failed(monkeypatch) -> None:
    session = MagicMock()
    check_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 1",
        display_code="SP 1",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=version_id,
    )
    version = DocumentVersion(id=version_id, document_id=document_id, edition_label="2023", is_active=True)
    check = FreshnessCheck(id=check_id, query_id=uuid4(), document_id=document_id, document_version_id=version_id)

    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.FreshnessCheckRepository",
        lambda _session: SimpleNamespace(get=lambda _check_id: check),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.load_local_document_freshness_state",
        lambda _session, document_id=None, document_code=None: SimpleNamespace(
            document=document,
            current_version=version,
            current_source=None,
        ),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.fetch_current_document_metadata",
        lambda _session, document_code: _metadata(
            document=document,
            current_version=version,
            remote_edition_label="2024",
            remote_text_actualized_at=date(2025, 1, 1),
        ),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service._load_latest_update_event",
        lambda _session, document_id: UpdateEvent(document_id=document_id, status="refresh_failed"),
    )

    result = evaluate_freshness_check(session, freshness_check_id=check_id)

    assert result.check_status == FreshnessCheckStatus.REFRESH_FAILED
    assert result.freshness_status == FreshnessStatus.REFRESH_FAILED


def test_stage2_freshness_jobs_wrap_service_calls(monkeypatch) -> None:
    check_id = uuid4()
    fake_session = object()

    class _FakeResult:
        def __init__(self, status: str) -> None:
            self._status = status

        def to_payload(self) -> dict[str, str]:
            return {"status": self._status}

    @contextmanager
    def _fake_scope():
        yield fake_session

    monkeypatch.setattr(
        "qanorm.workers.stage2.evaluate_freshness_check",
        lambda session, freshness_check_id, auto_queue_refresh: _FakeResult("fresh"),
    )
    monkeypatch.setattr(
        "qanorm.workers.stage2.queue_refresh_for_freshness_check",
        lambda session, freshness_check_id: _FakeResult("refresh_in_progress"),
    )
    monkeypatch.setattr("qanorm.workers.stage2.session_scope", _fake_scope)

    freshness_payload = asyncio.run(freshness_check_job({}, {"freshness_check_id": str(check_id)}))
    refresh_payload = asyncio.run(document_refresh_job({}, {"freshness_check_id": str(check_id)}))

    assert freshness_payload["status"] == "fresh"
    assert refresh_payload["status"] == "refresh_in_progress"


def test_connect_freshness_branch_schedules_checks_without_blocking(monkeypatch) -> None:
    session = MagicMock()
    query = QAQuery(id=uuid4(), session_id=uuid4(), message_id=uuid4(), query_text="test")
    query.requires_freshness_check = True
    check = FreshnessCheck(id=uuid4(), query_id=query.id, document_id=uuid4())
    evidence = QAEvidence(query_id=query.id, source_kind=EvidenceSourceKind.NORMATIVE, document_id=check.document_id)
    scheduled_ids: list[str] = []

    monkeypatch.setattr(
        "qanorm.services.qa.freshness_service.schedule_freshness_checks",
        lambda _session, **kwargs: [check],
    )

    async def _scheduler(item):
        scheduled_ids.append(str(item.id))
        return {"status": "queued"}

    result = asyncio.run(
        connect_freshness_branch(
            session,
            query=query,
            evidence_rows=[evidence],
            scheduler=_scheduler,
        )
    )

    assert result == [check]
    assert scheduled_ids == [str(check.id)]


def test_annotate_answer_with_freshness_adds_warning_block() -> None:
    answer = StructuredAnswer(
        answer_text="Base answer",
        markdown="## Answer\n\nBase answer",
        answer_format="markdown",
        answer_mode=AnswerMode.PARTIAL_ANSWER,
        coverage_status=SimpleNamespace(value="partial"),
        has_stale_sources=False,
        has_external_sources=False,
        assumptions=[],
        limitations=[],
        warnings=[],
        sections=[],
        model_name="test-model",
    )
    result = annotate_answer_with_freshness(
        answer,
        checks=[
            FreshnessCheck(
                id=uuid4(),
                query_id=uuid4(),
                document_id=uuid4(),
                local_edition_label="2023",
                remote_edition_label="2024",
                check_status=FreshnessCheckStatus.STALE,
                details_json={"document_code": "SP 1"},
            )
        ],
    )

    assert result.has_stale_sources is True
    assert "### Freshness" in result.markdown
    assert "2023" in result.markdown
    assert "2024" in result.markdown


def test_build_freshness_warning_messages_mentions_local_and_remote_editions() -> None:
    warnings = build_freshness_warning_messages(
        [
            FreshnessCheck(
                id=uuid4(),
                query_id=uuid4(),
                document_id=uuid4(),
                local_edition_label="2023",
                remote_edition_label="2024",
                check_status=FreshnessCheckStatus.REFRESH_IN_PROGRESS,
                details_json={"document_code": "SP 35.13330.2011"},
            )
        ]
    )

    assert len(warnings) == 1
    assert "SP 35.13330.2011" in warnings[0]
    assert "2023" in warnings[0]
    assert "2024" in warnings[0]


def test_enrich_persisted_answer_with_freshness_updates_answer_and_message(monkeypatch) -> None:
    query = QAQuery(id=uuid4(), session_id=uuid4(), message_id=uuid4(), query_text="test")
    answer_row = SimpleNamespace(answer_text="## Answer", has_stale_sources=False)
    message_row = QAMessage(session_id=query.session_id, role="assistant", content="## Answer", metadata_json={})
    check = FreshnessCheck(
        id=uuid4(),
        query_id=query.id,
        document_id=uuid4(),
        local_edition_label="2023",
        remote_edition_label="2024",
        check_status=FreshnessCheckStatus.STALE,
        details_json={"document_code": "SP 1"},
    )

    class _FakeSession:
        def __init__(self) -> None:
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        def flush(self):
            return None

        def get(self, model, value):
            return query

    class _AnswerRepository:
        def __init__(self, _session) -> None:
            self.saved = None

        def get_by_query(self, query_id):
            return answer_row

        def save(self, answer):
            self.saved = answer
            return answer

    class _MessageRepository:
        def __init__(self, _session) -> None:
            self.saved = None

        def get_latest_assistant_for_session(self, session_id):
            return message_row

        def save(self, message):
            self.saved = message
            return message

    class _CheckRepository:
        def __init__(self, _session) -> None:
            return None

        def list_for_query(self, query_id):
            return [check]

    monkeypatch.setattr("qanorm.services.qa.freshness_service.QAAnswerRepository", _AnswerRepository)
    monkeypatch.setattr("qanorm.services.qa.freshness_service.QAMessageRepository", _MessageRepository)
    monkeypatch.setattr("qanorm.services.qa.freshness_service.FreshnessCheckRepository", _CheckRepository)

    result = enrich_persisted_answer_with_freshness(_FakeSession(), query_id=query.id)

    assert result["status"] == "ok"
    assert "### Freshness" in answer_row.answer_text
    assert message_row.metadata_json["has_stale_sources"] is True


def test_post_answer_enrichment_job_wraps_service_call(monkeypatch) -> None:
    query_id = uuid4()
    fake_session = object()

    @contextmanager
    def _fake_scope():
        yield fake_session

    monkeypatch.setattr(
        "qanorm.workers.stage2.enrich_persisted_answer_with_freshness",
        lambda session, query_id: {"status": "ok", "query_id": str(query_id)},
    )
    monkeypatch.setattr("qanorm.workers.stage2.session_scope", _fake_scope)

    payload = asyncio.run(post_answer_enrichment_job({}, {"query_id": str(query_id)}))

    assert payload["status"] == "ok"


def _metadata(
    *,
    document: Document,
    current_version: DocumentVersion,
    remote_edition_label: str | None = None,
    remote_text_actualized_at: date | None = None,
) -> SimpleNamespace:
    current_source = DocumentSource(id=uuid4(), document_id=document.id, document_version_id=current_version.id, card_url="https://example.test/card")
    card_data = DocumentCardData(
        card_url=current_source.card_url,
        source_type="index_card",
        source_list_status_raw="действует",
        card_status_raw="действует",
        document_code=document.normalized_code,
        document_title=document.title or document.display_code,
        text_actualized_at=remote_text_actualized_at,
        description_actualized_at=remote_text_actualized_at,
        published_at=None,
        effective_from=None,
        scope_text=None,
        normative_references=[],
        pdf_url=None,
        html_url=current_source.card_url,
        print_url=None,
        has_full_html=True,
        has_page_images=False,
        edition_label=remote_edition_label or current_version.edition_label,
    )
    return SimpleNamespace(
        document=document,
        current_version=current_version,
        current_source=current_source,
        card_data=card_data,
        source_status_normalized=StatusNormalized.ACTIVE,
        source_status_raw="действует",
    )
