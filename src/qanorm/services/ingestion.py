"""Ingestion service helpers."""

from __future__ import annotations

from typing import Any

from qanorm.crawler.discovery import discover_all_seeds
from qanorm.settings import get_settings


def check_configuration() -> dict[str, Any]:
    """Validate and summarize the current runtime configuration."""

    settings = get_settings()
    return {
        "status": "ok",
        "seed_count": len(settings.sources.seed_urls),
        "max_retries": settings.app.max_retries,
        "request_timeout_seconds": settings.app.request_timeout_seconds,
        "raw_storage_path": str(settings.env.raw_storage_path),
    }


def run_seed_crawl() -> dict[str, Any]:
    """Run first-page discovery across all configured seeds."""

    discovered = discover_all_seeds()
    total_list_pages = sum(len(item.page_urls) for item in discovered)
    total_document_cards = sum(len(item.entries) for item in discovered)
    total_jobs = sum(len(item.queued_jobs) for item in discovered)

    return {
        "status": "ok",
        "seed_count": len(discovered),
        "total_list_pages_discovered": total_list_pages,
        "total_document_cards_discovered_on_first_pages": total_document_cards,
        "queued_process_document_card_jobs": total_jobs,
        "seeds": [
            {
                "seed_url": item.seed_url,
                "first_page_document_count": len(item.entries),
                "total_section_pages_discovered": len(item.page_urls),
                "queued_jobs": len(item.queued_jobs),
            }
            for item in discovered
        ],
    }
