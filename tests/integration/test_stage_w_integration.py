from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from qanorm.cli.main import _build_alembic_config, init_db
from qanorm.crawler.list_pages import crawl_seed_first_page
from qanorm.db.types import ArtifactType, JobType, ProcessingStatus, StatusNormalized
from qanorm.indexing.indexer import index_document_version
from qanorm.models import Document, DocumentNode, DocumentSource, DocumentVersion, RawArtifact
from qanorm.ocr.tesseract import OcrPageResult
from qanorm.services.document_pipeline import (
    download_document_artifacts,
    extract_document_text,
    normalize_document_structure,
    orchestrate_document_pipeline_step,
    process_document_card,
    run_document_ocr,
)
from qanorm.services.ingestion import process_parse_list_page_job
from qanorm.services.refresh_service import process_refresh_document_job
from qanorm.services.versioning import compute_version_content_hash
from qanorm.settings import get_settings
from qanorm.storage.raw_store import RawFileStore
from tests.integration.support import FakeSession, patched_in_memory_repositories
from tests.unit.fixture_loader import fixture_path, read_fixture_text


def _pdf_bytes(name: str) -> bytes:
    return fixture_path("pdfs", name).read_bytes()


def _html_document() -> str:
    return read_fixture_text("documents", "full_html_document.html")


def _ocr_page_results() -> list[OcrPageResult]:
    return [
        OcrPageResult(
            page_number=1,
            image_path=Path("page_0001.png"),
            text=read_fixture_text("ocr", "ocr_result.txt"),
        )
    ]


def _card_payload(*, full_html: bool) -> str:
    fixture_name = "index_card_full_html.html" if full_html else "index_card_pdf_only.html"
    return read_fixture_text("cards", fixture_name)


def _job_payload(session: FakeSession, index: int) -> dict[str, object]:
    return dict(session.store.jobs[index].payload)


def test_386_integration_loads_runtime_config() -> None:
    settings = get_settings()

    assert settings.app.request_timeout_seconds > 0
    assert settings.sources.seed_urls
    assert settings.statuses.active
    assert settings.statuses.inactive


