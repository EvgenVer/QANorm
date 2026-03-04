from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from qanorm.cli.main import build_parser, main
from qanorm.db.types import ArtifactType, JobStatus, JobType, ProcessingStatus, StatusNormalized
from qanorm.models import Document, DocumentSource, DocumentVersion, IngestionJob, RawArtifact, UpdateEvent
from qanorm.services.metrics import (
    IngestionMetrics,
    build_ingestion_test_run_report,
    build_stage1_readiness_checklist,
    collect_ingestion_metrics,
    compare_metrics_to_mvp_targets,
    get_ingestion_metrics,
    get_ingestion_test_run_report,
)
from tests.integration.support import FakeSession, patched_in_memory_repositories


def _settings(threshold: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(ocr_low_confidence_threshold=threshold))


def _add_artifact(
    session: FakeSession,
    *,
    version_id,
    artifact_type: ArtifactType,
    relative_path: str,
) -> None:
    session.store.artifacts.append(
        RawArtifact(
            document_version_id=version_id,
            artifact_type=artifact_type,
            storage_path=f"raw/{relative_path}",
            relative_path=relative_path,
            mime_type="text/plain",
            file_size=10,
            checksum_sha256="a" * 64,
        )
    )


def _build_ready_sample_session() -> FakeSession:
    session = FakeSession()
    document_id = uuid4()
    version_id = uuid4()

    session.store.documents.append(
        Document(
            id=document_id,
            normalized_code="SP 20.13330.2016",
            display_code="SP 20.13330.2016",
            status_normalized=StatusNormalized.ACTIVE,
            current_version_id=version_id,
        )
    )
    session.store.versions.append(
        DocumentVersion(
            id=version_id,
            document_id=document_id,
            is_active=True,
            processing_status=ProcessingStatus.INDEXED,
        )
    )
    session.store.sources.append(
        DocumentSource(
            document_id=document_id,
            document_version_id=version_id,
            seed_url="https://example.test/seeds/1",
            list_page_url="https://example.test/list/1",
            card_url="https://example.test/card/1",
            html_url="https://example.test/html/1",
            pdf_url=None,
            print_url=None,
            source_type="html",
        )
    )
    _add_artifact(
        session,
        version_id=version_id,
        artifact_type=ArtifactType.HTML_RAW,
        relative_path="sample.html",
    )
    _add_artifact(
        session,
        version_id=version_id,
        artifact_type=ArtifactType.PARSED_TEXT_SNAPSHOT,
        relative_path="sample.txt",
    )
    session.store.jobs.extend(
        [
            IngestionJob(
                job_type=JobType.PARSE_LIST_PAGE,
                payload={"list_page_url": "https://example.test/list/1"},
                status=JobStatus.COMPLETED,
            ),
            IngestionJob(
                job_type=JobType.PROCESS_DOCUMENT_CARD,
                payload={"card_url": "https://example.test/card/1"},
                status=JobStatus.COMPLETED,
            ),
        ]
    )
    session.store.events.append(
        UpdateEvent(
            document_id=document_id,
            old_version_id=version_id,
            new_version_id=version_id,
            update_reason="new_content_hash",
            status="refresh_completed",
            details={"reason": "sample"},
        )
    )
    return session


def _make_metrics(**overrides: float | int | str) -> IngestionMetrics:
    base: dict[str, float | int | str] = {
        "status": "ok",
        "list_pages_processed_successfully": 10,
        "list_pages_total": 10,
        "list_pages_success_rate": 1.0,
        "document_cards_discovered": 8,
        "documents_total": 8,
        "documents_active": 5,
        "documents_inactive": 2,
        "documents_unknown": 1,
        "documents_with_raw_artifacts": 7,
        "documents_with_extracted_text": 6,
        "documents_with_ocr": 1,
        "documents_with_low_confidence_parse": 0,
        "documents_structured": 5,
        "documents_indexed": 5,
        "updates_detected": 2,
        "updates_successful": 2,
        "updates_failed": 0,
        "active_documents_reaching_card_rate": 1.0,
        "active_documents_with_raw_rate": 1.0,
        "active_documents_with_text_rate": 1.0,
        "active_documents_structured_rate": 1.0,
        "active_index_documents_total": 5,
        "active_index_documents_with_correct_status": 5,
        "active_index_documents_with_correct_status_rate": 1.0,
        "inactive_documents_in_active_index": 0,
        "active_documents_without_active_version": 0,
        "active_versions_without_text_source": 0,
    }
    base.update(overrides)
    return IngestionMetrics(**base)


