"""Source extraction helpers used before deeper web ingestion is added."""

from __future__ import annotations

import html
import re
from typing import Any

from qanorm.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolInputError, ToolResult
from qanorm.utils.text import normalize_whitespace


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


class SourceExtractTool(Tool):
    """Normalize raw source content into text suitable for downstream processing."""

    definition = ToolDefinition(
        name="source_extract",
        scope="source_extract",
        description="Extract normalized text and a short excerpt from raw source content.",
    )

    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Extract text from either plain text or lightweight HTML payloads."""

        content = str(payload.get("content", ""))
        if not content.strip():
            raise ToolInputError("'content' is required for source_extract.")

        content_type = str(payload.get("content_type", "text/plain")).lower()
        normalized_text = self._normalize_content(content, content_type=content_type)
        max_excerpt_length = max(80, min(int(payload.get("max_excerpt_length", 500)), 4000))
        excerpt = normalized_text[:max_excerpt_length].rstrip()
        paragraph_count = len([part for part in normalized_text.split("\n") if part.strip()])

        return ToolResult(
            payload={
                "source_url": payload.get("source_url"),
                "content_type": content_type,
                "text": normalized_text,
                "excerpt": excerpt,
                "char_count": len(normalized_text),
                "paragraph_count": paragraph_count,
            },
            summary=f"Extracted {len(normalized_text)} characters of normalized source text.",
        )

    def _normalize_content(self, content: str, *, content_type: str) -> str:
        """Collapse markup noise while keeping the text stable for later chunking."""

        if "html" in content_type:
            content = _SCRIPT_STYLE_RE.sub(" ", content)
            content = _TAG_RE.sub(" ", content)
            content = html.unescape(content)

        lines = [normalize_whitespace(line) for line in content.replace("\r", "\n").split("\n")]
        return "\n".join(line for line in lines if line)
