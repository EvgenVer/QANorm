"""Ingestion service helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from qanorm.crawler.list_pages import crawl_seed_first_page
from qanorm.db.session import session_scope
from qanorm.db.types import JobType
from qanorm.fetchers.html import fetch_html_document
from qanorm.jobs.scheduler import create_job
from qanorm.logging import get_ingestion_logger
from qanorm.parsers.list_parser import parse_list_page
from qanorm.repositories import IngestionJobRepository
from qanorm.settings import get_settings


@dataclass(slots=True)
class SeedJobQueueResult:
    """Summary of queued crawl-seed jobs."""

    status: str
    seed_count: int
    queued_job_count: int
    queued_job_ids: list[str]


@dataclass(slots=True)
class CrawlSeedJobResult:
    """Summary of a processed ``crawl_seed`` job."""

    status: str
    seed_url: str
    discovered_page_count: int
    queued_parse_jobs: int
    queued_job_ids: list[str]


@dataclass(slots=True)
class ParseListPageJobResult:
    """Summary of a processed ``parse_list_page`` job."""

    status: str
    list_page_url: str
    discovered_entry_count: int
    queued_card_jobs: int
    queued_job_ids: list[str]


logger = get_ingestion_logger()


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
    """Queue ``crawl_seed`` jobs for all configured seed URLs."""

    with session_scope() as session:
        result = queue_seed_crawl_jobs(session)
    logger.info(
        "Queued %s crawl_seed job(s) for %s configured seed(s)",
        result.queued_job_count,
        result.seed_count,
    )
    return asdict(result)


def queue_seed_crawl_jobs(
    session: Any,
    *,
    seed_urls: list[str] | None = None,
) -> SeedJobQueueResult:
    """Queue crawl jobs for the configured or provided seeds."""

    configured_seed_urls = list(seed_urls if seed_urls is not None else get_settings().sources.seed_urls)
    job_repository = IngestionJobRepository(session)
    queued_jobs = [
        create_job(
            job_repository,
            job_type=JobType.CRAWL_SEED,
            payload={"seed_url": seed_url},
        )
        for seed_url in configured_seed_urls
    ]
    logger.info(
        "Prepared crawl_seed queue: %s requested seed(s), %s queued job(s)",
        len(configured_seed_urls),
        len(queued_jobs),
    )
    return SeedJobQueueResult(
        status="ok",
        seed_count=len(configured_seed_urls),
        queued_job_count=len(queued_jobs),
        queued_job_ids=[str(job.id) for job in queued_jobs],
    )


def process_crawl_seed_job(
    session: Any,
    *,
    seed_url: str,
) -> CrawlSeedJobResult:
    """Process one ``crawl_seed`` job and queue ``parse_list_page`` jobs."""

    snapshot = crawl_seed_first_page(seed_url)
    job_repository = IngestionJobRepository(session)
    queued_jobs = [
        create_job(
            job_repository,
            job_type=JobType.PARSE_LIST_PAGE,
            payload={
                "list_page_url": page_url,
                "seed_url": seed_url,
            },
        )
        for page_url in snapshot.page_urls
    ]
    logger.info(
        "Processed crawl_seed for %s: discovered %s list page(s), queued %s parse_list_page job(s)",
        seed_url,
        len(snapshot.page_urls),
        len(queued_jobs),
    )
    return CrawlSeedJobResult(
        status="ok",
        seed_url=seed_url,
        discovered_page_count=len(snapshot.page_urls),
        queued_parse_jobs=len(queued_jobs),
        queued_job_ids=[str(job.id) for job in queued_jobs],
    )


def process_parse_list_page_job(
    session: Any,
    *,
    list_page_url: str,
    seed_url: str | None = None,
) -> ParseListPageJobResult:
    """Process one ``parse_list_page`` job and queue document-card jobs."""

    page_html = fetch_html_document(list_page_url)
    entries = parse_list_page(list_page_url, page_html)
    job_repository = IngestionJobRepository(session)
    queued_jobs = [
        create_job(
            job_repository,
            job_type=JobType.PROCESS_DOCUMENT_CARD,
            payload={
                "card_url": entry.card_url,
                "list_page_url": list_page_url,
                "list_status_raw": entry.status_raw,
                "seed_url": seed_url,
            },
        )
        for entry in entries
    ]
    logger.info(
        "Processed parse_list_page for %s: discovered %s card(s), queued %s process_document_card job(s)",
        list_page_url,
        len(entries),
        len(queued_jobs),
    )

    return ParseListPageJobResult(
        status="ok",
        list_page_url=list_page_url,
        discovered_entry_count=len(entries),
        queued_card_jobs=len(queued_jobs),
        queued_job_ids=[str(job.id) for job in queued_jobs],
    )
