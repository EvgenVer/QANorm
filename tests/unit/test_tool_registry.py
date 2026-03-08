from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from qanorm.db.types import ToolInvocationStatus
from qanorm.tools import (
    DEFAULT_ALLOWED_SCOPES,
    Tool,
    ToolDefinition,
    ToolExecutionContext,
    ToolInputError,
    ToolPolicyError,
    create_tool_registry,
)
from qanorm.tools.answer_format import AnswerFormatTool
from qanorm.tools.base import ToolRegistry, ToolResult
from qanorm.tools.source_extract import SourceExtractTool


class _FakeNormativeTool(Tool):
    definition = ToolDefinition(name="fake_normative", scope="normative", description="fake")

    async def execute(self, context: ToolExecutionContext, payload: dict[str, object]) -> ToolResult:
        return ToolResult(payload={"echo": payload}, summary="ok")


class _FailingNormativeTool(Tool):
    definition = ToolDefinition(name="failing_normative", scope="normative", description="fake")

    async def execute(self, context: ToolExecutionContext, payload: dict[str, object]) -> ToolResult:
        raise ToolInputError("boom")


def test_tool_registry_records_completed_invocation() -> None:
    session = MagicMock()
    registry = ToolRegistry()
    registry.register(_FakeNormativeTool())
    context = ToolExecutionContext(session=session, query_id=uuid4(), allowed_scopes=frozenset({"normative"}))

    result = asyncio.run(registry.invoke("fake_normative", context=context, payload={"query_text": "42"}))

    invocation = session.add.call_args.args[0]
    assert result.payload["echo"] == {"query_text": "42"}
    assert invocation.tool_name == "fake_normative"
    assert invocation.tool_scope == "normative"
    assert invocation.status == ToolInvocationStatus.COMPLETED
    assert invocation.result_summary == "ok"


def test_tool_registry_rejects_scope_violations_before_execution() -> None:
    session = MagicMock()
    registry = ToolRegistry()
    registry.register(_FakeNormativeTool())
    context = ToolExecutionContext(session=session, query_id=uuid4(), allowed_scopes=frozenset({"open_web"}))

    with pytest.raises(ToolPolicyError):
        asyncio.run(registry.invoke("fake_normative", context=context, payload={"query_text": "42"}))

    session.add.assert_not_called()


def test_tool_registry_records_failed_invocation() -> None:
    session = MagicMock()
    registry = ToolRegistry()
    registry.register(_FailingNormativeTool())
    context = ToolExecutionContext(session=session, query_id=uuid4(), allowed_scopes=frozenset({"normative"}))

    with pytest.raises(ToolInputError):
        asyncio.run(registry.invoke("failing_normative", context=context, payload={"query_text": "42"}))

    invocation = session.add.call_args.args[0]
    assert invocation.status == ToolInvocationStatus.FAILED
    assert invocation.result_summary == "boom"


def test_create_tool_registry_registers_all_block_af_tools() -> None:
    registry = create_tool_registry()

    assert set(registry.list_registered()) == {
        "normative_search",
        "document_fetch",
        "freshness_check",
        "document_refresh",
        "trusted_search",
        "open_web_search",
        "source_extract",
        "answer_format",
    }
    assert DEFAULT_ALLOWED_SCOPES.issuperset({"normative", "open_web", "answer_format"})


def test_source_extract_tool_normalizes_html() -> None:
    result = asyncio.run(
        SourceExtractTool().execute(
            ToolExecutionContext(session=MagicMock(), query_id=uuid4()),
            {"content": "<p>Hello</p><script>ignored()</script><p>world</p>", "content_type": "text/html"},
        )
    )

    assert result.payload["text"] == "Hello world"


def test_answer_format_tool_builds_markdown_sections() -> None:
    result = asyncio.run(
        AnswerFormatTool().execute(
            ToolExecutionContext(session=MagicMock(), query_id=uuid4()),
            {
                "answer_text": "Body",
                "warnings": ["Check freshness"],
                "normative_sources": ["SP 1.0"],
                "external_sources": ["example.com"],
            },
        )
    )

    assert "## Ответ" in result.payload["markdown"]
    assert "## Предупреждения" in result.payload["markdown"]
    assert "- SP 1.0" in result.payload["markdown"]
