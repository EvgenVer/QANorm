"""Trusted-source search tool backed by the local trusted-source store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from qanorm.db.types import SearchScope, SearchStatus
from qanorm.models import QAQuery, SearchEvent, TrustedSourceChunk, TrustedSourceDocument
from qanorm.repositories import SearchEventRepository
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class TrustedSearchTool(Tool):
    """Search the locally synchronized trusted-source corpus."""

    definition = ToolDefinition(
        name="trusted_search",
        scope="trusted_web",
        description="Search the locally indexed trusted-source corpus.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Run a bounded lookup against trusted-source chunks and audit the search event."""

        query_text = str(payload.get("query_text", "")).strip()
        if not query_text:
            raise ToolInputError("'query_text' is required for trusted_search.")

        limit = max(1, min(int(payload.get("limit", 5)), 20))
        allowed_domains = [str(item).strip() for item in payload.get("allowed_domains", []) if str(item).strip()]
        pattern = f"%{query_text}%"

        stmt = (
            select(TrustedSourceChunk, TrustedSourceDocument)
            .join(TrustedSourceDocument, TrustedSourceChunk.document_id == TrustedSourceDocument.id)
            .where(TrustedSourceChunk.text.ilike(pattern))
            .order_by(TrustedSourceChunk.created_at.desc())
        )
        if allowed_domains:
            stmt = stmt.where(TrustedSourceDocument.source_domain.in_(allowed_domains))
        rows = context.session.execute(stmt.limit(limit)).all()

        results = [
            {
                "chunk_id": str(chunk.id),
                "document_id": str(document.id),
                "source_domain": document.source_domain,
                "source_url": document.source_url,
                "title": document.title,
                "locator": chunk.locator,
                "text": chunk.text,
            }
            for chunk, document in rows
        ]

        SearchEventRepository(context.session).add(
            SearchEvent(
                query_id=context.query_id,
                subtask_id=context.subtask_id,
                provider_name="trusted_sources",
                search_scope=SearchScope.TRUSTED_WEB,
                query_text=query_text,
                allowed_domains=allowed_domains or None,
                result_count=len(results),
                status=SearchStatus.COMPLETED,
            )
        )
        self._mark_query_usage(context)
        return ToolResult(
            payload={"query_text": query_text, "results": results},
            summary=f"Trusted-source search returned {len(results)} results.",
            metadata={"result_count": len(results)},
        )

    def _mark_query_usage(self, context: ToolExecutionContext) -> None:
        """Persist that the current query used trusted external evidence."""

        query = context.session.get(QAQuery, context.query_id)
        if query is not None and not query.used_trusted_web:
            query.used_trusted_web = True
            context.session.flush()
