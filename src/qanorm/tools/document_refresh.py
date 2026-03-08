"""Document refresh tool that queues ingestion refresh jobs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from qanorm.db.types import JobType
from qanorm.jobs.scheduler import create_job
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import DocumentRepository, IngestionJobRepository
from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class DocumentRefreshTool(Tool):
    """Queue a refresh job for one known document."""

    definition = ToolDefinition(
        name="document_refresh",
        scope="refresh",
        description="Queue a refresh job for one normative document.",
        mutates_state=True,
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Queue or reuse a deduplicated refresh job for the target document."""

        normalized_code = self._resolve_document_code(context, payload)
        job = create_job(
            IngestionJobRepository(context.session),
            job_type=JobType.REFRESH_DOCUMENT,
            payload={"document_code": normalized_code},
        )
        return ToolResult(
            payload={
                "document_code": normalized_code,
                "refresh_job_id": str(job.id),
                "job_status": job.status.value,
            },
            summary=f"Queued refresh job for '{normalized_code}'.",
        )

    def _resolve_document_code(self, context: ToolExecutionContext, payload: dict[str, Any]) -> str:
        """Resolve and validate the target document code before queuing work."""

        repository = DocumentRepository(context.session)
        document_code = payload.get("document_code")
        document_id = payload.get("document_id")

        if document_code:
            normalized_code = normalize_document_code(str(document_code))
            document = repository.get_by_normalized_code(normalized_code)
        elif document_id:
            document = repository.get(UUID(str(document_id)))
            normalized_code = document.normalized_code if document is not None else ""
        else:
            raise ToolInputError("Either 'document_code' or 'document_id' is required for document_refresh.")

        if document is None:
            raise ToolInputError("Requested document was not found.")
        return normalized_code
