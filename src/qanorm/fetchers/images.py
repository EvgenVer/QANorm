"""Image download helpers."""

from __future__ import annotations

from qanorm.fetchers.http import HttpFetcher


def fetch_image_bytes(url: str, fetcher: HttpFetcher | None = None) -> bytes:
    """Fetch an image using the shared HTTP wrapper."""

    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher()
    try:
        return fetcher.get_bytes(url)
    finally:
        if owned_fetcher:
            fetcher.close()
