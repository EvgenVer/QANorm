"""Trusted-source search tool backed by online allowlisted retrieval."""

from __future__ import annotations

from typing import Any

from qanorm.models import QAQuery
from qanorm.services.qa.trusted_sources_service import normalize_trusted_hits_to_evidence, search_trusted_sources
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class TrustedSearchTool(Tool):
    """Search approved trusted sources online with bounded shared cache."""

    definition = ToolDefinition(
        name="trusted_search",
        scope="trusted_web",
        description="Search allowlisted trusted sources online.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Run bounded trusted-source online retrieval and audit the search event."""

        query_text = str(payload.get("query_text", "")).strip()
        if not query_text:
            raise ToolInputError("'query_text' is required for trusted_search.")

        limit = max(1, min(int(payload.get("limit", 5)), 20))
        allowed_domains = [str(item).strip() for item in payload.get("allowed_domains", []) if str(item).strip()]
        hits = await search_trusted_sources(
            context.session,
            query_id=context.query_id,
            subtask_id=context.subtask_id,
            query_text=query_text,
            allowed_domains=allowed_domains or None,
            limit=limit,
        )
        results = [
            {
                "source_id": hit.source_id,
                "source_domain": hit.source_domain,
                "source_url": hit.source_url,
                "title": hit.title,
                "locator": hit.locator,
                "text": hit.text,
                "language": hit.language,
                "score": hit.score,
                "cache_hit": hit.cache_hit,
            }
            for hit in hits
        ]
        self._mark_query_usage(context)
        return ToolResult(
            payload={
                "query_text": query_text,
                "results": results,
                "evidence": [row.quote for row in normalize_trusted_hits_to_evidence(query_id=context.query_id, hits=hits)],
            },
            summary=f"Trusted-source search returned {len(results)} results.",
            metadata={"result_count": len(results)},
        )

    def _mark_query_usage(self, context: ToolExecutionContext) -> None:
        """Persist that the current query used trusted external evidence."""

        query = context.session.get(QAQuery, context.query_id)
        if query is not None and not query.used_trusted_web:
            query.used_trusted_web = True
            context.session.flush()