def test_387_integration_init_db_invokes_alembic_head(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_upgrade(config, revision: str) -> None:
        calls.append((config.config_file_name, revision))

    monkeypatch.setattr("qanorm.cli.main.command.upgrade", fake_upgrade)

    init_db()

    config = _build_alembic_config()
    assert calls == [(config.config_file_name, "head")]
    assert Path(config.config_file_name).exists() is True


def test_388_integration_crawls_one_seed_first_page() -> None:
    fixture_html = read_fixture_text("list_pages", "mega_doc_page.html")

    class FakeFetcher:
        def get_html(self, url: str) -> str:
            return fixture_html

        def close(self) -> None:
            return None

    snapshot = crawl_seed_first_page(
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
        fetcher=FakeFetcher(),
    )

    assert len(snapshot.page_urls) == 3
    assert len(snapshot.entries) == 2
    assert snapshot.entries[0].document_code == "СП 20.13330.2016"


def test_389_integration_parses_one_list_page(monkeypatch) -> None:
    session = FakeSession()
    with patched_in_memory_repositories(session):
        monkeypatch.setattr(
            "qanorm.services.ingestion.fetch_html_document",
            lambda url: read_fixture_text("list_pages", "list2_page.html"),
        )
        result = process_parse_list_page_job(
            session,
            list_page_url="https://meganorm.ru/list2/64522-0.htm",
            seed_url="https://meganorm.ru/list2/64522-0.htm",
        )

    assert result.status == "ok"
    assert result.discovered_entry_count == 2
    assert session.store.jobs[0].job_type is JobType.PROCESS_DOCUMENT_CARD


def test_390_integration_processes_card_until_download_queue(monkeypatch) -> None:
    session = FakeSession()
    with patched_in_memory_repositories(session):
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_document_card", lambda url: _card_payload(full_html=True))
        result = process_document_card(
            session,
            card_url="https://meganorm.ru/Index2/1/4294845/4294845305.htm",
            list_status_raw="действует",
            list_page_url="https://meganorm.ru/list2/64522-0.htm",
            seed_url="https://meganorm.ru/list2/64522-0.htm",
        )

    assert result.status == "queued"
    assert len(session.store.documents) == 1
    assert len(session.store.versions) == 1
    assert len(session.store.sources) == 1
    assert session.store.jobs[0].job_type is JobType.DOWNLOAD_ARTIFACTS


def test_391_integration_downloads_raw_artifacts(monkeypatch, tmp_path: Path) -> None:
    session = FakeSession()
    document = Document(
        id=uuid4(),
        normalized_code="FEDERAL LAW 3-FZ",
        display_code="Federal Law 3-FZ",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id)
    session.store.documents.append(document)
    session.store.versions.append(version)

    with patched_in_memory_repositories(session):
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_html_document", lambda url: _html_document())
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_pdf_bytes", lambda url: _pdf_bytes("text_layer_sample.pdf"))
        result = download_document_artifacts(
            session,
            document_version_id=version.id,
            document_code=document.display_code,
            card_url="https://meganorm.ru/Index2/1/4294845/4294845305.htm",
            html_url="https://meganorm.ru/Data2/1/doc.htm",
            pdf_url="https://meganorm.ru/Data2/1/doc.pdf",
            print_url=None,
            has_full_html=True,
            has_page_images=False,
            raw_store=RawFileStore(base_path=tmp_path / "raw_store"),
        )

    assert result.status == "ok"
    assert result.saved_artifact_count == 2
    assert len(session.store.artifacts) == 2
    assert session.store.jobs[-1].job_type is JobType.EXTRACT_TEXT


def test_392_integration_extracts_html_text(tmp_path: Path) -> None:
    session = FakeSession()
    document = Document(
        id=uuid4(),
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id)
    session.store.documents.append(document)
    session.store.versions.append(version)

    artifact_path = tmp_path / "raw" / "document.html"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(_html_document(), encoding="utf-8")
    session.store.artifacts.append(
        RawArtifact(
            document_version_id=version.id,
            artifact_type=ArtifactType.HTML_RAW,
            storage_path=str(artifact_path),
            relative_path="raw/document.html",
            checksum_sha256="0" * 64,
        )
    )

    with patched_in_memory_repositories(session):
        result = extract_document_text(
            session,
            document_version_id=version.id,
            raw_store=RawFileStore(base_path=tmp_path / "snapshots"),
        )

    assert result.status == "ok"
    assert result.chosen_source == "html"
    assert result.needs_ocr is False
    assert any(item.artifact_type is ArtifactType.PARSED_TEXT_SNAPSHOT for item in session.store.artifacts)


def test_393_integration_extracts_pdf_text(tmp_path: Path) -> None:
    session = FakeSession()
    document = Document(
        id=uuid4(),
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id)
    session.store.documents.append(document)
    session.store.versions.append(version)

    pdf_path = tmp_path / "raw" / "document.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(_pdf_bytes("text_layer_sample.pdf"))
    session.store.artifacts.append(
        RawArtifact(
            document_version_id=version.id,
            artifact_type=ArtifactType.PDF_RAW,
            storage_path=str(pdf_path),
            relative_path="raw/document.pdf",
            checksum_sha256="0" * 64,
        )
    )

    with patched_in_memory_repositories(session):
        result = extract_document_text(
            session,
            document_version_id=version.id,
            raw_store=RawFileStore(base_path=tmp_path / "snapshots"),
        )

    assert result.status == "ok"
    assert result.chosen_source == "pdf"
    assert result.needs_ocr is False


def test_394_integration_runs_ocr_fallback(monkeypatch, tmp_path: Path) -> None:
    session = FakeSession()
    document = Document(
        id=uuid4(),
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id, processing_status=ProcessingStatus.EXTRACTED)
    session.store.documents.append(document)
    session.store.versions.append(version)

    pdf_path = tmp_path / "raw" / "scan.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(_pdf_bytes("scan_emulated_sample.pdf"))
    session.store.artifacts.append(
        RawArtifact(
            document_version_id=version.id,
            artifact_type=ArtifactType.PDF_RAW,
            storage_path=str(pdf_path),
            relative_path="raw/scan.pdf",
            checksum_sha256="0" * 64,
        )
    )

    with patched_in_memory_repositories(session):
        monkeypatch.setattr(
            "qanorm.services.document_pipeline.run_ocr_for_pages",
            lambda image_paths, languages=None: _ocr_page_results(),
        )
        result = run_document_ocr(
            session,
            document_version_id=version.id,
            raw_store=RawFileStore(base_path=tmp_path / "ocr"),
        )

    assert result.status == "ok"
    assert result.page_count == 1
    assert version.has_ocr is True
    assert any(item.artifact_type is ArtifactType.OCR_RAW for item in session.store.artifacts)


def test_395_integration_normalizes_structure(tmp_path: Path) -> None:
    session = FakeSession()
    document = Document(
        id=uuid4(),
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id, processing_status=ProcessingStatus.EXTRACTED)
    session.store.documents.append(document)
    session.store.versions.append(version)

    source_path = tmp_path / "raw" / "ocr.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(read_fixture_text("ocr", "ocr_result.txt"), encoding="utf-8")
    session.store.artifacts.append(
        RawArtifact(
            document_version_id=version.id,
            artifact_type=ArtifactType.OCR_RAW,
            storage_path=str(source_path),
            relative_path="raw/ocr.txt",
            checksum_sha256="0" * 64,
        )
    )

    with patched_in_memory_repositories(session):
        result = normalize_document_structure(session, document_version_id=version.id)

    assert result.status == "ok"
    assert result.deduplicated is False
    assert len(session.store.nodes) >= 2
    assert session.store.jobs[-1].job_type is JobType.INDEX_DOCUMENT


def test_396_integration_detects_deduplicated_version(tmp_path: Path) -> None:
    document = Document(
        id=uuid4(),
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    active_version = DocumentVersion(
        id=uuid4(),
        document_id=document.id,
        is_active=True,
        content_hash=compute_version_content_hash(read_fixture_text("dedup", "same_designation", "version_a.txt")),
    )
    candidate_version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=False)
    document.current_version_id = active_version.id
    session = FakeSession()
    session.store.documents.append(document)
    session.store.versions.extend([active_version, candidate_version])

    source_path = tmp_path / "raw" / "candidate.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(read_fixture_text("dedup", "same_designation", "version_b_equivalent.txt"), encoding="utf-8")
    session.store.artifacts.append(
        RawArtifact(
            document_version_id=candidate_version.id,
            artifact_type=ArtifactType.OCR_RAW,
            storage_path=str(source_path),
            relative_path="raw/candidate.txt",
            checksum_sha256="0" * 64,
        )
    )

    with patched_in_memory_repositories(session):
        result = normalize_document_structure(session, document_version_id=candidate_version.id)

    assert result.status == "ok"
    assert result.deduplicated is True
    assert document.current_version_id == active_version.id
    assert session.store.events[-1].status == "skipped_duplicate"


def test_397_integration_indexes_active_version() -> None:
    document = Document(
        id=uuid4(),
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    active_version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=True)
    stale_version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=False)
    document.current_version_id = active_version.id
    session = FakeSession()
    session.store.documents.append(document)
    session.store.versions.extend([active_version, stale_version])
    session.store.nodes.extend(
        [
            DocumentNode(document_version_id=active_version.id, node_type="point", text="Main active node", order_index=1),
            DocumentNode(
                document_version_id=stale_version.id,
                node_type="point",
                text="Stale node",
                order_index=1,
                text_tsv="old",
                embedding=[0.1, 0.2],
            ),
        ]
    )

    with patched_in_memory_repositories(session):
        result = index_document_version(session, document_version_id=active_version.id)

    assert result.status == "ok"
    assert session.store.nodes[0].text_tsv is not None
    assert session.store.nodes[1].text_tsv is None