def test_collect_ingestion_metrics_aggregates_stage1_counts() -> None:
    session = FakeSession()
    active_ready_document_id = uuid4()
    active_ready_version_id = uuid4()
    active_ocr_document_id = uuid4()
    active_ocr_version_id = uuid4()
    active_missing_document_id = uuid4()
    inactive_document_id = uuid4()
    inactive_version_id = uuid4()
    unknown_document_id = uuid4()

    session.store.documents.extend(
        [
            Document(
                id=active_ready_document_id,
                normalized_code="SP 1.0",
                display_code="SP 1.0",
                status_normalized=StatusNormalized.ACTIVE,
                current_version_id=active_ready_version_id,
            ),
            Document(
                id=active_ocr_document_id,
                normalized_code="SP 2.0",
                display_code="SP 2.0",
                status_normalized=StatusNormalized.ACTIVE,
                current_version_id=active_ocr_version_id,
            ),
            Document(
                id=active_missing_document_id,
                normalized_code="SP 3.0",
                display_code="SP 3.0",
                status_normalized=StatusNormalized.ACTIVE,
                current_version_id=None,
            ),
            Document(
                id=inactive_document_id,
                normalized_code="SP 4.0",
                display_code="SP 4.0",
                status_normalized=StatusNormalized.INACTIVE,
                current_version_id=inactive_version_id,
            ),
            Document(
                id=unknown_document_id,
                normalized_code="SP 5.0",
                display_code="SP 5.0",
                status_normalized=StatusNormalized.UNKNOWN,
                current_version_id=None,
            ),
        ]
    )
    session.store.versions.extend(
        [
            DocumentVersion(
                id=active_ready_version_id,
                document_id=active_ready_document_id,
                is_active=True,
                processing_status=ProcessingStatus.INDEXED,
            ),
            DocumentVersion(
                id=active_ocr_version_id,
                document_id=active_ocr_document_id,
                is_active=True,
                has_ocr=True,
                parse_confidence=0.4,
                processing_status=ProcessingStatus.NORMALIZED,
            ),
            DocumentVersion(
                id=inactive_version_id,
                document_id=inactive_document_id,
                is_active=True,
                processing_status=ProcessingStatus.INDEXED,
            ),
        ]
    )
    session.store.sources.extend(
        [
            DocumentSource(
                document_id=active_ready_document_id,
                document_version_id=active_ready_version_id,
                seed_url="https://example.test/seeds/1",
                list_page_url="https://example.test/list/1",
                card_url="https://example.test/card/1",
                html_url="https://example.test/html/1",
                pdf_url=None,
                print_url=None,
                source_type="html",
            ),
            DocumentSource(
                document_id=active_ocr_document_id,
                document_version_id=active_ocr_version_id,
                seed_url="https://example.test/seeds/1",
                list_page_url="https://example.test/list/1",
                card_url="https://example.test/card/2",
                html_url=None,
                pdf_url="https://example.test/pdf/2",
                print_url=None,
                source_type="pdf",
            ),
            DocumentSource(
                document_id=inactive_document_id,
                document_version_id=inactive_version_id,
                seed_url="https://example.test/seeds/2",
                list_page_url="https://example.test/list/2",
                card_url="https://example.test/card/3",
                html_url="https://example.test/html/3",
                pdf_url=None,
                print_url=None,
                source_type="html",
            ),
        ]
    )
    _add_artifact(
        session,
        version_id=active_ready_version_id,
        artifact_type=ArtifactType.HTML_RAW,
        relative_path="ready.html",
    )
    _add_artifact(
        session,
        version_id=active_ready_version_id,
        artifact_type=ArtifactType.PARSED_TEXT_SNAPSHOT,
        relative_path="ready.txt",
    )
    _add_artifact(
        session,
        version_id=active_ocr_version_id,
        artifact_type=ArtifactType.PDF_RAW,
        relative_path="ocr.pdf",
    )
    _add_artifact(
        session,
        version_id=active_ocr_version_id,
        artifact_type=ArtifactType.OCR_RAW,
        relative_path="ocr.txt",
    )
    _add_artifact(
        session,
        version_id=inactive_version_id,
        artifact_type=ArtifactType.HTML_RAW,
        relative_path="inactive.html",
    )
    _add_artifact(
        session,
        version_id=inactive_version_id,
        artifact_type=ArtifactType.PARSED_TEXT_SNAPSHOT,
        relative_path="inactive.txt",
    )
    session.store.jobs.extend(
        [
            IngestionJob(
                job_type=JobType.PARSE_LIST_PAGE,
                payload={"list_page_url": "https://example.test/list/1"},
                status=JobStatus.FAILED,
            ),
            IngestionJob(
                job_type=JobType.PARSE_LIST_PAGE,
                payload={"list_page_url": "https://example.test/list/1"},
                status=JobStatus.COMPLETED,
            ),
            IngestionJob(
                job_type=JobType.PARSE_LIST_PAGE,
                payload={"list_page_url": "https://example.test/list/2"},
                status=JobStatus.FAILED,
            ),
            IngestionJob(
                job_type=JobType.PROCESS_DOCUMENT_CARD,
                payload={"card_url": "https://example.test/card/1"},
                status=JobStatus.COMPLETED,
            ),
            IngestionJob(
                job_type=JobType.PROCESS_DOCUMENT_CARD,
                payload={"card_url": "https://example.test/card/2"},
                status=JobStatus.PENDING,
            ),
            IngestionJob(
                job_type=JobType.PROCESS_DOCUMENT_CARD,
                payload={"card_url": "https://example.test/card/3"},
                status=JobStatus.COMPLETED,
            ),
            IngestionJob(
                job_type=JobType.PROCESS_DOCUMENT_CARD,
                payload={"card_url": "https://example.test/card/3"},
                status=JobStatus.FAILED,
            ),
        ]
    )
    session.store.events.extend(
        [
            UpdateEvent(
                document_id=active_ready_document_id,
                old_version_id=active_ready_version_id,
                new_version_id=active_ready_version_id,
                update_reason="new_content_hash",
                status="refresh_completed",
                details={},
            ),
            UpdateEvent(
                document_id=active_ready_document_id,
                old_version_id=active_ready_version_id,
                new_version_id=None,
                update_reason="metadata_unchanged",
                status="skipped_up_to_date",
                details={},
            ),
            UpdateEvent(
                document_id=active_ocr_document_id,
                old_version_id=active_ocr_version_id,
                new_version_id=None,
                update_reason="text_actualized_changed",
                status="refresh_failed",
                details={},
            ),
            UpdateEvent(
                document_id=active_ocr_document_id,
                old_version_id=active_ocr_version_id,
                new_version_id=active_ocr_version_id,
                update_reason="same_content_hash",
                status="skipped_duplicate",
                details={},
            ),
            UpdateEvent(
                document_id=inactive_document_id,
                old_version_id=None,
                new_version_id=inactive_version_id,
                update_reason="initial_activation",
                status="activated",
                details={},
            ),
        ]
    )

    with patched_in_memory_repositories(session):
        with patch("qanorm.services.metrics.get_settings", return_value=_settings()):
            metrics = collect_ingestion_metrics(session)

    assert metrics.list_pages_total == 2
    assert metrics.list_pages_processed_successfully == 1
    assert metrics.list_pages_success_rate == 0.5
    assert metrics.document_cards_discovered == 3
    assert metrics.documents_total == 5
    assert metrics.documents_active == 3
    assert metrics.documents_inactive == 1
    assert metrics.documents_unknown == 1
    assert metrics.documents_with_raw_artifacts == 3
    assert metrics.documents_with_extracted_text == 3
    assert metrics.documents_with_ocr == 1
    assert metrics.documents_with_low_confidence_parse == 1
    assert metrics.documents_structured == 3
    assert metrics.documents_indexed == 2
    assert metrics.updates_detected == 3
    assert metrics.updates_successful == 2
    assert metrics.updates_failed == 1
    assert metrics.active_documents_reaching_card_rate == 2 / 3
    assert metrics.active_documents_with_raw_rate == 2 / 3
    assert metrics.active_documents_with_text_rate == 2 / 3
    assert metrics.active_documents_structured_rate == 2 / 3
    assert metrics.active_index_documents_total == 2
    assert metrics.active_index_documents_with_correct_status == 1
    assert metrics.active_index_documents_with_correct_status_rate == 0.5
    assert metrics.inactive_documents_in_active_index == 1
    assert metrics.active_documents_without_active_version == 1
    assert metrics.active_versions_without_text_source == 0


