"""List page crawling helpers."""

from __future__ import annotations

from dataclasses import dataclass

from qanorm.fetchers.http import HttpFetcher
from qanorm.parsers.list_parser import ListPageEntry, extract_pagination_urls, parse_list_page


@dataclass(slots=True)
class SeedListPageSnapshot:
    """The parsed first-page snapshot for a single seed."""

    seed_url: str
    page_urls: list[str]
    entries: list[ListPageEntry]


def crawl_seed_first_page(seed_url: str, fetcher: HttpFetcher | None = None) -> SeedListPageSnapshot:
    """Fetch and parse the first page for a configured seed."""

    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher()
    try:
        page_html = fetcher.get_html(seed_url)
    finally:
        if owned_fetcher:
            fetcher.close()

    return SeedListPageSnapshot(
        seed_url=seed_url,
        page_urls=extract_pagination_urls(seed_url, page_html),
        entries=parse_list_page(seed_url, page_html),
    )
