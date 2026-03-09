"""Allowlisted trusted-source fetching and sitemap discovery helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from lxml import html

from qanorm.fetchers.http import HttpFetcher
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True, frozen=True)
class TrustedSourcePage:
    """Normalized page payload fetched from one allowlisted trusted source."""

    url: str
    title: str | None
    text: str
    source_domain: str
    metadata: dict[str, str] = field(default_factory=dict)
    published_at: datetime | None = None


def fetch_trusted_source_page(url: str, *, fetcher: HttpFetcher | None = None) -> TrustedSourcePage:
    """Load one trusted-source page and normalize its visible text."""

    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher()
    try:
        payload = fetcher.get_html(url)
    finally:
        if owned_fetcher:
            fetcher.close()

    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(payload.encode("utf-8"), parser=parser)
    for node in tree.xpath("//script|//style|//noscript"):
        node.drop_tree()

    title = normalize_whitespace(" ".join(tree.xpath("//title/text()"))) or None
    meta_pairs = _extract_meta_pairs(tree)
    text = normalize_whitespace(" ".join(tree.xpath("//body//text()")))
    published_at = _parse_published_at(meta_pairs)
    return TrustedSourcePage(
        url=url,
        title=title,
        text=text,
        source_domain=urlparse(url).netloc.lower(),
        metadata=meta_pairs,
        published_at=published_at,
    )


def discover_trusted_source_urls(
    *,
    domain: str,
    sitemap_urls: Iterable[str],
    seed_urls: Iterable[str],
    allowed_prefixes: Iterable[str],
    fetcher: HttpFetcher | None = None,
) -> list[str]:
    """Discover candidate trusted-source URLs from sitemaps and configured seeds."""

    owned_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher()
    try:
        discovered: list[str] = []
        seen: set[str] = set()
        prefixes = [item.rstrip("/") for item in allowed_prefixes if item.strip()]

        for sitemap_url in sitemap_urls:
            for url in _load_sitemap_urls(sitemap_url, fetcher=fetcher):
                normalized = _normalize_discovered_url(url, base_domain=domain, allowed_prefixes=prefixes)
                if normalized is None or normalized in seen:
                    continue
                seen.add(normalized)
                discovered.append(normalized)

        for seed_url in seed_urls:
            normalized = _normalize_discovered_url(seed_url, base_domain=domain, allowed_prefixes=prefixes)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            discovered.append(normalized)

        return discovered
    finally:
        if owned_fetcher:
            fetcher.close()


def _load_sitemap_urls(sitemap_url: str, *, fetcher: HttpFetcher) -> list[str]:
    """Load one sitemap or sitemap-index recursively."""

    xml_payload = fetcher.get_html(sitemap_url)
    try:
        root = ElementTree.fromstring(xml_payload.encode("utf-8"))
    except ElementTree.ParseError:
        return []

    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.partition("}")[0] + "}"
    location_tag = f"{namespace}loc"

    if root.tag.endswith("sitemapindex"):
        nested: list[str] = []
        for child in root.findall(f".//{location_tag}"):
            if child.text:
                nested.extend(_load_sitemap_urls(child.text.strip(), fetcher=fetcher))
        return nested

    return [child.text.strip() for child in root.findall(f".//{location_tag}") if child.text]


def _normalize_discovered_url(
    url: str,
    *,
    base_domain: str,
    allowed_prefixes: list[str],
) -> str | None:
    """Validate a discovered URL against the allowlisted domain and prefixes."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() != base_domain.lower():
        return None
    normalized = urljoin(f"{parsed.scheme}://{parsed.netloc}", parsed.path or "/")
    if allowed_prefixes and not any(normalized.startswith(prefix) for prefix in allowed_prefixes):
        return None
    return normalized


def _extract_meta_pairs(tree: html.HtmlElement) -> dict[str, str]:
    """Collect simple meta tags to keep external provenance compact."""

    metadata: dict[str, str] = {}
    for node in tree.xpath("//meta[@name or @property][@content]"):
        key = (node.get("name") or node.get("property") or "").strip().lower()
        value = normalize_whitespace(node.get("content", ""))
        if key and value:
            metadata[key] = value
    return metadata


def _parse_published_at(metadata: dict[str, str]) -> datetime | None:
    """Parse a coarse publication timestamp from common meta tags."""

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