def test_report_builders_compare_targets_and_signal_readiness() -> None:
    healthy_metrics = _make_metrics()
    degraded_metrics = _make_metrics(
        list_pages_success_rate=0.5,
        active_documents_with_text_rate=0.6,
        active_index_documents_with_correct_status_rate=0.5,
        inactive_documents_in_active_index=1,
        active_documents_without_active_version=1,
        active_versions_without_text_source=2,
    )

    healthy_comparison = compare_metrics_to_mvp_targets(healthy_metrics)
    degraded_comparison = compare_metrics_to_mvp_targets(degraded_metrics)
    healthy_readiness = build_stage1_readiness_checklist(healthy_metrics)
    degraded_readiness = build_stage1_readiness_checklist(degraded_metrics)

    assert healthy_comparison["passed"] is True
    assert degraded_comparison["passed"] is False
    assert degraded_comparison["checks"]["list_pages_success_rate"]["passed"] is False
    assert healthy_readiness["ready"] is True
    assert degraded_readiness["ready"] is False
    assert degraded_readiness["checks"]["metrics_target_comparison_passed"]["passed"] is False
    assert degraded_readiness["checks"]["inactive_documents_excluded_from_active_index"]["passed"] is False
    assert degraded_readiness["checks"]["active_documents_have_active_version"]["passed"] is False
    assert degraded_readiness["checks"]["active_versions_have_text_or_ocr"]["passed"] is False


