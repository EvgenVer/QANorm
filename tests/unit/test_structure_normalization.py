from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from qanorm.db.types import ArtifactType, JobType, ProcessingStatus, StatusNormalized
from qanorm.models import Document, DocumentVersion, IngestionJob, RawArtifact
from qanorm.normalizers.structure import normalize_document_structure_text, prepare_text_for_structure_parsing
from qanorm.services.document_pipeline import normalize_document_structure


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


def test_prepare_text_for_structure_parsing_preserves_char_ranges_and_page_markers() -> None:
    prepared = prepare_text_for_structure_parsing(
        "Title\r\n[[PAGE:2]]\r\nSection line\r\n\r\nPoint line"
    )

    assert prepared.text == "Title\nSection line\nPoint line"
    assert [line.page_number for line in prepared.lines] == [None, 2, 2]
    assert prepared.lines[1].char_start == len("Title\n")
    assert prepared.lines[2].char_start == len("Title\nSection line\n")


def test_normalize_document_structure_text_detects_nodes_hierarchy_and_references() -> None:
    text = "\n".join(
        [
            "Test Document Title",
            "[[PAGE:1]]",
            "РАЗДЕЛ I Общие положения",
            "1.1 Область применения",
            "1. Основное требование",
            "а) Детализация требования",
            "Настоящий документ ссылается на ГОСТ Р 1.0-2020.",
            "ПРИЛОЖЕНИЕ А Дополнение",
            "ТАБЛИЦА 1 Значения",
            "ПРИМЕЧАНИЕ Дополнительное пояснение",
        ]
    )

    result = normalize_document_structure_text(text, parse_confidence=0.88)

    assert [node.node_type for node in result.nodes] == [
        "title",
        "section",
        "subsection",
        "point",
        "subpoint",
        "paragraph",
        "appendix",
        "table",
        "note",
    ]
    assert result.nodes[1].parent_order_index == 1
    assert result.nodes[2].parent_order_index == 2
    assert result.nodes[3].parent_order_index == 3
    assert result.nodes[4].parent_order_index == 4
    assert result.nodes[5].parent_order_index == 5
    assert result.nodes[3].page_from == 1
    assert result.nodes[5].char_start is not None
    assert result.nodes[5].char_end is not None
    assert result.nodes[5].parse_confidence == 0.88
    assert result.references[0].reference_text == "ГОСТ Р 1.0-2020"
    assert result.references[0].referenced_code_normalized == "ГОСТ Р 1.0-2020"


def test_normalize_document_structure_persists_nodes_references_and_queues_index(tmp_path: Path) -> None:
    version_id = uuid4()
    document_id = uuid4()
    matched_document_id = uuid4()
    snapshot_path = tmp_path / "snapshots" / "parsed_text_snapshot_html.txt"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        "\n".join(
            [
                "Test Document Title",
                "РАЗДЕЛ I Общие положения",
                "1.1 Область применения",
                "1. Основное требование",
                "Настоящий документ ссылается на ГОСТ Р 1.0-2020.",
            ]
        ),
        encoding="utf-8",
    )

    snapshot_artifact = RawArtifact(
        document_version_id=version_id,
        artifact_type=ArtifactType.PARSED_TEXT_SNAPSHOT,
        storage_path=str(snapshot_path),
        relative_path="snapshots/parsed_text_snapshot_html.txt",
        file_size=snapshot_path.stat().st_size,
        checksum_sha256="0" * 64,
    )
    version = DocumentVersion(id=version_id, document_id=document_id)
    version.processing_status = ProcessingStatus.EXTRACTED
    version.parse_confidence = 0.91
    document = Document(
        id=document_id,
        normalized_code="SP 1.0",
        display_code="SP 1.0",
        status_normalized=StatusNormalized.ACTIVE,
    )
    matched_document = Document(
        id=matched_document_id,
        normalized_code="ГОСТ Р 1.0-2020",
        display_code="ГОСТ Р 1.0-2020",
        status_normalized=StatusNormalized.ACTIVE,
    )

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.side_effect = [
        _ScalarListResult([snapshot_artifact]),
        _ScalarOneResult(matched_document),
        _ScalarOneResult(None),
    ]

    with patch(
        "qanorm.services.document_pipeline.compare_candidate_version_to_active",
        return_value=SimpleNamespace(
            content_hash="abc123",
            is_duplicate=False,
            active_version_id=None,
        ),
    ) as compare_mock:
        with patch("qanorm.services.document_pipeline.activate_processed_version") as activate_mock:
            result = normalize_document_structure(session, document_version_id=version_id)

    assert result.status == "ok"
    assert result.source_artifact_type == ArtifactType.PARSED_TEXT_SNAPSHOT.value
    assert result.node_count >= 5
    assert result.reference_count == 1
    assert result.content_hash == "abc123"
    assert result.deduplicated is False
    assert version.processing_status is ProcessingStatus.NORMALIZED
    assert session.add_all.call_count == 2
    compare_mock.assert_called_once()
    activate_mock.assert_called_once_with(
        session,
        document_version_id=version_id,
        content_hash="abc123",
    )

    saved_nodes = session.add_all.call_args_list[0].args[0]
    saved_references = session.add_all.call_args_list[1].args[0]
    assert saved_nodes[0].node_type == "title"
    assert saved_nodes[1].parent_node_id == saved_nodes[0].id
    assert saved_references[0].matched_document_id == matched_document_id
    assert saved_references[0].match_confidence == 1.0

    session.add.assert_called_once()
    created_job = session.add.call_args.args[0]
    assert isinstance(created_job, IngestionJob)
    assert created_job.job_type is JobType.INDEX_DOCUMENT
