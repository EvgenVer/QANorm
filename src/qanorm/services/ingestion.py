"""Ingestion service helpers."""

from __future__ import annotations

from typing import Any

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
    """Return a dry-run summary for the seed crawl command."""

    settings = get_settings()
    return {
        "status": "queued",
        "message": "Seed crawl entrypoint is ready. Crawl implementation will be added in later blocks.",
        "seed_count": len(settings.sources.seed_urls),
        "seed_urls": settings.sources.seed_urls,
    }
