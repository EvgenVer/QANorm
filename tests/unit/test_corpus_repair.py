from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from qanorm.cli.main import build_parser
from qanorm.services.corpus_repair import _repair_document_from_card, run_targeted_corpus_repair


@contextmanager
def _fake_session_scope() -> MagicMock:
    yield MagicMock()


def test_build_parser_supports_repair_targeted_corpus_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["repair-targeted-corpus"])

    assert args.command == "repair-targeted-corpus"


def test_run_targeted_corpus_repair_aggregates_results() -> None:
    repaired = [
        SimpleNamespace(
            document_code="ГОСТ 27751-2014",
            document_version_id="v1",
            normalize_status="ok",
            deduplicated=False,
            indexed=True,
        ),
        SimpleNamespace(
            document_code="СП 1.13130.2020",
            document_version_id="v2",
            normalize_status="ok",
            deduplicated=False,
            indexed=True,
        ),
    ]

    with patch("qanorm.services.corpus_repair.session_scope", _fake_session_scope):
        with patch("qanorm.services.corpus_repair._delete_placeholder_documents", return_value=1):
            with patch("qanorm.services.corpus_repair._repair_document_from_card", side_effect=repaired):
                with patch(
                    "qanorm.services.corpus_repair.rebuild_derived_retrieval_data",
                    side_effect=[
                        SimpleNamespace(
                            aliases=SimpleNamespace(aliases_created=5),
                            retrieval_units=SimpleNamespace(units_created=10),
                        ),
                        SimpleNamespace(
                            aliases=SimpleNamespace(aliases_created=6),
                            retrieval_units=SimpleNamespace(units_created=12),
                        ),
                    ],
                ):
                    with patch(
                        "qanorm.services.corpus_repair.backfill_retrieval_unit_embeddings",
                        return_value=SimpleNamespace(processed_units=22),
                    ):
                        result = run_targeted_corpus_repair()

    assert result["status"] == "ok"
    assert result["placeholders_deleted"] == 1
    assert result["aliases_rebuilt"] == 11
    assert result["retrieval_units_rebuilt"] == 22
    assert result["embeddings_backfilled"] == 22


def test_repair_document_from_card_runs_pipeline_without_ocr_when_not_needed() -> None:
    session = MagicMock()
    card_data = SimpleNamespace(
        document_code="СП 1.13130.2020",
        card_url="https://example.test/card",
        html_url="https://example.test/doc.html",
        pdf_url="https://example.test/doc.pdf",
        print_url=None,
        has_full_html=True,
        has_page_images=False,
    )

    with patch("qanorm.services.corpus_repair.fetch_document_card", return_value="<html></html>"):
        with patch("qanorm.services.corpus_repair.parse_document_card", return_value=card_data):
            with patch(
                "qanorm.services.corpus_repair.persist_document_card",
                return_value=SimpleNamespace(document_version_id="version-1"),
            ):
                with patch("qanorm.services.corpus_repair.download_document_artifacts"):
                    with patch(
                        "qanorm.services.corpus_repair.extract_document_text",
                        return_value=SimpleNamespace(needs_ocr=False),
                    ):
                        with patch(
                            "qanorm.services.corpus_repair.normalize_document_structure",
                            return_value=SimpleNamespace(status="ok", deduplicated=False),
                        ):
                            with patch("qanorm.services.corpus_repair.index_document_version") as index_mock:
                                with patch("qanorm.services.corpus_repair.run_document_ocr") as ocr_mock:
                                    result = _repair_document_from_card(session, card_url=card_data.card_url)

    assert result.document_code == "СП 1.13130.2020"
    assert result.document_version_id == "version-1"
    assert result.indexed is True
    ocr_mock.assert_not_called()
    index_mock.assert_called_once()
