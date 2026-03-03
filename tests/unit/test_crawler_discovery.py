from __future__ import annotations

from typing import Any

import httpx

from qanorm.crawler.discovery import build_process_document_card_jobs, discover_seed
from qanorm.crawler.list_pages import crawl_seed_first_page
from qanorm.crawler.seeds import load_seed_urls
from qanorm.fetchers.http import HttpFetcher
from qanorm.parsers.list_parser import ListPageEntry
from qanorm.settings import SourcesConfig


TEST_SEED_HTML = """
<div class="div_linc_top">
  <div class="div_linc_top_in_activ">[1]</div>
  <div class="div_linc_top_in"><a href="./sp_svod-pravil_1.html">2</a></div>
</div>
<div class="table_doc">
  <div class="row header"><div class="cell">№</div><div class="cell">Наименование</div><div class="cell">Статус</div></div>
  <div class="row">
    <div class="cell">1</div>
    <div class="cell"><a href="../../../mega_doc/norm/pravila/0/doc_1.html">"СП 20.13330.2016. Нагрузки и воздействия"</a></div>
    <div class="cell">действует</div>
  </div>
  <div class="row">
    <div class="cell">2</div>
    <div class="cell"><a href="../../../mega_doc/norm/pravila/0/doc_1.html">"СП 20.13330.2016. Нагрузки и воздействия"</a></div>
    <div class="cell">действует</div>
  </div>
</div>
"""


def test_load_seed_urls_returns_configured_urls() -> None:
    seed_urls = load_seed_urls(config=SourcesConfig(seed_urls=["https://example.test/one", "https://example.test/two"]))

    assert seed_urls == ["https://example.test/one", "https://example.test/two"]


def test_build_process_document_card_jobs_deduplicates_by_card_url() -> None:
    entries = [
        ListPageEntry(
            card_url="https://example.test/card/1",
            document_code="SP 1.0",
            title="Doc 1",
            status_raw="действует",
        ),
        ListPageEntry(
            card_url="https://example.test/card/1",
            document_code="SP 1.0",
            title="Doc 1 duplicate",
            status_raw="действует",
        ),
    ]

    jobs = build_process_document_card_jobs(entries, source_list_url="https://example.test/list")

    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "process_document_card"
    assert jobs[0]["payload"]["card_url"] == "https://example.test/card/1"


def test_crawl_seed_first_page_fetches_and_parses_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=TEST_SEED_HTML)

    fetcher = HttpFetcher(transport=httpx.MockTransport(handler))
    try:
        snapshot = crawl_seed_first_page(
            "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
            fetcher=fetcher,
        )
    finally:
        fetcher.close()

    assert len(snapshot.page_urls) == 2
    assert len(snapshot.entries) == 2


def test_discover_seed_builds_unique_job_specs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=TEST_SEED_HTML)

    fetcher = HttpFetcher(transport=httpx.MockTransport(handler))
    try:
        result = discover_seed(
            "https://meganorm.ru/mega_doc/norm/sp_svod-pravil/sp_svod-pravil_0.html",
            fetcher=fetcher,
        )
    finally:
        fetcher.close()

    assert len(result.entries) == 2
    assert len(result.queued_jobs) == 1
