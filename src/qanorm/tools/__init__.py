"""Stage 2 tool package."""

from qanorm.tools.answer_format import AnswerFormatTool
from qanorm.tools.base import (
    DEFAULT_ALLOWED_SCOPES,
    Tool,
    ToolDefinition,
    ToolExecutionContext,
    ToolInputError,
    ToolPolicyError,
    ToolRegistry,
    ToolResult,
    ToolScope,
)
from qanorm.tools.document_fetch import DocumentFetchTool
from qanorm.tools.document_refresh import DocumentRefreshTool
from qanorm.tools.freshness_check import FreshnessCheckTool
from qanorm.tools.normative_search import NormativeSearchTool
from qanorm.tools.open_web_search import OpenWebSearchTool
from qanorm.tools.source_extract import SourceExtractTool
from qanorm.tools.trusted_search import TrustedSearchTool


def create_tool_registry() -> ToolRegistry:
    """Register all base Stage 2 tools available after block AF."""

    registry = ToolRegistry()
    for tool in (
        NormativeSearchTool(),
        DocumentFetchTool(),
        FreshnessCheckTool(),
        DocumentRefreshTool(),
        TrustedSearchTool(),
        OpenWebSearchTool(),
        SourceExtractTool(),
        AnswerFormatTool(),
    ):
        registry.register(tool)
    return registry


__all__ = [
    "AnswerFormatTool",
    "DEFAULT_ALLOWED_SCOPES",
    "DocumentFetchTool",
    "DocumentRefreshTool",
    "FreshnessCheckTool",
    "NormativeSearchTool",
    "OpenWebSearchTool",
    "SourceExtractTool",
    "Tool",
    "ToolDefinition",
    "ToolExecutionContext",
    "ToolInputError",
    "ToolPolicyError",
    "ToolRegistry",
    "ToolResult",
    "ToolScope",
    "TrustedSearchTool",
    "create_tool_registry",
]
