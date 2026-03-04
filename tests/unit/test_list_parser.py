from __future__ import annotations

from qanorm.parsers.list_parser import (
    detect_list_page_kind,
    extract_pagination_urls,
    parse_list2_list_page,
    parse_mega_doc_list_page,
)
from tests.unit.fixture_loader import read_fixture_text


MEGA_DOC_HTML = read_fixture_text("list_pages", "mega_doc_page.html")
LIST2_HTML = read_fixture_text("list_pages", "list2_page.html")


def test_detect_list_page_kind_distinguishes_sources() -> None:
    assert (
        detect_list_page_kind("https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html")
        == "mega_doc"
    )
    assert detect_list_page_kind("https://meganorm.ru/list2/64522-0.htm") == "list2"


def test_extract_pagination_urls_for_mega_doc() -> None:
    page_urls = extract_pagination_urls(
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
        MEGA_DOC_HTML,
    )

    assert page_urls == [
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_1.html",
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_2.html",
    ]


def test_parse_mega_doc_list_page_extracts_entries() -> None:
    entries = parse_mega_doc_list_page(
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
        MEGA_DOC_HTML,
    )

    assert len(entries) == 2
    assert entries[0].card_url == "https://meganorm.ru/mega_doc/norm/pravila/0/doc_1.html"
    assert entries[0].document_code == "СП 20.13330.2016"
    assert entries[0].title == "Нагрузки и воздействия"
    assert entries[0].status_raw == "действует"
    assert entries[1].status_raw == "взамен"


def test_extract_pagination_urls_for_list2() -> None:
    page_urls = extract_pagination_urls(
        "https://meganorm.ru/list2/64522-0.htm",
        LIST2_HTML,
    )

    assert page_urls == [
        "https://meganorm.ru/list2/64522-0.htm",
        "https://meganorm.ru/list2/64522-1.htm",
        "https://meganorm.ru/list2/64522-2.htm",
    ]


def test_parse_list2_list_page_extracts_entries() -> None:
    entries = parse_list2_list_page(
        "https://meganorm.ru/list2/64522-0.htm",
        LIST2_HTML,
    )

    assert len(entries) == 2
    assert entries[0].card_url == "https://meganorm.ru/Index2/1/4294845/4294845305.htm"
    assert entries[0].document_code == "Федеральный закон 3-ФЗ"
    assert entries[0].title == "О радиационной безопасности населения"
    assert entries[0].status_raw == "действует"
