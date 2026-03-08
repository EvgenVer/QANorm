"""Document metadata fetch tool for Stage 2 orchestration."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from qanorm.models import Document, DocumentNode
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import DocumentRepository, DocumentSourceRepository, DocumentVersionRepository, RawArtifactRepository
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class DocumentFetchTool(Tool):
    """Fetch detailed metadata for one normative document and its active version."""

    definition = ToolDefinition(
        name="document_fetch",
        scope="document",
        description="Fetch one document, its active version, sources, and raw artifacts.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Return a structured document snapshot suitable for later retrieval stages."""

        document = self._resolve_document(context, payload)
        version_repository = DocumentVersionRepository(context.session)
        active_version = version_repository.get_active_for_document(document.id)
        source_rows = DocumentSourceRepository(context.session).list_for_document_version(active_version.id) if active_version else []
        artifact_rows = RawArtifactRepository(context.session).list_for_document_version(active_version.id) if active_version else []

        node_count = 0
        node_preview: list[dict[str, Any]] = []
        if active_version is not None:
            node_count = int(
                context.session.execute(
                    select(func.count(DocumentNode.id)).where(DocumentNode.document_version_id == active_version.id)
                ).scalar_one()
            )
            if payload.get("include_node_preview"):
                preview_limit = max(1, min(int(payload.get("node_preview_limit", 10)), 50))
                stmt = (
                    select(DocumentNode)
                    .where(DocumentNode.document_version_id == active_version.id)
                    .order_by(DocumentNode.order_index.asc())
                    .limit(preview_limit)
                )
                node_preview = [
                    {
                        "node_id": str(node.id),
                        "node_type": node.node_type,
                        "label": node.label,
                        "title": node.title,
                        "locator": node.locator,
                        "text": node.text,
                    }
                    for node in context.session.execute(stmt).scalars().all()
                ]

        result = {
            "document": {
                "document_id": str(document.id),
                "normalized_code": document.normalized_code,
                "display_code": document.display_code,
                "document_type": document.document_type,
                "title": document.title,
                "status": document.status_normalized.value,
            },
            "active_version": None
            if active_version is None
            else {
                "document_version_id": str(active_version.id),
                "edition_label": active_version.edition_label,
                "status": active_version.status_normalized.value,
                "text_actualized_at": active_version.text_actualized_at.isoformat()
                if active_version.text_actualized_at
                else None,
                "description_actualized_at": active_version.description_actualized_at.isoformat()
                if active_version.description_actualized_at
                else None,
                "node_count": node_count,
            },
            "sources": [
                {
                    "source_id": str(source.id),
                    "card_url": source.card_url,
                    "html_url": source.html_url,
                    "pdf_url": source.pdf_url,
                    "print_url": source.print_url,
                    "source_type": source.source_type,
                }
                for source in source_rows
            ],
            "raw_artifacts": [
                {
                    "artifact_id": str(artifact.id),
                    "artifact_type": artifact.artifact_type.value,
                    "storage_path": artifact.storage_path,
                    "relative_path": artifact.relative_path,
                    "mime_type": artifact.mime_type,
                    "file_size": artifact.file_size,
                }
                for artifact in artifact_rows
            ],
            "node_preview": node_preview,
        }
        return ToolResult(
            payload=result,
            summary=f"Fetched document '{document.normalized_code}' and its active version metadata.",
            metadata={
                "source_count": len(source_rows),
                "artifact_count": len(artifact_rows),
                "node_count": node_count,
            },
        )

    def _resolve_document(self, context: ToolExecutionContext, payload: dict[str, Any]) -> Document:
        """Resolve a document by explicit id or canonical code."""

        repository = DocumentRepository(context.session)
        document_id = payload.get("document_id")
        document_code = payload.get("document_code")

        if document_id:
            document = repository.get(UUID(str(document_id)))
        elif document_code:
            document = repository.get_by_normalized_code(normalize_document_code(str(document_code)))
        else:
            raise ToolInputError("Either 'document_id' or 'document_code' is required for document_fetch.")

        if document is None:
            raise ToolInputError("Requested document was not found.")
        return document
