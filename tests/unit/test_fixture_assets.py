from __future__ import annotations

from qanorm.db.types import StatusNormalized
from qanorm.normalizers.statuses import resolve_status_conflict
from qanorm.ocr.quality import calculate_ocr_confidence
from qanorm.parsers.card_parser import parse_document_card
from qanorm.parsers.list_parser import parse_list2_list_page, parse_mega_doc_list_page
from qanorm.parsers.pdf_text_parser import extract_text_from_pdf
from qanorm.services.versioning import compute_version_content_hash
from tests.unit.fixture_loader import fixture_path, read_fixture_json, read_fixture_text


def test_list_and_card_fixtures_parse_successfully() -> None:
    mega_doc_entries = parse_mega_doc_list_page(
        "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
        read_fixture_text("list_pages", "mega_doc_page.html"),
    )
    list2_entries = parse_list2_list_page(
        "https://meganorm.ru/list2/64522-0.htm",
        read_fixture_text("list_pages", "list2_page.html"),
    )
    html_card = parse_document_card(
        "https://meganorm.ru/Index2/1/4294845/4294845305.htm",
        read_fixture_text("cards", "index_card_full_html.html"),
        source_list_status_raw="действует",
    )
    pdf_only_card = parse_document_card(
        "https://meganorm.ru/Index2/77/1000001.htm",
        read_fixture_text("cards", "index_card_pdf_only.html"),
        source_list_status_raw="действует",
    )

    assert len(mega_doc_entries) == 2
    assert mega_doc_entries[0].document_code == "СП 20.13330.2016"
    assert len(list2_entries) == 2
    assert list2_entries[0].document_code == "Федеральный закон 3-ФЗ"
    assert html_card.html_url == "https://meganorm.ru/Data2/1/doc.htm"
    assert pdf_only_card.html_url is None
    assert pdf_only_card.pdf_url == "https://meganorm.ru/Data2/77/gost_r_1_0_2020.pdf"


def test_pdf_and_ocr_fixtures_are_usable() -> None:
    text_pdf = fixture_path("pdfs", "text_layer_sample.pdf")
    scan_pdf = fixture_path("pdfs", "scan_emulated_sample.pdf")
    ocr_text = read_fixture_text("ocr", "ocr_result.txt")

    text_result = extract_text_from_pdf(text_pdf)
    scan_result = extract_text_from_pdf(scan_pdf)

    assert text_pdf.exists() is True
    assert scan_pdf.exists() is True
    assert "SP 20.13330.2016" in text_result.combined_text
    assert text_result.needs_ocr is False
    assert scan_result.needs_ocr is True
    assert calculate_ocr_confidence([ocr_text]) > 0.5


def test_status_conflict_and_dedup_fixtures_match_expected_rules() -> None:
    active_list_inactive_card = read_fixture_json("status_conflicts", "active_list_inactive_card.json")
    inactive_list_active_card = read_fixture_json("status_conflicts", "inactive_list_active_card.json")
    dedup_manifest = read_fixture_json("dedup", "same_designation", "manifest.json")
    version_a = read_fixture_text("dedup", "same_designation", "version_a.txt")
    version_b = read_fixture_text("dedup", "same_designation", "version_b_equivalent.txt")

    _, first_status = resolve_status_conflict(
        active_list_inactive_card["source_list_status_raw"],
        active_list_inactive_card["card_status_raw"],
    )
    _, second_status = resolve_status_conflict(
        inactive_list_active_card["source_list_status_raw"],
        inactive_list_active_card["card_status_raw"],
    )

    assert first_status is StatusNormalized.INACTIVE
    assert second_status is StatusNormalized.ACTIVE
    assert active_list_inactive_card["expected_status_normalized"] == "inactive"
    assert inactive_list_active_card["expected_status_normalized"] == "active"
    assert dedup_manifest["document_code"] == "СП 20.13330.2016"
    assert dedup_manifest["equivalent_versions"] == ["version_a.txt", "version_b_equivalent.txt"]
    assert compute_version_content_hash(version_a) == compute_version_content_hash(version_b)
