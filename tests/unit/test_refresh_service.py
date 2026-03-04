from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from qanorm.cli.main import build_parser
from qanorm.db.types import ProcessingStatus, StatusNormalized
from qanorm.models import Document, DocumentSource, DocumentVersion, UpdateEvent
from qanorm.parsers.card_parser import DocumentCardData
from qanorm.services.document_pipeline import DownloadArtifactsResult, ExtractedTextResult, StructureNormalizationPipelineResult
from qanorm.services.refresh_service import (
    CurrentSourceMetadata,
    determine_refresh_requirement,
    fetch_current_document_metadata,
    process_refresh_document_job,
    run_document_refresh,
)


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


def _card_data(
    *,
    text_actualized_at: date | None = None,
    description_actualized_at: date | None = None,
    card_status_raw: str | None = "действует",
) -> DocumentCardData:
    return DocumentCardData(
        card_url="https://example.test/card",
        source_type="index_card",
        source_list_status_raw="действует",
        card_status_raw=card_status_raw,
        document_code="SP 1.0",
        document_title="Test title",
        text_actualized_at=text_actualized_at,
        description_actualized_at=description_actualized_at,
        published_at=None,
        effective_from=None,
        scope_text=None,
        normative_references=[],
        pdf_url="https://example.test/doc.pdf",
        html_url="https://example.test/doc.html",
        print_url=None,
        has_full_html=True,
        has_page_images=False,
        edition_label="edition",
    )


def _metadata(
    *,
    document_status: StatusNormalized = StatusNormalized.ACTIVE,
    source_status: StatusNormalized = StatusNormalized.ACTIVE,
    current_text_actualized_at: date | None = None,
    source_text_actualized_at: date | None = None,
    current_description_actualized_at: date | None = None,
    source_description_actualized_at: date | None = None,
) -> CurrentSourceMetadata:
    document_id = uuid4()
    version_id = uuid4()
    source_id = uuid4()
    document = Document(
        id=document_id,
        normalized_code="SP 1.0",
        display_code="SP 1.0",
        status_normalized=document_status,
        current_version_id=version_id,
    )
    current_version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        text_actualized_at=current_text_actualized_at,
        description_actualized_at=current_description_actualized_at,
        source_status_raw="действует",
        is_active=True,
    )
    current_source = DocumentSource(
        id=source_id,
        document_id=document_id,
        document_version_id=version_id,
        card_url="https://example.test/card",
        html_url="https://example.test/doc.html",
        pdf_url="https://example.test/doc.pdf",
        seed_url="https://example.test/seed",
        list_page_url="https://example.test/list",
        source_type="index_card",
    )
    return CurrentSourceMetadata(
        document=document,
        current_version=current_version,
        current_source=current_source,
        card_data=_card_data(
            text_actualized_at=source_text_actualized_at,
            description_actualized_at=source_description_actualized_at,
            card_status_raw="действует" if source_status is StatusNormalized.ACTIVE else "утратил силу",
        ),
        source_status_normalized=source_status,
        source_status_raw="действует" if source_status is StatusNormalized.ACTIVE else "утратил силу",
    )


def test_fetch_current_document_metadata_loads_latest_source_card() -> None:
    document_id = uuid4()
    version_id = uuid4()
    older_source = DocumentSource(
        id=uuid4(),
        document_id=document_id,
        document_version_id=version_id,
        card_url="https://example.test/card/old",
    )
    latest_source = DocumentSource(
        id=uuid4(),
        document_id=document_id,
        document_version_id=version_id,
        card_url="https://example.test/card/latest",
    )
    document = Document(
        id=document_id,
        normalized_code="SP 1.0",
        display_code="SP 1.0",
        status_normalized=StatusNormalized.ACTIVE,
        current_version_id=version_id,
    )
    current_version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        source_status_raw="действует",
        is_active=True,
    )
    parsed_card = _card_data(text_actualized_at=date(2025, 1, 1))

    session = _mock_session()
    session.execute.side_effect = [
        _ScalarOneResult(document),
        _ScalarListResult([older_source, latest_source]),
    ]
    session.get.return_value = current_version

    with patch("qanorm.services.refresh_service.fetch_document_card", return_value="<html></html>") as fetch_mock:
        with patch("qanorm.services.refresh_service.parse_document_card", return_value=parsed_card):
            metadata = fetch_current_document_metadata(session, document_code="sp 1.0")

    assert metadata.document is document
    assert metadata.current_version is current_version
    assert metadata.current_source is latest_source
    assert metadata.card_data is parsed_card
    fetch_mock.assert_called_once_with("https://example.test/card/latest")


