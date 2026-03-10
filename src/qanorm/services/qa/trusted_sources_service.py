"""Online trusted-source retrieval with bounded shared cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.db.types import EvidenceSourceKind, SearchScope, SearchStatus
from qanorm.fetchers.trusted_sources import (
    TrustedSourcePage,
    TrustedSourceRouter,
    TrustedSourceSearchCandidate,
    build_cache_key,
    fetch_trusted_source_page,
    fragment_trusted_source_text,
    search_trusted_source_urls,
)
from qanorm.models import QAEvidence, SearchEvent
from qanorm.providers.searxng import SearXNGProvider
from qanorm.repositories import SearchEventRepository, TrustedSourceCacheEntryRepository
from qanorm.settings import TrustedSourceAdapterConfig, get_settings
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True, frozen=True)
class TrustedSourceSearchHit:
    """One normalized trusted-source evidence fragment."""

    source_id: str
    source_domain: str
    source_url: str
    title: str | None
    locator: str | None
    text: str
    language: str
    score: float
    cache_hit: bool


@dataclass(slots=True, frozen=True)
class TrustedSourcePrefetchResult:
    """Summary of one cache prefetch request."""

    source_count: int
    hit_count: int
    cache_hit_count: int


async def search_trusted_sources(
    session: Session,
    *,
    query_id: UUID | None,
    subtask_id: UUID | None,
    query_text: str,
    allowed_domains: Iterable[str] | None = None,
    limit: int = 5,
    router: TrustedSourceRouter | None = None,
    provider: SearXNGProvider | None = None,
) -> list[TrustedSourceSearchHit]:
    """Run online trusted-source retrieval and persist one audited search event."""

    settings = get_settings()
    cache_repository = TrustedSourceCacheEntryRepository(session)
    provider = provider or SearXNGProvider()
    router = router or TrustedSourceRouter(settings.trusted_sources.sources)
    domains = [item.strip() for item in (allowed_domains or []) if item.strip()]
    sources = router.select_sources(query_text=query_text, allowed_domains=domains)
    hits: list[TrustedSourceSearchHit] = []
    cache_hit_count = 0

    try:
        for source in sources:
            source_hits, source_cache_hits = await _search_one_source(
                session,
                query_text=query_text,
                source=source,
                limit=limit,
                provider=provider,
                cache_repository=cache_repository,
            )
            hits.extend(source_hits)
            cache_hit_count += source_cache_hits
    except Exception:
        SearchEventRepository(session).add(
            SearchEvent(
                query_id=query_id,
                subtask_id=subtask_id,
                provider_name="trusted_source_online",
                search_scope=SearchScope.TRUSTED_WEB,
                query_text=query_text,
                allowed_domains=domains or None,
                result_count=0,
                status=SearchStatus.FAILED,
            )
        )
        raise

    ordered_hits = sorted(hits, key=lambda item: item.score, reverse=True)[:limit]
    SearchEventRepository(session).add(
        SearchEvent(
            query_id=query_id,
            subtask_id=subtask_id,
            provider_name="trusted_source_online",
            search_scope=SearchScope.TRUSTED_WEB,
            query_text=query_text,
            allowed_domains=domains or None,
            result_count=len(ordered_hits),
            status=SearchStatus.COMPLETED,
        )
    )
    if cache_hit_count:
        session.flush()
    return ordered_hits


async def prefetch_trusted_sources(
    session: Session,
    *,
    query_text: str,
    allowed_domains: Iterable[str] | None = None,
    limit: int = 5,
) -> TrustedSourcePrefetchResult:
    """Warm trusted-source cache entries for a query without persisting evidence."""

    router = TrustedSourceRouter(get_settings().trusted_sources.sources)
    sources = router.select_sources(query_text=query_text, allowed_domains=allowed_domains)
    hits = await search_trusted_sources(
        session,
        query_id=None,
        subtask_id=None,
        query_text=query_text,
        allowed_domains=allowed_domains,
        limit=limit,
        router=router,
    )
    cache_count = sum(1 for hit in hits if hit.cache_hit)
    return TrustedSourcePrefetchResult(
        source_count=len(sources),
        hit_count=len(hits),
        cache_hit_count=cache_count,
    )


def cleanup_trusted_source_cache(session: Session) -> int:
    """Delete expired trusted-source cache entries."""

    return TrustedSourceCacheEntryRepository(session).delete_expired()


def normalize_trusted_hits_to_evidence(
    *,
    query_id: UUID,
    hits: Iterable[TrustedSourceSearchHit],
    subtask_id: UUID | None = None,
) -> list[QAEvidence]:
    """Convert trusted-source hits into external evidence rows."""

    evidence_rows: list[QAEvidence] = []
    for hit in hits:
        evidence_rows.append(
            QAEvidence(
                query_id=query_id,
                subtask_id=subtask_id,
                source_kind=EvidenceSourceKind.TRUSTED_WEB,
                source_url=hit.source_url,
                source_domain=hit.source_domain,
                # Preserve the page title as the primary display label and keep the
                # fragment marker as a secondary locator for the UI.
                locator=hit.title,
                locator_end=hit.locator,
                quote=hit.text[:500],
                chunk_text=hit.text,
                relevance_score=hit.score,
                is_normative=False,
                requires_verification=True,
            )
        )
    return evidence_rows


async def _search_one_source(
    session: Session,
    *,
    query_text: str,
    source: TrustedSourceAdapterConfig,
    limit: int,
    provider: SearXNGProvider,
    cache_repository: TrustedSourceCacheEntryRepository,
) -> tuple[list[TrustedSourceSearchHit], int]:
    """Search one trusted source end-to-end and return ranked evidence fragments."""

    search_candidates, search_cache_hit = await _load_search_candidates(
        source=source,
        query_text=query_text,
        provider=provider,
        cache_repository=cache_repository,
    )
    hits: list[TrustedSourceSearchHit] = []
    cache_hit_count = 1 if search_cache_hit else 0

    for candidate in search_candidates[: source.fetch.max_pages_per_query]:
        try:
            page, page_cache_hit = _load_page(candidate=candidate, source=source, cache_repository=cache_repository)
            fragments, fragments_cache_hit = _load_fragments(page=page, source=source, cache_repository=cache_repository)
        except Exception:
            # Skip slow, unsupported, or malformed pages so one bad trusted URL does
            # not block the whole answer path.
            continue
        cache_hit_count += int(page_cache_hit) + int(fragments_cache_hit)
        for index, fragment in enumerate(fragments[:2]):
            score = _score_fragment(query_text=query_text, fragment=fragment, base_score=candidate.score)
            hits.append(
                TrustedSourceSearchHit(
                    source_id=source.source_id or source.domain,
                    source_domain=source.domain,
                    source_url=page.url,
                    title=page.title or candidate.title,
                    locator=f"fragment:{index + 1}",
                    text=fragment,
                    language=page.source_language,
                    score=score,
                    cache_hit=search_cache_hit or page_cache_hit or fragments_cache_hit,
                )
            )

    ordered_hits = sorted(hits, key=lambda item: item.score, reverse=True)[:limit]
    return ordered_hits, cache_hit_count


async def _load_search_candidates(
    *,
    source: TrustedSourceAdapterConfig,
    query_text: str,
    provider: SearXNGProvider,
    cache_repository: TrustedSourceCacheEntryRepository,
) -> tuple[list[TrustedSourceSearchCandidate], bool]:
    """Load trusted search candidates from cache or the online provider."""

    cache_key = build_cache_key("search", source.source_id or source.domain, query_text, *source.search.query_hints)
    cached = cache_repository.get_valid(cache_kind="search_result", cache_key=cache_key)
    if cached is not None:
        cache_repository.touch(cached)
        return [_candidate_from_payload(item) for item in list(cached.payload_json)], True

    candidates = await search_trusted_source_urls(query_text=query_text, source=source, provider=provider)
    cache_repository.upsert(
        cache_kind="search_result",
        source_id=source.source_id or source.domain,
        source_domain=source.domain,
        cache_key=cache_key,
        payload_json=[_serialize_candidate(item) for item in candidates],
        expires_at=_expires_after(hours=source.cache.search_ttl_hours),
    )
    return candidates, False


def _load_page(
    *,
    candidate: TrustedSourceSearchCandidate,
    source: TrustedSourceAdapterConfig,
    cache_repository: TrustedSourceCacheEntryRepository,
) -> tuple[TrustedSourcePage, bool]:
    """Load one trusted page from cache or from the source website."""

    cache_key = build_cache_key("page", candidate.url)
    cached = cache_repository.get_valid(cache_kind="page", cache_key=cache_key)
    if cached is not None:
        cache_repository.touch(cached)
        return _page_from_payload(dict(cached.payload_json)), True

    page = fetch_trusted_source_page(candidate.url, source=source)
    cache_repository.upsert(
        cache_kind="page",
        source_id=source.source_id or source.domain,
        source_domain=source.domain,
        cache_key=cache_key,
        source_url=page.url,
        content_hash=page.content_hash,
        payload_json=_serialize_page(page),
        expires_at=_expires_after(hours=source.cache.page_ttl_hours),
    )
    return page, False


def _load_fragments(
    *,
    page: TrustedSourcePage,
    source: TrustedSourceAdapterConfig,
    cache_repository: TrustedSourceCacheEntryRepository,
) -> tuple[list[str], bool]:
    """Load extracted fragments from cache or split them from page text."""

    cache_key = build_cache_key("extraction", page.url, page.content_hash)
    cached = cache_repository.get_valid(cache_kind="extraction", cache_key=cache_key)
    if cached is not None:
        cache_repository.touch(cached)
        return [str(item) for item in list(cached.payload_json)], True

    fragments = fragment_trusted_source_text(page.text)
    cache_repository.upsert(
        cache_kind="extraction",
        source_id=source.source_id or source.domain,
        source_domain=source.domain,
        cache_key=cache_key,
        source_url=page.url,
        content_hash=page.content_hash,
        payload_json=list(fragments),
        expires_at=_expires_after(hours=source.cache.extraction_ttl_hours),
    )
    return fragments, False


def _candidate_from_payload(payload: dict[str, Any]) -> TrustedSourceSearchCandidate:
    return TrustedSourceSearchCandidate(
        source_id=str(payload["source_id"]),
        source_domain=str(payload["source_domain"]),
        source_language=str(payload["source_language"]),
        url=str(payload["url"]),
        title=str(payload["title"]),
        snippet=str(payload["snippet"]),
        score=float(payload["score"]),
        metadata={str(key): str(value) for key, value in dict(payload.get("metadata", {})).items()},
    )


def _page_from_payload(payload: dict[str, Any]) -> TrustedSourcePage:
    published_at_raw = payload.get("published_at")
    published_at = datetime.fromisoformat(str(published_at_raw)) if published_at_raw else None
    return TrustedSourcePage(
        source_id=str(payload["source_id"]),
        url=str(payload["url"]),
        title=str(payload["title"]) if payload.get("title") else None,
        text=str(payload["text"]),
        source_domain=str(payload["source_domain"]),
        source_language=str(payload["source_language"]),
        content_hash=str(payload["content_hash"]),
        metadata={str(key): str(value) for key, value in dict(payload.get("metadata", {})).items()},
        published_at=published_at,
    )


def _serialize_candidate(candidate: TrustedSourceSearchCandidate) -> dict[str, Any]:
    return {
        "source_id": candidate.source_id,
        "source_domain": candidate.source_domain,
        "source_language": candidate.source_language,
        "url": candidate.url,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "score": candidate.score,
        "metadata": candidate.metadata,
    }


def _serialize_page(page: TrustedSourcePage) -> dict[str, Any]:
    return {
        "source_id": page.source_id,
        "url": page.url,
        "title": page.title,
        "text": page.text,
        "source_domain": page.source_domain,
        "source_language": page.source_language,
        "content_hash": page.content_hash,
        "metadata": page.metadata,
        "published_at": page.published_at.isoformat() if page.published_at else None,
    }


def _score_fragment(*, query_text: str, fragment: str, base_score: float) -> float:
    """Blend provider score with simple lexical overlap for fragment ranking."""

    query_tokens = {token for token in normalize_whitespace(query_text.lower()).split(" ") if token}
    if not query_tokens:
        return base_score
    fragment_tokens = {token for token in normalize_whitespace(fragment.lower()).split(" ") if token}
    overlap = len(query_tokens & fragment_tokens) / max(1, len(query_tokens))
    return base_score + overlap


def _expires_after(*, hours: int) -> datetime:
    """Return UTC expiration timestamp after the requested TTL."""

    return datetime.now(timezone.utc) + timedelta(hours=hours)