def test_398_integration_refreshes_document_version(monkeypatch, tmp_path: Path) -> None:
    session = FakeSession()
    document = Document(
        id=uuid4(),
        normalized_code="FEDERAL LAW 3-FZ",
        display_code="Federal Law 3-FZ",
        status_normalized=StatusNormalized.ACTIVE,
    )
    current_version = DocumentVersion(
        id=uuid4(),
        document_id=document.id,
        is_active=True,
        processing_status=ProcessingStatus.INDEXED,
        source_status_raw="действует",
    )
    document.current_version_id = current_version.id
    current_source = DocumentSource(
        id=uuid4(),
        document_id=document.id,
        document_version_id=current_version.id,
        card_url="https://meganorm.ru/Index2/1/4294845/4294845305.htm",
        html_url="https://meganorm.ru/Data2/1/doc.htm",
        pdf_url="https://meganorm.ru/Data2/1/doc.pdf",
        seed_url="https://meganorm.ru/list2/64522-0.htm",
        list_page_url="https://meganorm.ru/list2/64522-0.htm",
        source_type="index_card",
    )
    session.store.documents.append(document)
    session.store.versions.append(current_version)
    session.store.sources.append(current_source)

    def raw_store_factory(base_path=None):
        return RawFileStore(base_path=tmp_path / "refresh_store")

    with patched_in_memory_repositories(session):
        monkeypatch.setattr("qanorm.services.refresh_service.fetch_document_card", lambda url: _card_payload(full_html=True))
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_html_document", lambda url: _html_document())
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_pdf_bytes", lambda url: _pdf_bytes("text_layer_sample.pdf"))
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_image_bytes", lambda url: b"GIF89a")
        monkeypatch.setattr("qanorm.services.document_pipeline.RawFileStore", raw_store_factory)
        result = process_refresh_document_job("Federal Law 3-FZ", session=session)

    assert result.status == "refresh_completed"
    assert result.new_version_id is not None
    assert len(session.store.versions) == 2
    assert any(str(item.id) == result.new_version_id for item in session.store.versions)
    assert any(item.processing_status is ProcessingStatus.INDEXED for item in session.store.versions if str(item.id) == result.new_version_id)
    assert session.store.events[-1].status == "refresh_completed"