def test_determine_refresh_requirement_detects_status_and_date_changes() -> None:
    metadata = _metadata(
        document_status=StatusNormalized.ACTIVE,
        source_status=StatusNormalized.INACTIVE,
        current_text_actualized_at=date(2024, 1, 1),
        source_text_actualized_at=date(2025, 1, 1),
        current_description_actualized_at=date(2024, 1, 1),
        source_description_actualized_at=date(2025, 2, 1),
    )

    result = determine_refresh_requirement(metadata)

    assert result.needs_refresh is True
    assert result.status_changed is True
    assert result.text_actualized_changed is True
    assert result.description_actualized_changed is True
    assert result.reasons == [
        "status_changed",
        "text_actualized_changed",
        "description_actualized_changed",
    ]


def test_process_refresh_document_job_skips_up_to_date_document() -> None:
    metadata = _metadata(
        current_text_actualized_at=date(2025, 1, 1),
        source_text_actualized_at=date(2025, 1, 1),
        current_description_actualized_at=date(2025, 1, 1),
        source_description_actualized_at=date(2025, 1, 1),
    )
    session = _mock_session()

    with patch("qanorm.services.refresh_service.fetch_current_document_metadata", return_value=metadata):
        result = process_refresh_document_job("SP 1.0", session=session)

    assert result.status == "skipped_up_to_date"
    assert result.needs_refresh is False
    session.add.assert_called_once()
    event = session.add.call_args.args[0]
    assert isinstance(event, UpdateEvent)
    assert event.status == "skipped_up_to_date"
    assert event.old_version_id == metadata.current_version.id


def test_process_refresh_document_job_reprocesses_document_when_source_is_newer() -> None:
    metadata = _metadata(
        current_text_actualized_at=date(2024, 1, 1),
        source_text_actualized_at=date(2025, 1, 1),
    )
    new_version_id = uuid4()
    session = _mock_session()

    with patch("qanorm.services.refresh_service.fetch_current_document_metadata", return_value=metadata):
        with patch(
            "qanorm.services.refresh_service.persist_document_card",
            return_value=SimpleNamespace(document_version_id=str(new_version_id)),
        ):
            with patch(
                "qanorm.services.refresh_service.download_document_artifacts",
                return_value=DownloadArtifactsResult("ok", [], 0, False, False, None),
            ):
                with patch(
                    "qanorm.services.refresh_service.extract_document_text",
                    return_value=ExtractedTextResult("ok", "html", 100, False, 1, None),
                ):
                    with patch(
                        "qanorm.services.refresh_service.normalize_document_structure",
                        return_value=StructureNormalizationPipelineResult("ok", "html_raw", 4, 0, "hash", False, None),
                    ):
                        with patch(
                            "qanorm.services.refresh_service.index_document_version",
                            return_value=SimpleNamespace(status="ok"),
                        ):
                            result = process_refresh_document_job("SP 1.0", session=session)

    assert result.status == "refresh_completed"
    assert result.needs_refresh is True
    assert result.new_version_id == str(new_version_id)
    assert result.details["download_status"] == "ok"
    assert result.details["index_status"] == "ok"
    event = session.add.call_args.args[0]
    assert isinstance(event, UpdateEvent)
    assert event.status == "refresh_completed"
    assert event.new_version_id == new_version_id