def test_get_ingestion_wrappers_and_report_smoke_use_managed_session() -> None:
    session = _build_ready_sample_session()

    @contextmanager
    def fake_session_scope():
        yield session

    with patched_in_memory_repositories(session):
        with patch("qanorm.services.metrics.get_settings", return_value=_settings()):
            with patch("qanorm.services.metrics.session_scope", fake_session_scope):
                metrics_payload = get_ingestion_metrics()
                report_payload = get_ingestion_test_run_report()

    assert metrics_payload["documents_total"] == 1
    assert metrics_payload["documents_indexed"] == 1
    assert report_payload["target_comparison"]["passed"] is True
    assert report_payload["readiness_checklist"]["ready"] is True
    assert report_payload["summary"]["ready_for_stage_1_exit"] is True


def test_build_ingestion_test_run_report_contains_expected_sections() -> None:
    report = build_ingestion_test_run_report(_make_metrics(updates_failed=1))

    assert report["status"] == "ok"
    assert report["metrics"]["documents_total"] == 8
    assert report["target_comparison"]["passed"] is True
    assert report["readiness_checklist"]["ready"] is True
    assert report["summary"]["updates_failed"] == 1


def test_build_parser_supports_ingestion_metrics_and_report_commands() -> None:
    parser = build_parser()

    metrics_args = parser.parse_args(["ingestion-metrics"])
    report_args = parser.parse_args(["ingestion-report"])

    assert metrics_args.command == "ingestion-metrics"
    assert report_args.command == "ingestion-report"


def test_main_dispatches_ingestion_commands(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["qanorm", "ingestion-metrics"])
    with patch("qanorm.cli.main.get_ingestion_metrics", return_value={"status": "ok", "documents_total": 1}):
        main()
    metrics_output = capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["qanorm", "ingestion-report"])
    with patch(
        "qanorm.cli.main.get_ingestion_test_run_report",
        return_value={"status": "ok", "summary": {"ready_for_stage_1_exit": True}},
    ):
        main()
    report_output = capsys.readouterr().out

    assert '"documents_total": 1' in metrics_output
    assert '"ready_for_stage_1_exit": true' in report_output