def test_399_integration_runs_full_pipeline_for_html_document(monkeypatch, tmp_path: Path) -> None:
    session = FakeSession()

    def raw_store_factory(base_path=None):
        return RawFileStore(base_path=tmp_path / "html_pipeline_store")

    with patched_in_memory_repositories(session):
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_document_card", lambda url: _card_payload(full_html=True))
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_html_document", lambda url: _html_document())
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_pdf_bytes", lambda url: _pdf_bytes("text_layer_sample.pdf"))
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_image_bytes", lambda url: b"GIF89a")
        monkeypatch.setattr("qanorm.services.document_pipeline.RawFileStore", raw_store_factory)

        process_document_card(
            session,
            card_url="https://meganorm.ru/Index2/1/4294845/4294845305.htm",
            list_status_raw="действует",
        )
        orchestrate_document_pipeline_step(session, job_type=JobType.DOWNLOAD_ARTIFACTS, payload=_job_payload(session, 0))
        orchestrate_document_pipeline_step(session, job_type=JobType.EXTRACT_TEXT, payload=_job_payload(session, 1))
        orchestrate_document_pipeline_step(session, job_type=JobType.NORMALIZE_DOCUMENT, payload=_job_payload(session, 2))
        index_result = orchestrate_document_pipeline_step(session, job_type=JobType.INDEX_DOCUMENT, payload=_job_payload(session, 3))

    assert index_result["status"] == "ok"
    assert session.store.versions[0].processing_status is ProcessingStatus.INDEXED
    assert session.store.documents[0].current_version_id == session.store.versions[0].id
    assert session.store.nodes


def test_400_integration_runs_full_pipeline_for_pdf_ocr_document(monkeypatch, tmp_path: Path) -> None:
    session = FakeSession()

    def raw_store_factory(base_path=None):
        return RawFileStore(base_path=tmp_path / "pdf_pipeline_store")

    with patched_in_memory_repositories(session):
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_document_card", lambda url: _card_payload(full_html=False))
        monkeypatch.setattr("qanorm.services.document_pipeline.fetch_pdf_bytes", lambda url: _pdf_bytes("scan_emulated_sample.pdf"))
        monkeypatch.setattr(
            "qanorm.services.document_pipeline.run_ocr_for_pages",
            lambda image_paths, languages=None: _ocr_page_results(),
        )
        monkeypatch.setattr("qanorm.services.document_pipeline.RawFileStore", raw_store_factory)

        process_document_card(
            session,
            card_url="https://meganorm.ru/Index2/77/1000001.htm",
            list_status_raw="действует",
        )
        orchestrate_document_pipeline_step(session, job_type=JobType.DOWNLOAD_ARTIFACTS, payload=_job_payload(session, 0))
        extract_result = orchestrate_document_pipeline_step(session, job_type=JobType.EXTRACT_TEXT, payload=_job_payload(session, 1))
        assert extract_result["needs_ocr"] is True
        orchestrate_document_pipeline_step(session, job_type=JobType.RUN_OCR, payload=_job_payload(session, 2))
        orchestrate_document_pipeline_step(session, job_type=JobType.NORMALIZE_DOCUMENT, payload=_job_payload(session, 3))
        index_result = orchestrate_document_pipeline_step(session, job_type=JobType.INDEX_DOCUMENT, payload=_job_payload(session, 4))

    assert index_result["status"] == "ok"
    assert session.store.versions[0].has_ocr is True
    assert session.store.versions[0].processing_status is ProcessingStatus.INDEXED
    assert any(item.artifact_type is ArtifactType.OCR_RAW for item in session.store.artifacts)
