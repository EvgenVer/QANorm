from __future__ import annotations

import asyncio
from uuid import uuid4

from qanorm.db.types import EvidenceSourceKind
from qanorm.providers.searxng import SearXNGResult
from qanorm.services.qa.open_web_service import (
    fetch_open_web_document,
    normalize_open_web_results_to_evidence,
    sanitize_html_to_text,
    search_open_web,
)


def test_sanitize_html_to_text_drops_active_tags() -> None:
    title, text = sanitize_html_to_text(
        "<html><head><title>Doc</title><script>alert(1)</script></head><body><p>Hello</p><style>x</style><p>world</p></body></html>"
    )

    assert title == "Doc"
    assert text == "Hello world"


def test_fetch_open_web_document_uses_html_fetcher(monkeypatch) -> None:
    monkeypatch.setattr(
        "qanorm.services.qa.open_web_service.fetch_html_document",
        lambda url: "<html><head><title>Doc</title></head><body><p>Hello world</p></body></html>",
    )

    document = fetch_open_web_document("https://example.com/doc")

    assert document.source_domain == "example.com"
    assert document.title == "Doc"
    assert document.text == "Hello world"


def test_search_open_web_records_search_event() -> None:
    class _FakeProvider:
        provider_name = "searxng"

        async def search(self, *, query_text, limit, allowed_domains=None):
            return [
                SearXNGResult(
                    title="Doc",
                    url="https://example.com/doc",
                    snippet="Snippet",
                    engine="test",
                    score=0.7,
                )
            ]

    session = type(
        "_Session",
        (),
        {
            "add": lambda self, item: None,
            "flush": lambda self: None,
        },
    )()

    results = asyncio.run(
        search_open_web(
            session,
            query_id=uuid4(),
            subtask_id=None,
            query_text="fire safety",
            allowed_domains=["example.com"],
            limit=5,
            provider=_FakeProvider(),
        )
    )

    assert len(results) == 1
    assert results[0].url == "https://example.com/doc"


def test_normalize_open_web_results_to_evidence_fetches_pages(monkeypatch) -> None:
    monkeypatch.setattr(
        "qanorm.services.qa.open_web_service.fetch_open_web_document",
        lambda url: type(
            "_Doc",
            (),
            {
                "source_url": url,
                "source_domain": "example.com",
                "title": "Doc",
                "text": "External engineering guidance.",
            },
        )(),
    )
    results = [
        SearXNGResult(
            title="Doc",
            url="https://example.com/doc",
            snippet="Snippet",
            score=0.8,
        )
    ]

    evidence = normalize_open_web_results_to_evidence(query_id=uuid4(), results=results)

    assert len(evidence) == 1
    assert evidence[0].source_kind == EvidenceSourceKind.OPEN_WEB
    assert evidence[0].requires_verification is True
