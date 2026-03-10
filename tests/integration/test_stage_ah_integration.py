from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from qanorm.agents.orchestrator import QueryOrchestrator
from qanorm.agents.planner import QueryAnalyzer, QueryTaskDecomposer
from qanorm.db.types import MessageRole, QueryStatus, SessionChannel, SessionStatus
from qanorm.models import AuditEvent, QAMessage, QAQuery, QASession
from qanorm.models.qa_state import PromptRenderContext
from qanorm.prompts.registry import create_prompt_registry
from qanorm.providers.base import ChatModelProvider, ChatRequest, ChatResponse, ProviderCapabilities, ProviderName
from qanorm.tools.base import Tool, ToolDefinition, ToolRegistry
from tests.unit.test_provider_registry import _runtime_config


class _RecordingSession:
    """Capture rows written through repository helpers without a real database."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, row: object) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flush_count += 1


class _FakeChatProvider(ChatModelProvider):
    """Return one deterministic payload so planner tests stay stable."""

    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(chat=True)

    def __init__(self, content: str) -> None:
        self.model = "planner-integration-test"
        self._content = content

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(provider=self.provider_name, model=request.model, content=self._content)


class _DummyTool(Tool):
    """Advertise one tool scope to the registry without executing anything."""

    def __init__(self, name: str, scope: str) -> None:
        self.definition = ToolDefinition(name=name, scope=scope, description="integration test tool")

    async def execute(self, context, payload):  # pragma: no cover - not exercised here.
        raise AssertionError("Tool execution is outside planner integration coverage")


@dataclass
class _InMemoryQueryRepository:
    """Return one persisted query row by id."""

    query: QAQuery

    def get(self, query_id):
        assert query_id == self.query.id
        return self.query


@dataclass
class _InMemorySubtaskRepository:
    """Capture planned subtasks as durable rows."""

    rows: list = field(default_factory=list)

    def add(self, subtask):
        self.rows.append(subtask)
        return subtask


@dataclass
class _InMemoryAuditRepository:
    """Capture orchestrator transition events written through the repository path."""

    rows: list = field(default_factory=list)

    def add(self, event):
        self.rows.append(event)
        return event


class _ContextServiceStub:
    """Return deterministic prompt context for one stored query."""

    def __init__(self, query: QAQuery) -> None:
        self._session = QASession(id=query.session_id, channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
        self._message = QAMessage(id=query.message_id, session_id=query.session_id, role=MessageRole.USER, content=query.query_text)

    def load_prompt_context(self, session_id, query_text, query_id):
        return PromptRenderContext(
            session_id=self._session.id,
            query_id=query_id,
            query_text=query_text,
            recent_messages=[self._message],
        )


def test_402_integration_orchestrator_handles_simple_normative_query() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="What does SP 35.13330.2011 require for bridges and culverts?",
        status=QueryStatus.PENDING,
    )
    session = _RecordingSession()
    subtask_repository = _InMemorySubtaskRepository()
    audit_repository = _InMemoryAuditRepository()
    runtime_config = _runtime_config()
    prompt_registry = create_prompt_registry(runtime_config)

    orchestrator = QueryOrchestrator(
        session,
        tool_registry=_build_tool_registry(),
        runtime_config=runtime_config,
        query_analyzer=QueryAnalyzer(
            runtime_config=runtime_config,
            prompt_registry=prompt_registry,
            provider=_FakeChatProvider(
                """
                {
                  "query_type": "normative",
                  "complexity": "simple",
                  "requires_normative_retrieval": true,
                  "requires_freshness_check": true,
                  "requires_trusted_web": false,
                  "requires_open_web": false,
                  "engineering_aspects": ["bridges and culverts"],
                  "constraints": [],
                  "assumptions": []
                }
                """
            ),
        ),
        task_decomposer=QueryTaskDecomposer(
            runtime_config=runtime_config,
            prompt_registry=prompt_registry,
            provider=_FakeChatProvider(
                """
                {
                  "subtasks": [
                    {
                      "subtask_type": "normative_retrieval",
                      "description": "Retrieve the applicable bridge and culvert requirements.",
                      "priority": 10,
                      "route": "normative",
                      "parent_index": null
                    },
                    {
                      "subtask_type": "freshness_check",
                      "description": "Check whether the referenced code version is current.",
                      "priority": 20,
                      "route": "freshness",
                      "parent_index": null
                    }
                  ]
                }
                """
            ),
        ),
        context_service=_ContextServiceStub(query),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=subtask_repository,
        audit_repository=audit_repository,
    )

    result = asyncio.run(orchestrator.analyze_and_plan(query_id=query.id))

    assert query.status == QueryStatus.RETRIEVING
    assert query.query_type == "normative"
    assert query.requires_freshness_check is True
    assert query.used_trusted_web is False
    assert query.used_open_web is False
    assert [row.subtask_type for row in subtask_repository.rows] == ["normative_retrieval", "freshness_check"]
    assert [row.subtask_type for row in result.state.subtasks] == ["normative_retrieval", "freshness_check"]
    assert [event.event_type for event in audit_repository.rows] == [
        "analysis_started",
        "analysis_completed",
        "planning_completed",
    ]
    assert any(isinstance(row, AuditEvent) and row.event_type == "planner_bindings_used" for row in session.added)


def test_403_integration_orchestrator_handles_multi_aspect_query() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="Compare fire safety and evacuation requirements for an underground station and note any useful external guidance.",
        status=QueryStatus.PENDING,
    )
    session = _RecordingSession()
    subtask_repository = _InMemorySubtaskRepository()
    audit_repository = _InMemoryAuditRepository()
    runtime_config = _runtime_config()
    prompt_registry = create_prompt_registry(runtime_config)

    orchestrator = QueryOrchestrator(
        session,
        tool_registry=_build_tool_registry(),
        runtime_config=runtime_config,
        query_analyzer=QueryAnalyzer(
            runtime_config=runtime_config,
            prompt_registry=prompt_registry,
            provider=_FakeChatProvider(
                """
                {
                  "query_type": "mixed",
                  "complexity": "multi_aspect",
                  "requires_normative_retrieval": true,
                  "requires_freshness_check": true,
                  "requires_trusted_web": true,
                  "requires_open_web": true,
                  "engineering_aspects": ["fire safety", "evacuation", "external guidance"],
                  "constraints": ["underground station"],
                  "assumptions": []
                }
                """
            ),
        ),
        task_decomposer=QueryTaskDecomposer(
            runtime_config=runtime_config,
            prompt_registry=prompt_registry,
            provider=_FakeChatProvider(
                """
                {
                  "subtasks": [
                    {
                      "subtask_type": "normative_retrieval",
                      "description": "Retrieve metro fire safety requirements.",
                      "priority": 10,
                      "route": "normative",
                      "parent_index": null
                    },
                    {
                      "subtask_type": "freshness_check",
                      "description": "Check freshness for referenced metro codes.",
                      "priority": 20,
                      "route": "freshness",
                      "parent_index": null
                    },
                    {
                      "subtask_type": "trusted_web_search",
                      "description": "Search trusted guidance sources for clarifications.",
                      "priority": 30,
                      "route": "trusted_web",
                      "parent_index": null
                    },
                    {
                      "subtask_type": "open_web_search",
                      "description": "Search the open web for unresolved aspects.",
                      "priority": 40,
                      "route": "open_web",
                      "parent_index": null
                    }
                  ]
                }
                """
            ),
        ),
        context_service=_ContextServiceStub(query),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=subtask_repository,
        audit_repository=audit_repository,
    )

    result = asyncio.run(orchestrator.analyze_and_plan(query_id=query.id))

    assert query.status == QueryStatus.RETRIEVING
    assert query.query_type == "mixed"
    assert query.requires_freshness_check is True
    assert query.used_trusted_web is True
    assert query.used_open_web is True
    assert [row.subtask_type for row in subtask_repository.rows] == [
        "normative_retrieval",
        "freshness_check",
        "trusted_web_search",
        "open_web_search",
    ]
    assert [subtask.subtask_type for subtask in result.state.subtasks] == [
        "normative_retrieval",
        "freshness_check",
        "trusted_web_search",
        "open_web_search",
    ]
    assert [subtask.priority for subtask in result.state.subtasks] == [10, 20, 30, 40]
    assert session.flush_count > 0


def _build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_DummyTool("normative_search", "normative"))
    registry.register(_DummyTool("freshness_check", "freshness"))
    registry.register(_DummyTool("trusted_search", "trusted_web"))
    registry.register(_DummyTool("open_web_search", "open_web"))
    return registry
