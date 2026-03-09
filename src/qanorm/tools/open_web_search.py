"""Open-web search tool shell that records audited provider requests."""

from __future__ import annotations

from typing import Any

from qanorm.settings import get_settings
from qanorm.models import QAQuery
from qanorm.services.qa.open_web_service import normalize_open_web_results_to_evidence, search_open_web
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
        results = await search_open_web(
            context.session,
            query_id=context.query_id,
            subtask_id=context.subtask_id,
            query_text=query_text,
            allowed_domains=allowed_domains or None,
            limit=limit,
        )
        evidence_rows = normalize_open_web_results_to_evidence(
            query_id=context.query_id,
            subtask_id=context.subtask_id,
            results=results,
        )
        self._mark_query_usage(context)

        return ToolResult(
            payload={
                "query_text": query_text,
                "allowed_domains": allowed_domains,
                "results": [
                    {
                        "title": item.title,
                        "url": item.url,
                        "snippet": item.snippet,
                        "engine": item.engine,
                        "score": item.score,
                    }
                    for item in results
                ],
                "evidence": [row.quote for row in evidence_rows],
            },
            summary=f"Open-web search request recorded with {len(results)} results.",
            metadata={"result_count": len(results)},
        )

    def _mark_query_usage(self, context: ToolExecutionContext) -> None:
        """Persist that the current query used open-web evidence."""

        query = context.session.get(QAQuery, context.query_id)
        if query is not None and not query.used_open_web:
            query.used_open_web = True
            context.session.flush()
