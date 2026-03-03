"""List page parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from urllib.parse import urljoin, urlparse

from lxml import html

from qanorm.utils.text import normalize_whitespace


_MEGA_DOC_PAGE_RE = re.compile(r"^(?P<prefix>.+_)(?P<page>\d+)\.html$")
_LIST2_PAGE_RE = re.compile(r"^(?P<prefix>.+-)(?P<page>\d+)\.htm$")


@dataclass(slots=True)
class ListPageEntry:
    """A normalized document row from a list page."""

    card_url: str
    document_code: str
    title: str
    status_raw: str


def detect_list_page_kind(list_page_url: str) -> str:
    """Detect the source-specific list page parser to use."""

    if "/mega_doc/" in list_page_url:
        return "mega_doc"
    if "/list2/" in list_page_url:
        return "list2"
    raise ValueError(f"Unsupported list page URL: {list_page_url}")


def extract_pagination_urls(list_page_url: str, page_html: str) -> list[str]:
    """Extract all pagination URLs for the current section, including the current page."""

    tree = html.fromstring(page_html)
    matcher = _MEGA_DOC_PAGE_RE if detect_list_page_kind(list_page_url) == "mega_doc" else _LIST2_PAGE_RE
    parsed = urlparse(list_page_url)
    page_name = PurePosixPath(parsed.path).name
    match = matcher.match(page_name)
    if not match:
        return [list_page_url]

    prefix = match.group("prefix")
    discovered: dict[int, str] = {int(match.group("page")): list_page_url}

    for href in tree.xpath("//a[@href]/@href"):
        absolute_url = urljoin(list_page_url, href)
        absolute_parsed = urlparse(absolute_url)
        if absolute_parsed.netloc != parsed.netloc:
            continue

        candidate_name = PurePosixPath(absolute_parsed.path).name
        candidate_match = matcher.match(candidate_name)
        if not candidate_match:
            continue
        if candidate_match.group("prefix") != prefix:
            continue

        discovered[int(candidate_match.group("page"))] = absolute_url

    return [discovered[page_number] for page_number in sorted(discovered)]


def parse_list_page(list_page_url: str, page_html: str) -> list[ListPageEntry]:
    """Parse a supported list page into normalized rows."""

    page_kind = detect_list_page_kind(list_page_url)
    if page_kind == "mega_doc":
        return parse_mega_doc_list_page(list_page_url, page_html)
    return parse_list2_list_page(list_page_url, page_html)


def parse_mega_doc_list_page(list_page_url: str, page_html: str) -> list[ListPageEntry]:
    """Parse a ``mega_doc`` list page."""

    tree = html.fromstring(page_html)
    entries: list[ListPageEntry] = []

    for row in tree.xpath("//div[contains(@class, 'table_doc')]//div[contains(@class, 'row')][not(contains(@class, 'header'))]"):
        cells = row.xpath("./div[contains(@class, 'cell')]")
        if len(cells) < 3:
            continue

        link_nodes = cells[1].xpath(".//a[@href]")
        if not link_nodes:
            continue

        card_url = urljoin(list_page_url, link_nodes[0].get("href"))
        anchor_text = normalize_whitespace(" ".join(link_nodes[0].itertext()))
        if not anchor_text:
            continue

        document_code, title = _split_mega_doc_anchor_text(anchor_text)
        status_raw = normalize_whitespace(" ".join(cells[2].itertext()))
        entries.append(
            ListPageEntry(
                card_url=card_url,
                document_code=document_code,
                title=title,
                status_raw=status_raw,
            )
        )

    return entries


def parse_list2_list_page(list_page_url: str, page_html: str) -> list[ListPageEntry]:
    """Parse a ``list2`` list page."""

    tree = html.fromstring(page_html)
    entries: list[ListPageEntry] = []

    for row in tree.xpath("//table[contains(@class, 'doctab1')]//tr[contains(@class, 'm3')]"):
        cells = row.xpath("./td")
        if len(cells) < 4:
            continue

        card_link = None
        for link in cells[0].xpath(".//a[@href]"):
            href = link.get("href", "")
            if "/Index" in href:
                card_link = link
                break

        if card_link is None:
            continue

        card_url = urljoin(list_page_url, card_link.get("href"))
        document_code = normalize_whitespace(" ".join(card_link.itertext()))
        title = normalize_whitespace(" ".join(cells[1].itertext()))
        status_raw = normalize_whitespace(" ".join(cells[3].itertext()))
        entries.append(
            ListPageEntry(
                card_url=card_url,
                document_code=document_code,
                title=title,
                status_raw=status_raw,
            )
        )

    return entries


def _split_mega_doc_anchor_text(anchor_text: str) -> tuple[str, str]:
    """Split the quoted mega_doc heading into ``code`` and ``title``."""

    normalized = normalize_whitespace(anchor_text)
    quoted_match = re.search(r'"([^"]+)"', normalized)
    first_line = quoted_match.group(1) if quoted_match else normalized.strip('"')
    if ". " not in first_line:
        return first_line, first_line

    document_code, title = first_line.split(". ", 1)
    return normalize_whitespace(document_code), normalize_whitespace(title)
