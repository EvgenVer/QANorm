"""Freshness-check tool that records non-blocking verification requests."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from qanorm.db.types import FreshnessCheckStatus
from qanorm.models import FreshnessCheck
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import DocumentRepository, DocumentVersionRepository, FreshnessCheckRepository
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class FreshnessCheckTool(Tool):
    """Persist a freshness-check request without blocking the current answer path."""

    definition = ToolDefinition(
        name="freshness_check",
        scope="freshness",
        description="Record a non-blocking freshness check request for one document.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Create a pending freshness-check row for later execution."""

        document = self._resolve_document(context, payload)
        current_version = DocumentVersionRepository(context.session).get_active_for_document(document.id)
        check = FreshnessCheckRepository(context.session).add(
            FreshnessCheck(
                query_id=context.query_id,
                document_id=document.id,
                document_version_id=current_version.id if current_version is not None else None,
                check_status=FreshnessCheckStatus.PENDING,
                local_edition_label=current_version.edition_label if current_version is not None else None,
                details_json={
                    "requested_by_tool": self.definition.name,
                    "reason": payload.get("reason"),
                    "non_blocking": True,
                },
            )
        )
        return ToolResult(
            payload={
                "freshness_check_id": str(check.id),
                "document_id": str(document.id),
                "document_code": document.normalized_code,
                "document_version_id": str(current_version.id) if current_version is not None else None,
                "status": check.check_status.value,
            },
            summary=f"Recorded freshness check request for '{document.normalized_code}'.",
        )

    def _resolve_document(self, context: ToolExecutionContext, payload: dict[str, Any]):
        """Resolve the target document once for both local metadata and audit."""

        repository = DocumentRepository(context.session)
        document_id = payload.get("document_id")
        document_code = payload.get("document_code")
        if document_id:
            document = repository.get(UUID(str(document_id)))
        elif document_code:
            document = repository.get_by_normalized_code(normalize_document_code(str(document_code)))
        else:
            raise ToolInputError("Either 'document_id' or 'document_code' is required for freshness_check.")
        if document is None:
            raise ToolInputError("Requested document was not found.")
        return document
