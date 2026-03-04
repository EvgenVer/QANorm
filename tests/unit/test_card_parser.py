from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from qanorm.db.types import JobType, StatusNormalized
from qanorm.models import Document, IngestionJob
from qanorm.parsers.card_parser import parse_document_card
from qanorm.services.document_pipeline import persist_document_card
from tests.unit.fixture_loader import read_fixture_text


INDEX_CARD_HTML = read_fixture_text("cards", "index_card_full_html.html")
INDEX_CARD_PDF_ONLY_HTML = read_fixture_text("cards", "index_card_pdf_only.html")
MEGA_DOC_CARD_HTML = """<?xml version="1.0" encoding="utf-8" ?>
<html>
  <head>
    <title>"СП 128.13330.2016. Свод правил. Алюминиевые конструкции" (ред. от 29.07.2024)</title>
  </head>
  <body>
    <div class="contener_doc">
      <span class="s4">1 Область применения</span>
      <span class="s2">Настоящий документ устанавливает требования к алюминиевым конструкциям.</span>
      <div class="s0 aJ bG">Первоначальный текст документа опубликован в издании М., 2016.</div>
      <a href="/mega_doc/norm/gost/1/doc.html" title="ГОСТ 1.0">ГОСТ 1.0</a>
    </div>
  </body>
</html>
"""


def _mock_session() -> MagicMock:
    return MagicMock()


def test_parse_index_card_extracts_metadata_and_source_links() -> None:
    card = parse_document_card(
        "https://meganorm.ru/Index2/1/4294845/4294845305.htm",
        INDEX_CARD_HTML,
        source_list_status_raw="действует",
    )

    assert card.source_type == "index_card"
    assert card.document_code == "Федеральный закон 3-ФЗ"
    assert card.document_title == "О радиационной безопасности населения"
    assert card.card_status_raw == "действует"
    assert card.text_actualized_at == date(2021, 1, 1)
    assert card.effective_from == date(1996, 1, 15)
    assert card.published_at == date(1996, 1, 17)
    assert card.scope_text == "Правовые основы обеспечения радиационной безопасности населения"
    assert card.normative_references == ["Федеральный закон 294-ФЗ"]
    assert card.pdf_url == "https://meganorm.ru/Data2/1/doc.pdf"
    assert card.html_url == "https://meganorm.ru/Data2/1/doc.htm"
    assert card.has_page_images is True


def test_parse_index_card_pdf_only_fixture_extracts_pdf_without_html_link() -> None:
    card = parse_document_card(
        "https://meganorm.ru/Index2/77/1000001.htm",
        INDEX_CARD_PDF_ONLY_HTML,
        source_list_status_raw="действует",
    )

    assert card.document_code == "ГОСТ Р 1.0-2020"
    assert card.card_status_raw == "действует"
    assert card.text_actualized_at == date(2022, 2, 5)
    assert card.pdf_url == "https://meganorm.ru/Data2/77/gost_r_1_0_2020.pdf"
    assert card.html_url is None
    assert card.has_full_html is False
    assert card.has_page_images is False


def test_parse_mega_doc_card_extracts_basic_metadata() -> None:
    card = parse_document_card(
        "https://meganorm.ru/mega_doc/norm/pravila/0/sp_128_13330_2016.html",
        MEGA_DOC_CARD_HTML,
        source_list_status_raw="действует",
    )

    assert card.source_type == "mega_doc_card"
    assert card.document_code == "СП 128.13330.2016"
    assert card.document_title == "Свод правил. Алюминиевые конструкции"
    assert card.card_status_raw is None
    assert card.text_actualized_at == date(2024, 7, 29)
    assert card.published_at == date(2016, 1, 1)
    assert card.scope_text == "Настоящий документ устанавливает требования к алюминиевым конструкциям."
    assert card.html_url == "https://meganorm.ru/mega_doc/norm/pravila/0/sp_128_13330_2016.html"


def test_persist_document_card_skips_inactive_documents() -> None:
    session = _mock_session()
    inactive_html = INDEX_CARD_HTML.replace("действует", "утратил силу", 1)
    card = parse_document_card(
        "https://meganorm.ru/Index2/1/4294845/4294845305.htm",
        inactive_html,
        source_list_status_raw="действует",
    )

    result = persist_document_card(session, card_data=card)

    assert result.status == "skipped"
    assert result.skip_reason == "inactive"
    session.add.assert_not_called()


def test_persist_document_card_creates_document_version_source_and_job() -> None:
    session = _mock_session()
    session.execute.return_value.scalar_one_or_none.return_value = None
    card = parse_document_card(
        "https://meganorm.ru/Index2/1/4294845/4294845305.htm",
        INDEX_CARD_HTML,
        source_list_status_raw="действует",
    )

    result = persist_document_card(
        session,
        card_data=card,
        list_page_url="https://meganorm.ru/list2/64522-0.htm",
        seed_url="https://meganorm.ru/list2/64522-0.htm",
    )

    assert result.status == "queued"
    assert session.add.call_count == 4
    added_instances = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(added_instances[0], Document)
    assert added_instances[0].status_normalized is StatusNormalized.ACTIVE
    assert added_instances[1].document_id == added_instances[0].id
    assert added_instances[2].pdf_url == "https://meganorm.ru/Data2/1/doc.pdf"
    assert isinstance(added_instances[3], IngestionJob)
    assert added_instances[3].job_type is JobType.DOWNLOAD_ARTIFACTS
