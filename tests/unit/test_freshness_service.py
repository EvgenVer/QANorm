from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import EvidenceSourceKind, FreshnessCheckStatus, FreshnessStatus, JobStatus, JobType, StatusNormalized
from qanorm.models import Document, DocumentSource, DocumentVersion, FreshnessCheck, IngestionJob, QAEvidence, UpdateEvent
from qanorm.parsers.card_parser import DocumentCardData
from qanorm.services.qa.freshness_service import (
    evaluate_freshness_check,
    load_local_document_freshness_state,
    schedule_freshness_checks,
    should_run_freshness_check,
)
from qanorm.workers.stage2 import document_refresh_job, freshness_check_job


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
