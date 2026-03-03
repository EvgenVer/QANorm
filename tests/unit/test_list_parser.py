from __future__ import annotations

from qanorm.parsers.list_parser import (
    detect_list_page_kind,
    extract_pagination_urls,
    parse_list2_list_page,
    parse_mega_doc_list_page,
)


MEGA_DOC_HTML = """
<div class="div_linc_top">
  <div class="div_linc_top_in_activ">[1]</div>
  <div class="div_linc_top_in"><a href="./sp_svod-pravil_1.html">2</a></div>
  <div class="div_linc_top_in"><a href="./sp_svod-pravil_2.html">3</a></div>
</div>
<div class="table_doc">
  <div class="row header">
    <div class="cell header-cell">№</div>
    <div class="cell header-cell">Наименование</div>
    <div class="cell header-cell">Статус</div>
  </div>
  <div class="row">
    <div class="cell">1</div>
    <div class="cell">
      <a href="../../../mega_doc/norm/pravila/0/doc_1.html">
        "СП 20.13330.2016. Нагрузки и воздействия"
        (ред. от 01.01.2024)
      </a>
    </div>
    <div class="cell">действует</div>
  </div>
  <div class="row">
    <div class="cell">2</div>
    <div class="cell">
      <a href="../../../mega_doc/norm/pravila/0/doc_2.html">
        "СП 30.13330.2020. Внутренний водопровод и канализация"
      </a>
    </div>
    <div class="cell">взамен</div>
  </div>
</div>
"""


LIST2_HTML = """
<span class="pagebox">
  <a href="../list2/64522-0.htm"><b>[1]</b></a>
  <a href="../list2/64522-1.htm">2</a>
  <a href="../list2/64522-2.htm">3</a>
</span>
<table class="doctab1">
  <tr class="m1">
    <td>Номер</td><td>Название</td><td>Дата введения</td><td>Статус</td>
  </tr>
  <tr class="m3">
    <td align="left">
      <a class="a2" href="../Data2/1/4294845/4294845305.pdf" target="_blank">pdf</a>
      <a href="../Index2/1/4294845/4294845305.htm" target="_blank">Федеральный закон 3-ФЗ</a>
    </td>
    <td align="left">О радиационной безопасности населения</td>
    <td align="center">15.01.1996</td>
    <td align="center"><font color="#0000FF"><b>действует</b></font></td>
  </tr>
  <tr class="m3">
    <td align="left">
      <a href="../Index2/1/4293750/4293750616.htm" target="_blank">Федеральный закон 7-ФЗ</a>
    </td>
    <td align="left">Об охране окружающей среды</td>
    <td align="center">10.01.2002</td>
    <td align="center"><font color="#0000FF"><b>действует</b></font></td>
  </tr>
</table>
"""


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
