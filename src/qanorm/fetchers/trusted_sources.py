"""Online fetch/search helpers for allowlisted trusted sources."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

from lxml import html

from qanorm.fetchers.http import HttpFetcher
from qanorm.providers.searxng import SearXNGProvider, SearXNGResult
from qanorm.settings import TrustedSourceAdapterConfig
from qanorm.utils.text import normalize_whitespace


_BINARY_TRUSTED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
}


@dataclass(slots=True, frozen=True)
class TrustedSourceSearchCandidate:
    """One site-restricted search result before page fetch and extraction."""

    source_id: str
    source_domain: str
    source_language: str
    url: str
    title: str
    snippet: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TrustedSourcePage:
    """Normalized trusted-source page payload fetched online."""

    source_id: str
    url: str
    title: str | None
    text: str
    source_domain: str
    source_language: str
    content_hash: str
    metadata: dict[str, str] = field(default_factory=dict)
    published_at: datetime | None = None


class TrustedSourceRouter:
    """Select trusted sources for one query based on the allowlist registry."""

    def __init__(self, sources: Iterable[TrustedSourceAdapterConfig]) -> None:
        self.sources = list(sources)

    def select_sources(
        self,
        *,
        query_text: str,
        allowed_domains: Iterable[str] | None = None,
    ) -> list[TrustedSourceAdapterConfig]:
        """Return matching sources in deterministic priority order."""

        domains = {item.strip().lower() for item in (allowed_domains or []) if item.strip()}
        selected = [item for item in self.sources if not domains or item.domain.lower() in domains]
        if not selected:
            return []

        has_cyrillic = any("\u0400" <= char <= "\u04FF" for char in query_text)
        if has_cyrillic:
            selected.sort(key=lambda item: (item.language != "ru", item.display_name or item.domain))
        else:
            selected.sort(key=lambda item: (item.language == "ru", item.display_name or item.domain))
        return selected


def build_trusted_search_query(*, query_text: str, source: TrustedSourceAdapterConfig) -> str:
    """Build a source-aware search query using trusted-source hints."""

    cleaned = normalize_whitespace(query_text)
    hints = [normalize_whitespace(item) for item in source.search.query_hints if normalize_whitespace(item)]
    if not hints:
        return cleaned
    unique_hints = " ".join(dict.fromkeys(hints))
    return normalize_whitespace(f"{cleaned} {unique_hints}")


def canonicalize_trusted_url(url: str, *, source: TrustedSourceAdapterConfig) -> str | None:
    """Normalize and validate one trusted-source URL against the source policy."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() != source.domain.lower():
        return None

    normalized = urljoin(f"{parsed.scheme}://{parsed.netloc}", parsed.path or "/")
    normalized_path = urlparse(normalized).path.lower()
    blocked_prefixes = [item.rstrip("/") for item in source.search.blocked_prefixes if item.strip()]
    allowed_prefixes = [item.rstrip("/") for item in source.search.allowed_prefixes if item.strip()]

    if any(normalized_path.endswith(extension) for extension in _BINARY_TRUSTED_EXTENSIONS):
        return None
    if blocked_prefixes and any(normalized.startswith(prefix) for prefix in blocked_prefixes):
        return None
    if allowed_prefixes and not any(normalized.startswith(prefix) for prefix in allowed_prefixes):
        return None
    return normalized


def filter_trusted_search_results(
    results: Iterable[SearXNGResult],
    *,
    source: TrustedSourceAdapterConfig,
) -> list[TrustedSourceSearchCandidate]:
    """Filter raw search results against one trusted-source policy."""

    candidates: list[TrustedSourceSearchCandidate] = []
    seen_urls: set[str] = set()
    for result in results:
        normalized_url = canonicalize_trusted_url(result.url, source=source)
        if normalized_url is None or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        candidates.append(
            TrustedSourceSearchCandidate(
                source_id=source.source_id or source.domain,
                source_domain=source.domain,
                source_language=source.language,
                url=normalized_url,
                title=result.title,
                snippet=result.snippet,
                score=float(result.score or 0.0),
                metadata={"engine": result.engine or "", "search_title": result.title},
            )
        )
    return candidates[: source.search.max_results]


