from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from qanorm.db.types import EvidenceSourceKind
from qanorm.fetchers.trusted_sources import (
    TrustedSourcePage,
    TrustedSourceRouter,
    TrustedSourceSearchCandidate,
    filter_trusted_search_results,
)
from qanorm.providers.searxng import SearXNGResult
from qanorm.services.qa.trusted_sources_service import (
    TrustedSourceSearchHit,
    cleanup_trusted_source_cache,
    normalize_trusted_hits_to_evidence,
    prefetch_trusted_sources,
    search_trusted_sources,
)
from qanorm.settings import TrustedSourceAdapterConfig


def _adapter(*, domain: str = "example.com", language: str = "en") -> TrustedSourceAdapterConfig:
    return TrustedSourceAdapterConfig(
        source_id=domain.replace(".", "_"),
        display_name=domain,
        domain=domain,
        base_url=f"https://{domain}",
        language=language,
        search={
            "mode": "site_query",
            "allowed_prefixes": [f"https://{domain}/docs/"],
            "blocked_prefixes": [f"https://{domain}/news/"],
            "query_hints": ["guidance"],
            "max_results": 3,
        },
        fetch={"timeout_seconds": 15, "max_pages_per_query": 2},
        extract={"strategy": "generic_article"},
        cache={"enabled": True, "search_ttl_hours": 24, "page_ttl_hours": 24, "extraction_ttl_hours": 24},
    )


def test_trusted_source_router_prioritizes_russian_source_for_cyrillic_query() -> None:
    router = TrustedSourceRouter([_adapter(domain="en.example.com", language="en"), _adapter(domain="ru.example.com", language="ru")])

    selected = router.select_sources(query_text="разъяснение по проектированию", allowed_domains=None)

    assert [item.domain for item in selected][:2] == ["ru.example.com", "en.example.com"]


def test_filter_trusted_search_results_respects_allowed_and_blocked_prefixes() -> None:
    source = _adapter()
    results = [
        SearXNGResult(title="Allowed", url="https://example.com/docs/guide", snippet="A", score=0.9),
        SearXNGResult(title="Binary", url="https://example.com/docs/guide.pdf", snippet="B", score=0.85),
        SearXNGResult(title="Blocked", url="https://example.com/news/item", snippet="B", score=0.8),
        SearXNGResult(title="Foreign", url="https://evil.test/docs/guide", snippet="C", score=0.7),
    ]

    filtered = filter_trusted_search_results(results, source=source)

    assert len(filtered) == 1
    assert filtered[0].url == "https://example.com/docs/guide"


def test_search_trusted_sources_uses_cache_and_returns_ranked_fragments(monkeypatch) -> None:
    calls = {"search": 0, "fetch": 0, "search_events": 0}
    stored_entries: dict[tuple[str, str], SimpleNamespace] = {}

    class _FakeCacheRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def get_valid(self, *, cache_kind: str, cache_key: str, now=None):
            entry = stored_entries.get((cache_kind, cache_key))
            if entry is not None and entry.expires_at > datetime.now(timezone.utc):
                return entry
            return None

        def upsert(self, **kwargs):
            entry = SimpleNamespace(**kwargs, last_accessed_at=datetime.now(timezone.utc))
            stored_entries[(kwargs["cache_kind"], kwargs["cache_key"])] = entry
            return entry

        def touch(self, entry, *, now=None):
            entry.last_accessed_at = now or datetime.now(timezone.utc)
            return entry

        def delete_expired(self, *, now=None):
            return 0

    class _FakeSearchEventRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def add(self, event):
            calls["search_events"] += 1
            return event

    async def _fake_search(**kwargs):
        calls["search"] += 1
        source = kwargs["source"]
        return [
            TrustedSourceSearchCandidate(
                source_id=source.source_id or source.domain,
                source_domain=source.domain,
                source_language=source.language,
                url=f"https://{source.domain}/docs/a",
                title="Guide",
                snippet="Engineering guidance",
                score=0.9,
            )
        ]

    def _fake_fetch(url, *, source, fetcher=None):
        calls["fetch"] += 1
        return TrustedSourcePage(
            source_id=source.source_id or source.domain,
            url=url,
            title="Guide",
            text="Engineering guidance. Additional design constraints. Safety requirement.",
            source_domain=source.domain,
            source_language=source.language,
            content_hash="hash-1",
        )

    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.TrustedSourceCacheEntryRepository", _FakeCacheRepository)
    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.SearchEventRepository", _FakeSearchEventRepository)
    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.search_trusted_source_urls", _fake_search)
    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.fetch_trusted_source_page", _fake_fetch)
    monkeypatch.setattr(
        "qanorm.services.qa.trusted_sources_service.get_settings",
        lambda: SimpleNamespace(trusted_sources=SimpleNamespace(sources=[_adapter()])),
    )

    session = SimpleNamespace(flush=lambda: None)
    first_hits = asyncio.run(
        search_trusted_sources(
            session,
            query_id=uuid4(),
            subtask_id=None,
            query_text="engineering guidance",
            allowed_domains=["example.com"],
            limit=3,
        )
    )
    second_hits = asyncio.run(
        search_trusted_sources(
            session,
            query_id=uuid4(),
            subtask_id=None,
            query_text="engineering guidance",
            allowed_domains=["example.com"],
            limit=3,
        )
    )

    assert first_hits
    assert second_hits
    assert calls["search"] == 1
    assert calls["fetch"] == 1
    assert any(hit.cache_hit for hit in second_hits)
    assert calls["search_events"] == 2


def test_prefetch_trusted_sources_returns_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "qanorm.services.qa.trusted_sources_service.search_trusted_sources",
        lambda *args, **kwargs: asyncio.sleep(0, result=[TrustedSourceSearchHit(
            source_id="example_com",
            source_domain="example.com",
            source_url="https://example.com/docs/a",
            title="Guide",
            locator="fragment:1",
            text="Guidance",
            language="en",
            score=1.0,
            cache_hit=True,
        )]),
    )
    monkeypatch.setattr(
        "qanorm.services.qa.trusted_sources_service.get_settings",
        lambda: SimpleNamespace(trusted_sources=SimpleNamespace(sources=[_adapter()])),
    )

    result = asyncio.run(prefetch_trusted_sources(SimpleNamespace(), query_text="guidance", allowed_domains=["example.com"]))

    assert result.source_count == 1
    assert result.hit_count == 1
    assert result.cache_hit_count == 1


def test_cleanup_trusted_source_cache_uses_repository(monkeypatch) -> None:
    monkeypatch.setattr(
        "qanorm.services.qa.trusted_sources_service.TrustedSourceCacheEntryRepository",
        lambda session: SimpleNamespace(delete_expired=lambda now=None: 3),
    )

    deleted = cleanup_trusted_source_cache(SimpleNamespace())

    assert deleted == 3


def test_normalize_trusted_hits_to_evidence_marks_external_verification() -> None:
    hit = TrustedSourceSearchHit(
        source_id="example_com",
        source_domain="example.com",
        source_url="https://example.com/doc",
        title="Doc",
        locator="fragment:1",
        text="Trusted guidance",
        language="en",
        score=0.9,
        cache_hit=False,
    )

    evidence = normalize_trusted_hits_to_evidence(query_id=uuid4(), hits=[hit])

    assert len(evidence) == 1
    assert evidence[0].source_kind == EvidenceSourceKind.TRUSTED_WEB
    assert evidence[0].requires_verification is True
