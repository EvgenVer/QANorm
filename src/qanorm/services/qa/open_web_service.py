"""Open-web search and content extraction over self-hosted SearXNG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse
from uuid import UUID

from lxml import html
from sqlalchemy.orm import Session

from qanorm.db.types import EvidenceSourceKind, SearchScope, SearchStatus
from qanorm.fetchers.html import fetch_html_document
from qanorm.models import QAEvidence, SearchEvent
from qanorm.providers.searxng import SearXNGProvider, SearXNGResult
from qanorm.repositories import SearchEventRepository
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True, frozen=True)
class OpenWebDocument:
    """Fetched and sanitized open-web page content."""

    source_url: str
    source_domain: str
    title: str | None
    text: str


def sanitize_html_to_text(payload: str) -> tuple[str | None, str]:
    """Drop active HTML content and return a compact title/text pair."""

    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(payload.encode("utf-8"), parser=parser)
    for node in tree.xpath("//script|//style|//noscript|//iframe"):
        node.drop_tree()
    title = normalize_whitespace(" ".join(tree.xpath("//title/text()"))) or None
    text = normalize_whitespace(" ".join(tree.xpath("//body//text()")))
    return title, text


def fetch_open_web_document(url: str) -> OpenWebDocument:
    """Fetch one selected open-web result and sanitize its HTML."""

    html_payload = fetch_html_document(url)
    title, text = sanitize_html_to_text(html_payload)
    return OpenWebDocument(
        source_url=url,
        source_domain=urlparse(url).netloc.lower(),
        title=title,
        text=text,
    )


async def search_open_web(
    session: Session,
    *,
    query_id: UUID | None,
    subtask_id: UUID | None,
    query_text: str,
    allowed_domains: Iterable[str] | None = None,
    limit: int,
    provider: SearXNGProvider | None = None,
) -> list[SearXNGResult]:
    """Run one audited open-web search against SearXNG."""

    provider = provider or SearXNGProvider()
    domains = [item.strip() for item in (allowed_domains or []) if item.strip()]
    results = await provider.search(query_text=query_text, limit=limit, allowed_domains=domains or None)
    SearchEventRepository(session).add(
        SearchEvent(
            query_id=query_id,
            subtask_id=subtask_id,
            provider_name=provider.provider_name,
            search_scope=SearchScope.OPEN_WEB,
            query_text=query_text,
            allowed_domains=domains or None,
            result_count=len(results),
            status=SearchStatus.COMPLETED,
        )
    )
    return results


def normalize_open_web_results_to_evidence(
    *,
    query_id: UUID,
    results: Iterable[SearXNGResult],
    subtask_id: UUID | None = None,
) -> list[QAEvidence]:
    """Fetch selected search results and normalize them into external evidence rows."""

    evidence_rows: list[QAEvidence] = []
    for result in results:
        page = fetch_open_web_document(result.url)
        quote = page.text[:500] or result.snippet
        evidence_rows.append(
            QAEvidence(
                query_id=query_id,
                subtask_id=subtask_id,
                source_kind=EvidenceSourceKind.OPEN_WEB,
                source_url=page.source_url,
                source_domain=page.source_domain,
                locator=page.title,
                quote=quote,
                chunk_text=page.text or result.snippet,
                relevance_score=result.score,
                is_normative=False,
                requires_verification=True,
            )
        )
    return evidence_rows