async def search_trusted_source_urls(
    *,
    query_text: str,
    source: TrustedSourceAdapterConfig,
    provider: SearXNGProvider | None = None,
) -> list[TrustedSourceSearchCandidate]:
    """Run one site-restricted online search for a trusted source."""

    provider = provider or SearXNGProvider()
    results = await provider.search(
        query_text=build_trusted_search_query(query_text=query_text, source=source),
        limit=source.search.max_results,
        allowed_domains=[source.domain],
    )
    return filter_trusted_search_results(results, source=source)


def fetch_trusted_source_page(
    url: str,
    *,
    source: TrustedSourceAdapterConfig,
    fetcher: HttpFetcher | None = None,
) -> TrustedSourcePage:
    """Fetch one trusted page with source-specific timeout and extraction settings."""

    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher(timeout_seconds=source.fetch.timeout_seconds)
    try:
        response = fetcher._request("GET", url)
    finally:
        if owned_fetcher:
            fetcher.close()

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise ValueError(f"Unsupported trusted-source content type for {url}: {content_type or 'unknown'}")

    payload = response.text
    return extract_trusted_source_page(payload, url=url, source=source)


def extract_trusted_source_page(
    payload: str,
    *,
    url: str,
    source: TrustedSourceAdapterConfig,
) -> TrustedSourcePage:
    """Extract sanitized text, metadata, and language from one trusted page."""

    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(payload.encode("utf-8"), parser=parser)
    for node in tree.xpath("//script|//style|//noscript|//iframe"):
        node.drop_tree()

    for selector in source.extract.remove_selectors:
        for node in tree.cssselect(selector):
            node.drop_tree()

    content_nodes = []
    for selector in source.extract.article_selectors:
        content_nodes.extend(tree.cssselect(selector))
    if not content_nodes:
        body = tree.find("body")
        content_nodes = [body if body is not None else tree]

    title = normalize_whitespace(" ".join(tree.xpath("//title/text()"))) or None
    text = normalize_whitespace(" ".join(node.text_content() for node in content_nodes if node is not None))
    metadata = _extract_meta_pairs(tree)
    published_at = _parse_published_at(metadata)
    source_language = (
        metadata.get("og:locale", "").split("_", maxsplit=1)[0]
        or tree.get("lang")
        or source.language
    )
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return TrustedSourcePage(
        source_id=source.source_id or source.domain,
        url=url,
        title=title,
        text=text,
        source_domain=source.domain,
        source_language=source_language,
        content_hash=content_hash,
        metadata=metadata,
        published_at=published_at,
    )


def fragment_trusted_source_text(text: str, *, max_chars: int = 1200) -> list[str]:
    """Split trusted-source content into compact retrieval fragments without overlap."""

    normalized = normalize_whitespace(text)
    if not normalized:
        return []

    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", normalized) if item.strip()]
    if not sentences:
        return [normalized[:max_chars]]

    fragments: list[str] = []
    buffer = ""
    for sentence in sentences:
        candidate = normalize_whitespace(f"{buffer} {sentence}".strip())
        if buffer and len(candidate) > max_chars:
            fragments.append(buffer)
            buffer = sentence
            continue
        if len(candidate) > max_chars and not buffer:
            fragments.append(sentence[:max_chars].strip())
            buffer = sentence[max_chars:].strip()
            continue
        buffer = candidate
    if buffer:
        fragments.append(buffer)
    return [item for item in fragments if item]


def build_cache_key(*parts: str) -> str:
    """Build a stable cache key from ordered text fragments."""

    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _extract_meta_pairs(tree: html.HtmlElement) -> dict[str, str]:
    """Collect compact page metadata for provenance and cache invalidation."""

    metadata: dict[str, str] = {}
    for node in tree.xpath("//meta[@name or @property][@content]"):
        key = (node.get("name") or node.get("property") or "").strip().lower()
        value = normalize_whitespace(node.get("content", ""))
        if key and value:
            metadata[key] = value
    return metadata


def _parse_published_at(metadata: dict[str, str]) -> datetime | None:
    """Parse coarse publication time from known metadata fields."""

    for key in ("article:published_time", "og:published_time", "date", "publishdate"):
        value = metadata.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None
