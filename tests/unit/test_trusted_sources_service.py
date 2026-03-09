from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.db.types import EvidenceSourceKind
from qanorm.fetchers.trusted_sources import TrustedSourcePage, discover_trusted_source_urls
from qanorm.services.qa.trusted_sources_service import (
    chunk_trusted_source_text,
    normalize_trusted_hits_to_evidence,
    search_trusted_sources,
    sync_trusted_source,
    TrustedSourceSearchHit,
)
from qanorm.settings import TrustedSourceAdapterConfig


def test_chunk_trusted_source_text_preserves_overlap_without_empty_chunks() -> None:
    chunks = chunk_trusted_source_text(
        " ".join(["alpha"] * 400),
        chunk_size_chars=120,
        chunk_overlap_chars=20,
    )

    assert len(chunks) >= 2
    assert all(chunk.strip() for chunk in chunks)
    assert len(chunks[0]) <= 120


def test_discover_trusted_source_urls_filters_domain_and_prefix(monkeypatch) -> None:
    sitemap_payload = """<?xml version="1.0"?><urlset><url><loc>https://example.com/docs/a</loc></url><url><loc>https://evil.test/x</loc></url></urlset>"""

    class _FakeFetcher:
        def get_html(self, url):
            return sitemap_payload

        def close(self):
            return None

    urls = discover_trusted_source_urls(
        domain="example.com",
        sitemap_urls=["https://example.com/sitemap.xml"],
        seed_urls=["https://example.com/docs/manual"],
        allowed_prefixes=["https://example.com/docs"],
        fetcher=_FakeFetcher(),
    )

    assert urls == ["https://example.com/docs/a", "https://example.com/docs/manual"]


def test_sync_trusted_source_saves_documents_and_chunks(monkeypatch) -> None:
    session = SimpleNamespace(flush=lambda: None)
    sync_run = SimpleNamespace(id=uuid4(), details_json={}, status=None, documents_discovered=0, documents_indexed=0)
    saved_document = SimpleNamespace(id=uuid4())
    replace_calls = []

    class _FakeRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def save_sync_run(self, run):
            return sync_run

        def save_document(self, document):
            return saved_document

        def replace_chunks(self, document_id, chunks):
            replace_calls.append((document_id, list(chunks)))
            return list(chunks)

    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.TrustedSourceRepository", _FakeRepository)
    monkeypatch.setattr(
        "qanorm.services.qa.trusted_sources_service.discover_trusted_source_urls",
        lambda **kwargs: ["https://example.com/docs/a"],
    )
    monkeypatch.setattr(
        "qanorm.services.qa.trusted_sources_service.fetch_trusted_source_page",
        lambda url: TrustedSourcePage(
            url=url,
            title="Doc",
            text="Paragraph one. " * 80,
            source_domain="example.com",
        ),
    )

    result = sync_trusted_source(
        session,
        adapter=TrustedSourceAdapterConfig(
            domain="example.com",
            sitemap_urls=["https://example.com/sitemap.xml"],
            seed_urls=[],
            allowed_prefixes=["https://example.com/docs"],
            max_documents_per_sync=5,
            chunk_size_chars=200,
            chunk_overlap_chars=20,
        ),
    )

    assert result.discovered_url_count == 1
    assert result.indexed_document_count == 1
    assert replace_calls


def test_search_trusted_sources_records_search_event() -> None:
    query_id = uuid4()
    chunk = SimpleNamespace(id=uuid4(), locator="chunk:1", text="Trusted engineering guidance.")
    document = SimpleNamespace(id=uuid4(), source_domain="example.com", source_url="https://example.com/doc", title="Doc")
    session = SimpleNamespace(
        execute=lambda stmt: SimpleNamespace(all=lambda: [(chunk, document, 0.8)]),
        add=lambda item: None,
        flush=lambda: None,
    )

    hits = search_trusted_sources(
        session,
        query_id=query_id,
        subtask_id=None,
        query_text="engineering guidance",
        allowed_domains=["example.com"],
        limit=5,
    )

    assert len(hits) == 1
    assert hits[0].source_domain == "example.com"


def test_normalize_trusted_hits_to_evidence_marks_external_verification() -> None:
    hit = TrustedSourceSearchHit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        source_domain="example.com",
        source_url="https://example.com/doc",
        title="Doc",
        locator="chunk:1",
        text="Trusted guidance",
        score=0.9,
    )

    evidence = normalize_trusted_hits_to_evidence(query_id=uuid4(), hits=[hit])

    assert len(evidence) == 1
    assert evidence[0].source_kind == EvidenceSourceKind.TRUSTED_WEB
    assert evidence[0].requires_verification is True