def test_process_refresh_document_job_keeps_current_version_on_failure() -> None:
    metadata = _metadata(
        current_text_actualized_at=date(2024, 1, 1),
        source_text_actualized_at=date(2025, 1, 1),
    )
    new_version_id = uuid4()
    failed_version = DocumentVersion(id=new_version_id, document_id=metadata.document.id)
    session = _mock_session()
    session.get.return_value = failed_version

    def fail_after_activation(*args, **kwargs) -> StructureNormalizationPipelineResult:
        metadata.current_version.is_active = False
        metadata.current_version.is_outdated = True
        return StructureNormalizationPipelineResult("ok", "html_raw", 4, 0, "hash", False, None)

    with patch("qanorm.services.refresh_service.fetch_current_document_metadata", return_value=metadata):
        with patch(
            "qanorm.services.refresh_service.persist_document_card",
            return_value=SimpleNamespace(document_version_id=str(new_version_id)),
        ):
            with patch(
                "qanorm.services.refresh_service.download_document_artifacts",
                return_value=DownloadArtifactsResult("ok", [], 0, False, False, None),
            ):
                with patch(
                    "qanorm.services.refresh_service.extract_document_text",
                    return_value=ExtractedTextResult("ok", "html", 100, False, 1, None),
                ):
                    with patch(
                        "qanorm.services.refresh_service.normalize_document_structure",
                        side_effect=fail_after_activation,
                    ):
                        with patch(
                            "qanorm.services.refresh_service.index_document_version",
                            side_effect=RuntimeError("index failed"),
                        ):
                            with pytest.raises(RuntimeError, match="index failed"):
                                process_refresh_document_job("SP 1.0", session=session)

    assert metadata.document.current_version_id == metadata.current_version.id
    assert metadata.current_version.is_active is True
    assert metadata.current_version.is_outdated is False
    assert failed_version.processing_status is ProcessingStatus.FAILED
    event = session.add.call_args.args[0]
    assert isinstance(event, UpdateEvent)
    assert event.status == "refresh_failed"
    assert event.old_version_id == metadata.current_version.id
    assert event.new_version_id == new_version_id


def test_build_parser_supports_update_document_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["update-document", "SP 1.0"])

    assert args.command == "update-document"
    assert args.document_code == "SP 1.0"


def test_run_document_refresh_returns_failure_payload_for_cli() -> None:
    with patch("qanorm.services.refresh_service.process_refresh_document_job", side_effect=ValueError("boom")):
        result = run_document_refresh("SP 1.0")

    assert result["status"] == "failed"
    assert result["document_code"] == "SP 1.0"
    assert result["details"]["error"] == "boom"


def test_process_refresh_document_job_smoke_updates_test_document() -> None:
    metadata = _metadata(
        current_text_actualized_at=date(2024, 1, 1),
        source_text_actualized_at=date(2025, 1, 1),
    )
    new_version_id = uuid4()
    session = _mock_session()

    with patch("qanorm.services.refresh_service.fetch_current_document_metadata", return_value=metadata):
        with patch(
            "qanorm.services.refresh_service.persist_document_card",
            return_value=SimpleNamespace(document_version_id=str(new_version_id)),
        ):
            with patch(
                "qanorm.services.refresh_service.download_document_artifacts",
                return_value=DownloadArtifactsResult("ok", [], 0, False, False, None),
            ):
                with patch(
                    "qanorm.services.refresh_service.extract_document_text",
                    return_value=ExtractedTextResult("ok", "html", 100, False, 1, None),
                ):
                    with patch(
                        "qanorm.services.refresh_service.normalize_document_structure",
                        return_value=StructureNormalizationPipelineResult("ok", "html_raw", 4, 0, "hash", False, None),
                    ):
                        with patch(
                            "qanorm.services.refresh_service.index_document_version",
                            return_value=SimpleNamespace(status="ok"),
                        ):
                            result = process_refresh_document_job("SP 1.0", session=session)

    assert result.status == "refresh_completed"
    assert result.new_version_id == str(new_version_id)
