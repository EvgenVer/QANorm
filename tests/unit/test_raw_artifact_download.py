from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import ArtifactType, JobType, ProcessingStatus
from qanorm.models import DocumentVersion, IngestionJob, RawArtifact
from qanorm.services.document_pipeline import download_document_artifacts
from qanorm.storage.raw_store import RawFileStore


def _mock_session() -> MagicMock:
    return MagicMock()


def _session_with_execute_side_effect(*values: object) -> MagicMock:
    session = _mock_session()
    session.execute.return_value.scalar_one_or_none.side_effect = list(values)
    return session


def test_download_document_artifacts_saves_html_and_pdf_and_queues_extract_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    version = DocumentVersion(id=uuid4(), document_id=uuid4())
    version.processing_status = ProcessingStatus.PENDING
    session = _session_with_execute_side_effect(None, None, None)
    session.get.return_value = version

    monkeypatch.setattr(
        "qanorm.services.document_pipeline.fetch_html_document",
        lambda url: "<html><body>full html</body></html>",
    )
    monkeypatch.setattr(
        "qanorm.services.document_pipeline.fetch_pdf_bytes",
        lambda url: b"%PDF-1.7",
    )

    result = download_document_artifacts(
        session,
        document_version_id=version.id,
        document_code="Federal Law 3-FZ",
        card_url="https://example.test/card",
        html_url="https://example.test/doc.htm",
        pdf_url="https://example.test/doc.pdf",
        print_url=None,
        has_full_html=True,
        has_page_images=False,
        raw_store=RawFileStore(base_path=tmp_path),
    )

    assert result.saved_artifact_count == 2
    assert result.html_missing is False
    assert result.pdf_missing is False
    assert version.processing_status is ProcessingStatus.DOWNLOADED
    assert session.add.call_count == 3
    added_instances = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(added_instances[0], RawArtifact)
    assert added_instances[0].artifact_type is ArtifactType.HTML_RAW
    assert isinstance(added_instances[1], RawArtifact)
    assert added_instances[1].artifact_type is ArtifactType.PDF_RAW
    assert isinstance(added_instances[2], IngestionJob)
    assert added_instances[2].job_type is JobType.EXTRACT_TEXT
    assert Path(added_instances[0].storage_path).exists() is True
    assert Path(added_instances[1].storage_path).exists() is True


def test_download_document_artifacts_uses_print_html_when_full_html_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    version = DocumentVersion(id=uuid4(), document_id=uuid4())
    session = _session_with_execute_side_effect(None, None)
    session.get.return_value = version

    monkeypatch.setattr(
        "qanorm.services.document_pipeline.fetch_html_document",
        lambda url: "<html><body>print html</body></html>",
    )

    result = download_document_artifacts(
        session,
        document_version_id=version.id,
        document_code="GOST R 1.0",
        card_url="https://example.test/card",
        html_url=None,
        pdf_url=None,
        print_url="https://example.test/print.htm",
        has_full_html=False,
        has_page_images=False,
        raw_store=RawFileStore(base_path=tmp_path),
    )

    assert result.saved_artifact_count == 1
    assert result.html_missing is False
    assert result.pdf_missing is True


def test_download_document_artifacts_downloads_page_images_as_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    version = DocumentVersion(id=uuid4(), document_id=uuid4())
    session = _session_with_execute_side_effect(None, None, None)
    session.get.return_value = version
    card_html = """
    <html><body>
      <img class="img2" src="/pages/0.gif"/>
      <img class="img2" src="/pages/1.gif"/>
    </body></html>
    """

    monkeypatch.setattr("qanorm.services.document_pipeline.fetch_document_card", lambda url: card_html)
    monkeypatch.setattr("qanorm.services.document_pipeline.fetch_image_bytes", lambda url: b"GIF89a")

    result = download_document_artifacts(
        session,
        document_version_id=version.id,
        document_code="Federal Law 3-FZ",
        card_url="https://example.test/card",
        html_url=None,
        pdf_url=None,
        print_url=None,
        has_full_html=False,
        has_page_images=True,
        raw_store=RawFileStore(base_path=tmp_path),
    )

    assert result.saved_artifact_count == 2
    added_instances = [call.args[0] for call in session.add.call_args_list]
    raw_artifacts = [item for item in added_instances if isinstance(item, RawArtifact)]
    assert len(raw_artifacts) == 2
    assert all(item.artifact_type is ArtifactType.PAGE_IMAGE for item in raw_artifacts)


def test_download_document_artifacts_skips_page_images_when_html_or_pdf_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    version = DocumentVersion(id=uuid4(), document_id=uuid4())
    session = _session_with_execute_side_effect(None, None, None)
    session.get.return_value = version

    monkeypatch.setattr(
        "qanorm.services.document_pipeline.fetch_html_document",
        lambda url: "<html><body>full html</body></html>",
    )
    monkeypatch.setattr(
        "qanorm.services.document_pipeline.fetch_pdf_bytes",
        lambda url: b"%PDF-1.7",
    )

    fetch_card_mock = MagicMock(return_value="<html></html>")
    monkeypatch.setattr("qanorm.services.document_pipeline.fetch_document_card", fetch_card_mock)
    monkeypatch.setattr("qanorm.services.document_pipeline.fetch_image_bytes", lambda url: b"GIF89a")

    result = download_document_artifacts(
        session,
        document_version_id=version.id,
        document_code="Federal Law 3-FZ",
        card_url="https://example.test/card",
        html_url="https://example.test/doc.htm",
        pdf_url="https://example.test/doc.pdf",
        print_url=None,
        has_full_html=True,
        has_page_images=True,
        raw_store=RawFileStore(base_path=tmp_path),
    )

    assert result.saved_artifact_count == 2
    assert fetch_card_mock.call_count == 0
    added_instances = [call.args[0] for call in session.add.call_args_list]
    raw_artifacts = [item for item in added_instances if isinstance(item, RawArtifact)]
    assert all(item.artifact_type is not ArtifactType.PAGE_IMAGE for item in raw_artifacts)


def test_download_document_artifacts_skips_existing_artifact_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    version = DocumentVersion(id=uuid4(), document_id=uuid4())
    existing_artifact = RawArtifact(
        document_version_id=version.id,
        artifact_type=ArtifactType.HTML_RAW,
        storage_path=str(tmp_path / "existing.html"),
        relative_path="already/there.html",
        checksum_sha256="0" * 64,
    )
    session = _session_with_execute_side_effect(existing_artifact, None)
    session.get.return_value = version

    monkeypatch.setattr(
        "qanorm.services.document_pipeline.fetch_html_document",
        lambda url: "<html>should not be used</html>",
    )

    result = download_document_artifacts(
        session,
        document_version_id=version.id,
        document_code="GOST R 1.0",
        card_url="https://example.test/card",
        html_url="https://example.test/doc.htm",
        pdf_url=None,
        print_url=None,
        has_full_html=True,
        has_page_images=False,
        raw_store=RawFileStore(base_path=tmp_path),
    )

    assert result.saved_artifact_count == 0
    assert session.add.call_count == 1
    added_instance = session.add.call_args.args[0]
    assert isinstance(added_instance, IngestionJob)
