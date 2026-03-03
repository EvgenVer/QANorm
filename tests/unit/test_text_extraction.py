from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import fitz

from qanorm.db.types import ArtifactType, JobType, ProcessingStatus, StatusNormalized
from qanorm.models import Document, DocumentVersion, IngestionJob, RawArtifact
from qanorm.parsers.html_document_parser import extract_text_from_html_document
from qanorm.parsers.pdf_text_parser import extract_text_from_pdf
from qanorm.services.document_pipeline import extract_document_text
from qanorm.storage.raw_store import RawFileStore


def _mock_session() -> MagicMock:
    return MagicMock()


def _create_pdf(path: Path, page_texts: list[str]) -> None:
    document = fitz.open()
    try:
        for page_text in page_texts:
            page = document.new_page()
            if page_text:
                page.insert_text((72, 72), page_text)
        document.save(path)
    finally:
        document.close()


def test_extract_text_from_html_document_removes_navigation_and_service_blocks() -> None:
    html = """
    <html>
      <body>
        <header>Header</header>
        <div class="crumbs_span">Breadcrumbs</div>
        <div class="contener_doc">
          <h1>Title</h1>
          <script>console.log("hidden")</script>
          <p>Main paragraph.</p>
          <footer>Footer</footer>
        </div>
      </body>
    </html>
    """

    result = extract_text_from_html_document(html)

    assert "Header" not in result.text
    assert "Breadcrumbs" not in result.text
    assert "hidden" not in result.text
    assert "Title" in result.text
    assert "Main paragraph." in result.text


def test_extract_text_from_pdf_reads_pages_and_scores_text_layer(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _create_pdf(pdf_path, ["First page text", "Second page text"])

    result = extract_text_from_pdf(pdf_path)

    assert len(result.page_texts) == 2
    assert "First page text" in result.combined_text
    assert result.text_layer_score > 0
    assert result.needs_ocr is False


def test_extract_document_text_prefers_html_and_queues_normalize(tmp_path: Path) -> None:
    version_id = uuid4()
    document_id = uuid4()
    html_path = tmp_path / "raw" / "source.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(
        "<html><body><div class='contener_doc'><h1>Title</h1><p>"
        + ("Useful text " * 30)
        + "</p></div></body></html>",
        encoding="utf-8",
    )

    html_artifact = RawArtifact(
        document_version_id=version_id,
        artifact_type=ArtifactType.HTML_RAW,
        storage_path=str(html_path),
        relative_path="raw/source.html",
        checksum_sha256="0" * 64,
    )
    version = DocumentVersion(id=version_id, document_id=document_id)
    version.processing_status = ProcessingStatus.DOWNLOADED
    document = Document(
        id=document_id,
        normalized_code="FEDERAL LAW 3-FZ",
        display_code="Federal Law 3-FZ",
        status_normalized=StatusNormalized.ACTIVE,
    )

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.return_value.scalars.return_value.all.return_value = [html_artifact]
    session.execute.return_value.scalar_one_or_none.side_effect = [None, None]

    result = extract_document_text(
        session,
        document_version_id=version_id,
        raw_store=RawFileStore(base_path=tmp_path / "snapshots"),
    )

    assert result.chosen_source == "html"
    assert result.needs_ocr is False
    assert result.saved_snapshot_count == 1
    assert version.processing_status is ProcessingStatus.EXTRACTED
    assert session.add.call_count == 2
    added_instances = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(added_instances[0], RawArtifact)
    assert added_instances[0].artifact_type is ArtifactType.PARSED_TEXT_SNAPSHOT
    assert Path(added_instances[0].storage_path).exists() is True
    assert isinstance(added_instances[1], IngestionJob)
    assert added_instances[1].job_type is JobType.NORMALIZE_DOCUMENT


def test_extract_document_text_queues_run_ocr_for_low_quality_pdf(tmp_path: Path) -> None:
    version_id = uuid4()
    document_id = uuid4()
    pdf_path = tmp_path / "raw" / "source.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    _create_pdf(pdf_path, ["x"])

    pdf_artifact = RawArtifact(
        document_version_id=version_id,
        artifact_type=ArtifactType.PDF_RAW,
        storage_path=str(pdf_path),
        relative_path="raw/source.pdf",
        checksum_sha256="0" * 64,
    )
    version = DocumentVersion(id=version_id, document_id=document_id)
    version.processing_status = ProcessingStatus.DOWNLOADED
    document = Document(
        id=document_id,
        normalized_code="GOST R 1.0",
        display_code="GOST R 1.0",
        status_normalized=StatusNormalized.ACTIVE,
    )

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.return_value.scalars.return_value.all.return_value = [pdf_artifact]
    session.execute.return_value.scalar_one_or_none.side_effect = [None, None]

    result = extract_document_text(
        session,
        document_version_id=version_id,
        raw_store=RawFileStore(base_path=tmp_path / "snapshots"),
    )

    assert result.chosen_source == "pdf"
    assert result.needs_ocr is True
    assert result.saved_snapshot_count == 1
    added_instances = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(added_instances[-1], IngestionJob)
    assert added_instances[-1].job_type is JobType.RUN_OCR
