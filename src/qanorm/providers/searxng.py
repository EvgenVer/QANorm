"""Self-hosted SearXNG provider integration for open-web fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from qanorm.settings import RuntimeConfig, get_settings
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True, frozen=True)
class SearXNGResult:
    """One normalized SearXNG search result."""

    title: str
    url: str
    snippet: str
    engine: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SearXNGProvider:
    """Thin JSON client for the configured self-hosted SearXNG instance."""

    provider_name = "searxng"

    def __init__(self, runtime_config: RuntimeConfig | None = None) -> None:
        self.runtime_config = runtime_config or get_settings()
        self.base_url = self.runtime_config.env.searxng_base_url.rstrip("/")
        self.timeout_seconds = self.runtime_config.qa.search.open_web_request_timeout_seconds
        self.user_agent = self.runtime_config.qa.search.open_web_user_agent

    def build_query(self, *, query_text: str, allowed_domains: list[str] | None = None) -> str:
        """Build a constrained SearXNG query string."""

        cleaned = normalize_whitespace(query_text)
        if allowed_domains:
            domain_filters = " OR ".join(f"site:{domain}" for domain in allowed_domains)
            return f"{cleaned} ({domain_filters})"
        return cleaned

    async def search(
        self,
        *,
        query_text: str,
        limit: int,
        allowed_domains: list[str] | None = None,
    ) -> list[SearXNGResult]:
        """Query SearXNG and normalize the returned result items."""

        query = self.build_query(query_text=query_text, allowed_domains=allowed_domains)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        ) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": "all",
                    "safesearch": 0,
                },
            )
            response.raise_for_status()
            payload = response.json()

        results: list[SearXNGResult] = []
        for item in payload.get("results", [])[:limit]:
            title = normalize_whitespace(str(item.get("title", "")).strip())
            url = str(item.get("url", "")).strip()
            snippet = normalize_whitespace(str(item.get("content", "")).strip())
            if not title or not url:
                continue
            results.append(
                SearXNGResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    engine=str(item.get("engine")) if item.get("engine") else None,
                    score=float(item["score"]) if item.get("score") is not None else None,
                    metadata={key: value for key, value in item.items() if key not in {"title", "url", "content", "engine", "score"}},
                )
            )
        return results
