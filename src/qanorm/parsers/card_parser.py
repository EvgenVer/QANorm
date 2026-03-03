"""Document card parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from urllib.parse import urljoin

from lxml import html

from qanorm.fetchers.html import fetch_html_document
from qanorm.utils.dates import parse_date_string
from qanorm.utils.text import normalize_whitespace


_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_TITLE_LINE_RE = re.compile(r'"([^"]+)"')


@dataclass(slots=True)
class DocumentCardData:
    """Normalized metadata extracted from a document card."""

    card_url: str
    source_type: str
    source_list_status_raw: str | None
    card_status_raw: str | None
    document_code: str
    document_title: str
    text_actualized_at: date | None
    description_actualized_at: date | None
    published_at: date | None
    effective_from: date | None
    scope_text: str | None
    normative_references: list[str]
    pdf_url: str | None
    html_url: str | None
    print_url: str | None
    has_full_html: bool
    has_page_images: bool
    edition_label: str | None


def fetch_document_card(card_url: str) -> str:
    """Load a document card by URL."""

    return fetch_html_document(card_url)


def extract_card_page_image_urls(card_url: str, page_html: str) -> list[str]:
    """Extract absolute page image URLs from a card page."""

    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(page_html.encode("utf-8"), parser=parser)
    image_urls: list[str] = []
    for raw_src in tree.xpath("//img[contains(@class, 'img2')]/@src"):
        absolute_url = urljoin(card_url, raw_src)
        if absolute_url not in image_urls:
            image_urls.append(absolute_url)
    return image_urls


def parse_document_card(
    card_url: str,
    page_html: str,
    *,
    source_list_status_raw: str | None = None,
) -> DocumentCardData:
    """Parse a supported document card into normalized metadata."""

    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(page_html.encode("utf-8"), parser=parser)
    if "/Index" in card_url:
        return _parse_index_card(tree, card_url, source_list_status_raw=source_list_status_raw)
    if "/mega_doc/" in card_url:
        return _parse_mega_doc_card(tree, card_url, source_list_status_raw=source_list_status_raw)
    raise ValueError(f"Unsupported card URL: {card_url}")


def _parse_index_card(tree: html.HtmlElement, card_url: str, *, source_list_status_raw: str | None) -> DocumentCardData:
    metadata = _extract_index_metadata(tree)
    document_code = metadata.get("Обозначение") or _extract_h2_document_code(tree)
    document_title = metadata.get("Название рус.") or _extract_h3_title(tree)
    card_status_raw = metadata.get("Статус")
    description_actualized_at = _parse_optional_date(
        metadata.get("Дата актуализации") or _extract_h3_actualized_date(tree)
    )
    effective_from = _parse_optional_date(metadata.get("Дата введения"))
    published_at = _extract_index_published_at(metadata.get("Издан"))
    scope_text = metadata.get("Область применения")
    normative_references = _extract_index_normative_references(tree)
    pdf_url = _extract_link_by_title(tree, "PDF", card_url)
    html_url = _extract_link_by_title(tree, "HTML", card_url)
    print_url = _extract_print_url(tree, card_url)
    edition_label = (
        f"актуализация от {description_actualized_at.isoformat()}" if description_actualized_at else None
    )

    return DocumentCardData(
        card_url=card_url,
        source_type="index_card",
        source_list_status_raw=source_list_status_raw,
        card_status_raw=card_status_raw,
        document_code=document_code,
        document_title=document_title,
        text_actualized_at=description_actualized_at,
        description_actualized_at=description_actualized_at,
        published_at=published_at,
        effective_from=effective_from,
        scope_text=scope_text,
        normative_references=normative_references,
        pdf_url=pdf_url,
        html_url=html_url,
        print_url=print_url,
        has_full_html=html_url is not None,
        has_page_images=bool(tree.xpath("//img[contains(@class, 'img2')]")),
        edition_label=edition_label,
    )


def _parse_mega_doc_card(
    tree: html.HtmlElement,
    card_url: str,
    *,
    source_list_status_raw: str | None,
) -> DocumentCardData:
    title_text = normalize_whitespace(" ".join(tree.xpath("//title/text()")))
    document_code, document_title = _split_title_header(title_text)
    actualized = _parse_optional_date(_search_first_date_after_marker(title_text, "ред. от"))
    effective_from = _parse_optional_date(_search_first_date_after_marker(title_text, "введен"))
    page_text = normalize_whitespace(" ".join(tree.xpath("//body//text()")))
    published_at = _extract_mega_doc_published_at(page_text)
    scope_text = _extract_mega_doc_scope(tree)
    normative_references = _extract_mega_doc_references(tree)
    edition_label = f"ред. от {actualized.isoformat()}" if actualized else None

    return DocumentCardData(
        card_url=card_url,
        source_type="mega_doc_card",
        source_list_status_raw=source_list_status_raw,
        card_status_raw=None,
        document_code=document_code,
        document_title=document_title,
        text_actualized_at=actualized,
        description_actualized_at=actualized,
        published_at=published_at,
        effective_from=effective_from,
        scope_text=scope_text,
        normative_references=normative_references,
        pdf_url=None,
        html_url=card_url,
        print_url=None,
        has_full_html=True,
        has_page_images=False,
        edition_label=edition_label,
    )


def _extract_index_metadata(tree: html.HtmlElement) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for row in tree.xpath("//table[contains(@class, 'doctab2')]//tr"):
        label_nodes = row.xpath("./td[1]//b/text()")
        value_nodes = row.xpath("./td[2]")
        if not label_nodes or not value_nodes:
            continue

        label = normalize_whitespace(label_nodes[0]).rstrip(":")
        value_cell = value_nodes[0]
        if label == "Обозначение":
            link_text = value_cell.xpath(".//a[contains(@class, 'a1')]/text()")
            value = normalize_whitespace(link_text[0]) if link_text else normalize_whitespace(" ".join(value_cell.itertext()))
        elif label == "Нормативные ссылки":
            references = [normalize_whitespace(" ".join(link.itertext())) for link in value_cell.xpath(".//li//a[last()]")]
            value = "; ".join(item for item in references if item)
        else:
            value = normalize_whitespace(" ".join(value_cell.itertext()))
        metadata[label] = value
    return metadata


def _extract_h2_document_code(tree: html.HtmlElement) -> str:
    headings = tree.xpath("//h2")
    for heading in headings:
        text = normalize_whitespace(" ".join(heading.itertext()))
        if text and not text.startswith("Скачать "):
            return text
    fallback = normalize_whitespace(" ".join(tree.xpath("//h2[1]//text()")))
    return fallback.removeprefix("Скачать ").strip()


def _extract_h3_title(tree: html.HtmlElement) -> str:
    titles = [normalize_whitespace(" ".join(node.itertext())) for node in tree.xpath("//h3")]
    for title in titles:
        if title and not title.startswith("Дата актуализации"):
            return title
    return ""


def _extract_h3_actualized_date(tree: html.HtmlElement) -> str | None:
    headings = [normalize_whitespace(" ".join(node.itertext())) for node in tree.xpath("//h3")]
    for heading in headings:
        if heading.startswith("Дата актуализации"):
            return _extract_first_date(heading)
    return None


def _extract_index_published_at(value: str | None) -> date | None:
    if not value:
        return None
    return _parse_optional_date(_extract_first_date(value))


def _extract_index_normative_references(tree: html.HtmlElement) -> list[str]:
    references = []
    for link in tree.xpath("//table[contains(@class, 'doctab2')]//tr[./td[1]//b[contains(., 'Нормативные ссылки')]]//li//a[last()]"):
        text = normalize_whitespace(" ".join(link.itertext()))
        if text:
            references.append(text)
    return references


def _extract_link_by_title(tree: html.HtmlElement, title: str, base_url: str) -> str | None:
    links = tree.xpath(f"//a[@href][translate(@title, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')='{title.upper()}']/@href")
    if not links:
        return None
    return urljoin(base_url, links[0])


def _extract_print_url(tree: html.HtmlElement, base_url: str) -> str | None:
    links = tree.xpath("//a[@href][contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'печати')]/@href")
    if not links:
        return None
    return urljoin(base_url, links[0])


def _split_title_header(title_text: str) -> tuple[str, str]:
    title_text = normalize_whitespace(title_text)
    match = _TITLE_LINE_RE.search(title_text)
    header = match.group(1) if match else title_text
    if ". " not in header:
        return header, header
    document_code, document_title = header.split(". ", 1)
    return normalize_whitespace(document_code), normalize_whitespace(document_title)


def _search_first_date_after_marker(value: str, marker: str) -> str | None:
    lowered = value.lower()
    index = lowered.find(marker.lower())
    if index < 0:
        return None
    return _extract_first_date(value[index:])


def _extract_first_date(value: str) -> str | None:
    match = _DATE_RE.search(value)
    return match.group(1) if match else None


def _parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return parse_date_string(value)
    except ValueError:
        return None


def _extract_mega_doc_published_at(page_text: str) -> date | None:
    if "Первоначальный текст документа опубликован в издании" not in page_text:
        return None
    year_match = _YEAR_RE.search(page_text)
    if not year_match:
        return None
    return date(int(year_match.group(0)), 1, 1)


def _extract_mega_doc_scope(tree: html.HtmlElement) -> str | None:
    section_headers = tree.xpath("//span[contains(@class, 's4') or contains(@class, 's5')]/text()")
    for index, header in enumerate(section_headers):
        normalized = normalize_whitespace(header)
        if normalized.startswith("1 ") and "Область применения" in normalized:
            following = tree.xpath("(//span[contains(@class, 's2') or contains(@class, 's3')])[1]//text()")
            value = normalize_whitespace(" ".join(following))
            return value or None
    return None


def _extract_mega_doc_references(tree: html.HtmlElement) -> list[str]:
    references: list[str] = []
    for link in tree.xpath("//body//a[@href and @title]")[:25]:
        title = normalize_whitespace(link.get("title", ""))
        text = normalize_whitespace(" ".join(link.itertext()))
        value = title or text
        if value and value not in references:
            references.append(value)
    return references
