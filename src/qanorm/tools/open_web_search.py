"""Open-web search tool shell that records audited provider requests."""

from __future__ import annotations

from typing import Any

from qanorm.db.types import SearchScope, SearchStatus
from qanorm.models import QAQuery, SearchEvent
from qanorm.repositories import SearchEventRepository
from qanorm.settings import get_settings
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class OpenWebSearchTool(Tool):
    """Record open-web search requests before the network provider is introduced."""

    definition = ToolDefinition(
        name="open_web_search",
        scope="open_web",
        description="Run an audited open-web search request through the configured provider.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Normalize the request and record the provider call envelope."""

        query_text = str(payload.get("query_text", "")).strip()
        if not query_text:
            raise ToolInputError("'query_text' is required for open_web_search.")

        limit = max(1, min(int(payload.get("limit", get_settings().qa.search.open_web_max_results)), 20))
        allowed_domains = [str(item).strip() for item in payload.get("allowed_domains", []) if str(item).strip()]
        results = [dict(item) for item in payload.get("results", [])][:limit]

        SearchEventRepository(context.session).add(
            SearchEvent(
                query_id=context.query_id,
                subtask_id=context.subtask_id,
                provider_name=get_settings().qa.search.open_web_provider,
                search_scope=SearchScope.OPEN_WEB,
                query_text=query_text,
                allowed_domains=allowed_domains or None,
                result_count=len(results),
                status=SearchStatus.COMPLETED,
            )
        )
        self._mark_query_usage(context)

        # Until the dedicated web-search block lands, this tool only wraps audited request/response payloads.
        return ToolResult(
            payload={"query_text": query_text, "allowed_domains": allowed_domains, "results": results},
            summary=f"Open-web search request recorded with {len(results)} results.",
            metadata={"result_count": len(results)},
        )

    def _mark_query_usage(self, context: ToolExecutionContext) -> None:
        """Persist that the current query used open-web evidence."""

        query = context.session.get(QAQuery, context.query_id)
        if query is not None and not query.used_open_web:
            query.used_open_web = True
            context.session.flush()
