from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import fitz

from qanorm.db.types import ArtifactType, JobType, ProcessingStatus, StatusNormalized
from qanorm.models import Document, DocumentVersion, IngestionJob, RawArtifact
from qanorm.ocr.quality import calculate_ocr_confidence, is_low_confidence_parse
from qanorm.ocr.renderer import get_ocr_render_dpi, render_pdf_pages
from qanorm.ocr.tesseract import OcrPageResult, merge_ocr_page_texts, run_ocr_for_pages
from qanorm.services.document_pipeline import run_document_ocr
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


def test_get_ocr_render_dpi_uses_explicit_or_configured_value() -> None:
    assert get_ocr_render_dpi(240) == 240
    assert get_ocr_render_dpi() == 300


def test_render_pdf_pages_creates_png_images(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _create_pdf(pdf_path, ["First page", "Second page"])

    rendered_pages = render_pdf_pages(pdf_path, output_dir=tmp_path / "rendered", dpi=180)

    assert len(rendered_pages) == 2
    assert rendered_pages[0].dpi == 180
    assert rendered_pages[0].image_path.suffix == ".png"
    assert rendered_pages[0].image_path.exists() is True
    assert rendered_pages[1].image_path.exists() is True


def test_run_ocr_for_pages_and_merge_text_use_tesseract_in_page_order(tmp_path: Path) -> None:
    image_paths = [tmp_path / "page_0001.png", tmp_path / "page_0002.png"]
    for image_path in image_paths:
        image_path.write_bytes(b"not-used-by-mock")

    with patch("qanorm.ocr.tesseract.pytesseract.image_to_string") as image_to_string:
        image_to_string.side_effect = ["  First page  \n", "Second   page"]
        page_results = run_ocr_for_pages(image_paths)

    assert [item.page_number for item in page_results] == [1, 2]
    assert page_results[0].text == "First page"
    assert merge_ocr_page_texts(page_results) == "First page\n\nSecond page"
    assert image_to_string.call_count == 2


def test_ocr_quality_helpers_compute_confidence_and_low_confidence_flag() -> None:
    high_confidence = calculate_ocr_confidence(["Нормальный распознанный текст 123"])
    low_confidence = calculate_ocr_confidence(["", ""])

    assert high_confidence > 0.7
    assert low_confidence == 0.0
    assert is_low_confidence_parse(high_confidence) is False
    assert is_low_confidence_parse(low_confidence) is True


def test_run_document_ocr_saves_raw_ocr_artifact_and_queues_normalize(tmp_path: Path) -> None:
    version_id = uuid4()
    document_id = uuid4()
    pdf_path = tmp_path / "raw" / "source.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4")

    pdf_artifact = RawArtifact(
        document_version_id=version_id,
        artifact_type=ArtifactType.PDF_RAW,
        storage_path=str(pdf_path),
        relative_path="raw/source.pdf",
        checksum_sha256="0" * 64,
    )
    version = DocumentVersion(id=version_id, document_id=document_id)
    version.processing_status = ProcessingStatus.EXTRACTED
    document = Document(
        id=document_id,
        normalized_code="SP 1.0.0",
        display_code="SP 1.0.0",
        status_normalized=StatusNormalized.ACTIVE,
    )

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.return_value.scalars.return_value.all.return_value = [pdf_artifact]
    session.execute.return_value.scalar_one_or_none.side_effect = [None, None]

    mocked_pages = [
        SimpleNamespace(image_path=tmp_path / "rendered" / "page_0001.png"),
        SimpleNamespace(image_path=tmp_path / "rendered" / "page_0002.png"),
    ]
    mocked_ocr_results = [
        OcrPageResult(page_number=1, image_path=mocked_pages[0].image_path, text="First scanned page"),
        OcrPageResult(page_number=2, image_path=mocked_pages[1].image_path, text="Second scanned page"),
    ]

    with patch("qanorm.services.document_pipeline.render_pdf_pages", return_value=mocked_pages) as render_mock:
        with patch("qanorm.services.document_pipeline.run_ocr_for_pages", return_value=mocked_ocr_results) as ocr_mock:
            result = run_document_ocr(
                session,
                document_version_id=version_id,
                raw_store=RawFileStore(base_path=tmp_path / "ocr"),
                render_dpi=200,
            )

    assert result.status == "ok"
    assert result.page_count == 2
    assert result.text_length == len("First scanned page\n\nSecond scanned page")
    assert result.parse_confidence == version.parse_confidence
    assert result.low_confidence_parse is False
    assert result.saved_artifact_count == 1
    assert version.has_ocr is True
    assert version.processing_status is ProcessingStatus.EXTRACTED
    render_mock.assert_called_once()
    ocr_mock.assert_called_once_with([item.image_path for item in mocked_pages], languages=None)

    added_instances = [call.args[0] for call in session.add.call_args_list]
    assert len(added_instances) == 2
    assert isinstance(added_instances[0], RawArtifact)
    assert added_instances[0].artifact_type is ArtifactType.OCR_RAW
    assert Path(added_instances[0].storage_path).exists() is True
    assert isinstance(added_instances[1], IngestionJob)
    assert added_instances[1].job_type is JobType.NORMALIZE_DOCUMENT
