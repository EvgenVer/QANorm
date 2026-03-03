"""Document discovery workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qanorm.crawler.list_pages import SeedListPageSnapshot, crawl_seed_first_page
from qanorm.crawler.seeds import load_seed_urls
from qanorm.db.types import JobType
from qanorm.fetchers.http import HttpFetcher
from qanorm.parsers.list_parser import ListPageEntry


@dataclass(slots=True)
class SeedDiscoveryResult:
    """Discovery summary for one seed."""

    seed_url: str
    page_urls: list[str]
    entries: list[ListPageEntry]
    queued_jobs: list[dict[str, Any]]


def build_process_document_card_jobs(
    entries: list[ListPageEntry],
    *,
    source_list_url: str,
    seen_card_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build unique ``process_document_card`` job specs for parsed list entries."""

    seen = seen_card_urls if seen_card_urls is not None else set()
    jobs: list[dict[str, Any]] = []

    for entry in entries:
        if entry.card_url in seen:
            continue

        seen.add(entry.card_url)
        jobs.append(
            {
                "job_type": JobType.PROCESS_DOCUMENT_CARD.value,
                "payload": {
                    "card_url": entry.card_url,
                    "source_list_url": source_list_url,
                    "list_document_code": entry.document_code,
                    "list_title": entry.title,
                    "list_status_raw": entry.status_raw,
                },
            }
        )

    return jobs


def discover_seed(seed_url: str, fetcher: HttpFetcher | None = None) -> SeedDiscoveryResult:
    """Run first-page discovery for a single seed."""

    snapshot = crawl_seed_first_page(seed_url, fetcher=fetcher)
    queued_jobs = build_process_document_card_jobs(
        snapshot.entries,
        source_list_url=snapshot.seed_url,
    )
    return SeedDiscoveryResult(
        seed_url=snapshot.seed_url,
        page_urls=snapshot.page_urls,
        entries=snapshot.entries,
        queued_jobs=queued_jobs,
    )


def discover_all_seeds(
    seed_urls: list[str] | None = None,
    fetcher: HttpFetcher | None = None,
) -> list[SeedDiscoveryResult]:
    """Run first-page discovery for all configured seeds."""

    discovered: list[SeedDiscoveryResult] = []
    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher()

    try:
        for seed_url in seed_urls or load_seed_urls():
            discovered.append(discover_seed(seed_url, fetcher=fetcher))
    finally:
        if owned_fetcher:
            fetcher.close()

    return discovered
