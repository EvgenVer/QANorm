"""Normative catalog lookup tool used before full chunk retrieval is available."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, or_, select

from qanorm.db.types import StatusNormalized
from qanorm.models import Document, DocumentVersion
from qanorm.normalizers.codes import normalize_document_code
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class NormativeSearchTool(Tool):
    """Search the normative document catalog by code and title."""

    definition = ToolDefinition(
        name="normative_search",
        scope="normative",
        description="Search the normative corpus catalog by code or title.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Search documents with a cheap catalog query until chunk retrieval is implemented."""

        query_text = str(payload.get("query_text", "")).strip()
        if not query_text:
            raise ToolInputError("'query_text' is required for normative_search.")

        limit = max(1, min(int(payload.get("limit", 10)), 50))
        active_only = bool(payload.get("active_only", True))
        normalized_query = normalize_document_code(query_text)
        pattern = f"%{query_text}%"

        stmt = (
            select(Document, DocumentVersion)
            .outerjoin(DocumentVersion, Document.current_version_id == DocumentVersion.id)
            .where(
                or_(
                    Document.normalized_code.ilike(pattern),
                    Document.display_code.ilike(pattern),
                    Document.title.ilike(pattern),
                )
            )
        )
        if active_only:
            stmt = stmt.where(Document.status_normalized == StatusNormalized.ACTIVE)

        stmt = stmt.order_by(
            case((Document.normalized_code == normalized_query, 0), else_=1),
            Document.updated_at.desc(),
        ).limit(limit)

        results = []
        for document, version in context.session.execute(stmt).all():
            results.append(
                {
                    "document_id": str(document.id),
                    "normalized_code": document.normalized_code,
                    "display_code": document.display_code,
                    "document_type": document.document_type,
                    "title": document.title,
                    "status": document.status_normalized.value,
                    "current_version_id": str(version.id) if version is not None else None,
                    "edition_label": version.edition_label if version is not None else None,
                }
            )

        return ToolResult(
            payload={"query_text": query_text, "results": results},
            summary=f"Found {len(results)} normative document candidates.",
            metadata={"result_count": len(results), "active_only": active_only},
        )
