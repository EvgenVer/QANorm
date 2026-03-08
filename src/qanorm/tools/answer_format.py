"""Answer formatting tool for consistent chat output structure."""

from __future__ import annotations

from typing import Any

from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult


class AnswerFormatTool(Tool):
    """Convert structured answer parts into a stable markdown response."""

    definition = ToolDefinition(
        name="answer_format",
        scope="answer_format",
        description="Render structured answer parts into markdown.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Build a compact markdown answer with warnings and source sections."""

        answer_text = str(payload.get("answer_text", "")).strip()
        if not answer_text:
            raise ToolInputError("'answer_text' is required for answer_format.")

        title = str(payload.get("title", "Ответ")).strip() or "Ответ"
        warnings = [str(item).strip() for item in payload.get("warnings", []) if str(item).strip()]
        normative_sources = [str(item).strip() for item in payload.get("normative_sources", []) if str(item).strip()]
        external_sources = [str(item).strip() for item in payload.get("external_sources", []) if str(item).strip()]

        sections = [f"## {title}", answer_text]
        if warnings:
            sections.extend(["## Предупреждения", *[f"- {warning}" for warning in warnings]])
        if normative_sources:
            sections.extend(["## Нормативные источники", *[f"- {source}" for source in normative_sources]])
        if external_sources:
            sections.extend(["## Ненормативные источники", *[f"- {source}" for source in external_sources]])

        markdown = "\n\n".join(sections).strip()
        return ToolResult(
            payload={"markdown": markdown},
            summary="Formatted answer into markdown sections.",
            metadata={
                "warning_count": len(warnings),
                "normative_source_count": len(normative_sources),
                "external_source_count": len(external_sources),
            },
        )
