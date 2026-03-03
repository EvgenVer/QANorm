"""HTML download helpers."""

from __future__ import annotations

from qanorm.fetchers.http import HttpFetcher


def fetch_html_document(url: str, fetcher: HttpFetcher | None = None) -> str:
    """Fetch an HTML document using the shared HTTP wrapper."""

    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher()
    try:
        return fetcher.get_html(url)
    finally:
        if owned_fetcher:
            fetcher.close()
