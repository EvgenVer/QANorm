from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.agents.orchestrator import QueryOrchestrator
from qanorm.agents.planner import PlannedSubtask, QueryAnalysis, QueryAnalyzer, QueryComplexity, QueryTaskDecomposer
from qanorm.db.types import EvidenceSourceKind, MessageRole, QueryStatus, SessionChannel, SessionStatus, SubtaskStatus
from qanorm.models import QAEvidence, QAMessage, QAQuery, QASession
from qanorm.models.qa_state import EvidenceBundle, PromptRenderContext
from qanorm.prompts.registry import create_prompt_registry
from qanorm.providers.base import ChatModelProvider, ChatRequest, ChatResponse, ProviderCapabilities, ProviderName
from qanorm.tools.base import Tool, ToolDefinition, ToolRegistry
from tests.unit.test_provider_registry import _runtime_config


class _FakeChatProvider(ChatModelProvider):
    """Minimal chat provider stub used to exercise planner code paths."""

    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(chat=True)

    def __init__(self, content: str) -> None:
        self.model = "planner-test"
        self._content = content

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(provider=self.provider_name, model=request.model, content=self._content)


class _DummyTool(Tool):
    """Tool stub that only advertises one scope to the registry."""

    def __init__(self, name: str, scope: str) -> None:
        self.definition = ToolDefinition(name=name, scope=scope, description="test tool")

    async def execute(self, context, payload):  # pragma: no cover - not exercised in this block.
        raise AssertionError("Tool execution is out of scope for planner tests")


@dataclass
class _InMemoryQueryRepository:
    """Simple repository stub for orchestrator unit tests."""

    query: QAQuery

    def get(self, query_id):
        assert query_id == self.query.id
        return self.query


@dataclass
class _InMemorySubtaskRepository:
    """Capture persisted subtasks without needing a real database."""

    rows: list = None

    def __post_init__(self) -> None:
        if self.rows is None:
            self.rows = []

    def add(self, subtask):
        self.rows.append(subtask)
        return subtask

    def save(self, subtask):
        return subtask

    def list_for_query(self, query_id):
        return [row for row in self.rows if row.query_id == query_id]


@dataclass
class _InMemoryAuditRepository:
    """Capture orchestrator audit transitions for assertions."""

    rows: list = None

    def __post_init__(self) -> None:
        if self.rows is None:
            self.rows = []

    def add(self, event):
        self.rows.append(event)
        return event


class _ContextServiceStub:
    """Return a deterministic prompt context snapshot for one query."""

    def __init__(self, session_id, query_id, query_text) -> None:
        self._context = PromptRenderContext(
            session_id=session_id,
            query_id=query_id,
            query_text=query_text,
            recent_messages=[QAMessage(session_id=session_id, role=MessageRole.USER, content=query_text)],
        )

    def load_prompt_context(self, session_id, query_text, query_id):
        assert session_id == self._context.session_id
        assert query_text == self._context.query_text
        assert query_id == self._context.query_id
        return self._context


def test_query_analyzer_parses_model_json_response() -> None:
    runtime_config = _runtime_config()
    registry = create_prompt_registry(runtime_config)
    provider = _FakeChatProvider(
        """
        {
          "query_type": "mixed",
          "complexity": "multi_aspect",
          "requires_normative_retrieval": true,
          "requires_freshness_check": true,
          "requires_trusted_web": true,
          "requires_open_web": false,
          "engineering_aspects": ["fire safety", "evacuation"],
          "constraints": ["for residential building"],
          "assumptions": ["RF jurisdiction"]
        }
        """
    )
    analyzer = QueryAnalyzer(runtime_config=runtime_config, prompt_registry=registry, provider=provider)
    state = _build_query_state("Какие требования по пожарной безопасности и эвакуации для жилого дома?")

    analysis = asyncio.run(analyzer.analyze(state))

    assert analysis.query_type == "mixed"
    assert analysis.complexity == QueryComplexity.MULTI_ASPECT
    assert analysis.requires_trusted_web is True
    assert analysis.engineering_aspects == ["fire safety", "evacuation"]
    assert analysis.used_fallback is False


def test_query_analyzer_uses_deterministic_fallback_for_invalid_model_output() -> None:
    analyzer = QueryAnalyzer(
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("not-json"),
    )
    state = _build_query_state("Найди в интернете и в нормах требования к огнестойкости перекрытия")

    analysis = asyncio.run(analyzer.analyze(state))

    assert analysis.used_fallback is True
    assert analysis.requires_normative_retrieval is True
    assert analysis.requires_open_web is True
    assert analysis.requires_freshness_check is True


def test_task_decomposer_routes_all_required_sources() -> None:
    decomposer = QueryTaskDecomposer(
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("not-json"),
    )
    analysis = QueryAnalysis(
        query_type="mixed",
        complexity=QueryComplexity.COMPLEX,
        requires_normative_retrieval=True,
        requires_freshness_check=True,
        requires_trusted_web=True,
        requires_open_web=True,
        engineering_aspects=["aspect"],
    )

    tasks = asyncio.run(decomposer.decompose(_build_query_state("сложный запрос"), analysis))

    assert [task.route for task in tasks] == ["normative", "freshness", "trusted_web", "open_web"]
    assert [task.priority for task in tasks] == [10, 20, 30, 40]


def test_query_orchestrator_persists_subtasks_records_audit_and_publishes_progress() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="Нужно определить требования и при необходимости проверить внешние источники",
        status=QueryStatus.PENDING,
    )
    subtask_repository = _InMemorySubtaskRepository()
    audit_repository = _InMemoryAuditRepository()
    published_events: list[tuple[str, dict]] = []

    async def _publisher(query_id, event, data=None):
        published_events.append((event, data or {}))

    orchestrator = QueryOrchestrator(
        MagicMock(),
        tool_registry=_build_tool_registry(),
        runtime_config=_runtime_config(),
        query_analyzer=SimpleNamespace(
            analyze=_async_return(
                QueryAnalysis(
                    query_type="mixed",
                    complexity=QueryComplexity.MULTI_ASPECT,
                    requires_normative_retrieval=True,
                    requires_freshness_check=True,
                    requires_trusted_web=False,
                    requires_open_web=True,
                    engineering_aspects=["requirements", "external validation"],
                )
            )
        ),
        task_decomposer=SimpleNamespace(
            decompose=_async_return(
                [
                    PlannedSubtask("normative_retrieval", "Retrieve norms", 10, "normative"),
                    PlannedSubtask("freshness_check", "Check freshness", 20, "freshness"),
                    PlannedSubtask("open_web_search", "Search open web", 40, "open_web"),
                ]
            ),
            fallback_subtasks=lambda query_text, analysis: [],
        ),
        progress_publisher=_publisher,
        context_service=_ContextServiceStub(query.session_id, query.id, query.query_text),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=subtask_repository,
        audit_repository=audit_repository,
    )

    result = asyncio.run(orchestrator.analyze_and_plan(query_id=query.id))

    assert query.status == QueryStatus.RETRIEVING
    assert query.query_type == "mixed"
    assert query.requires_freshness_check is True
    assert query.used_open_web is False
    assert [row.subtask_type for row in subtask_repository.rows] == [
        "normative_retrieval",
        "freshness_check",
        "open_web_search",
    ]
    assert [row.status for row in subtask_repository.rows] == [
        SubtaskStatus.PENDING,
        SubtaskStatus.PENDING,
        SubtaskStatus.SKIPPED,
    ]
    assert [subtask.subtask_type for subtask in result.state.subtasks] == [
        "normative_retrieval",
        "freshness_check",
        "open_web_search",
    ]
    assert [event.event_type for event in audit_repository.rows] == [
        "analysis_started",
        "analysis_completed",
        "planning_completed",
    ]
    assert [item[0] for item in published_events] == [
        "analysis_started",
        "analysis_completed",
        "planning_completed",
    ]


def test_query_orchestrator_schedules_open_web_fallback_only_after_insufficient_coverage() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="Need external guidance for a niche facade topic, installation tolerances after checking norms.",
        status=QueryStatus.RETRIEVING,
        query_type="mixed",
        used_open_web=False,
    )
    subtask_repository = _InMemorySubtaskRepository()
    open_web_row = subtask_repository.add(
        type(
            "_Subtask",
            (),
            {
                "id": uuid4(),
                "query_id": query.id,
                "parent_subtask_id": None,
                "subtask_type": "open_web_search",
                "description": "Deferred open web search",
                "status": SubtaskStatus.SKIPPED,
                "priority": 40,
                "result_summary": "deferred_until_normative_and_trusted_coverage_is_insufficient",
            },
        )()
    )
    published_events: list[tuple[str, dict]] = []

    async def _publisher(query_id, event, data=None):
        published_events.append((event, data or {}))

    orchestrator = QueryOrchestrator(
        MagicMock(),
        tool_registry=_build_tool_registry(),
        runtime_config=_runtime_config(),
        progress_publisher=_publisher,
        context_service=_ContextServiceStub(query.session_id, query.id, query.query_text),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=subtask_repository,
        audit_repository=_InMemoryAuditRepository(),
    )
    state = _build_runtime_state(query)
    state.subtasks.append(
        type(
            "_StateSubtask",
            (),
            {
                "subtask_id": open_web_row.id,
                "parent_subtask_id": None,
                "subtask_type": "open_web_search",
                "description": open_web_row.description,
                "status": SubtaskStatus.SKIPPED,
                "priority": 40,
                "result_summary": open_web_row.result_summary,
            },
        )()
    )
    state.evidence_bundle = EvidenceBundle(
        normative=[
            QAEvidence(
                query_id=query.id,
                source_kind=EvidenceSourceKind.NORMATIVE,
                quote="One narrow requirement",
                chunk_text="One narrow requirement",
            )
        ],
        trusted_web=[],
        open_web=[],
    )
    state.open_web_fallback_allowed = True

    async def _scheduler(payload):
        return {"result_count": 2, "payload": payload}

    result = asyncio.run(orchestrator.schedule_open_web_fallback(query_id=query.id, state=state, scheduler=_scheduler))

    assert result is not None
    assert query.used_open_web is True
    assert open_web_row.status == SubtaskStatus.COMPLETED
    assert any(event == "open_web_fallback_scheduled" for event, _ in published_events)


def test_query_orchestrator_skips_open_web_fallback_when_normative_and_trusted_are_sufficient() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="Compare two design options and use only verified sources.",
        status=QueryStatus.RETRIEVING,
        query_type="mixed",
        used_open_web=False,
    )
    subtask_repository = _InMemorySubtaskRepository()
    open_web_row = subtask_repository.add(
        type(
            "_Subtask",
            (),
            {
                "id": uuid4(),
                "query_id": query.id,
                "parent_subtask_id": None,
                "subtask_type": "open_web_search",
                "description": "Deferred open web search",
                "status": SubtaskStatus.SKIPPED,
                "priority": 40,
                "result_summary": "deferred",
            },
        )()
    )

    orchestrator = QueryOrchestrator(
        MagicMock(),
        tool_registry=_build_tool_registry(),
        runtime_config=_runtime_config(),
        context_service=_ContextServiceStub(query.session_id, query.id, query.query_text),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=subtask_repository,
        audit_repository=_InMemoryAuditRepository(),
    )
    state = _build_runtime_state(query)
    state.subtasks.append(
        type(
            "_StateSubtask",
            (),
            {
                "subtask_id": open_web_row.id,
                "parent_subtask_id": None,
                "subtask_type": "open_web_search",
                "description": open_web_row.description,
                "status": SubtaskStatus.SKIPPED,
                "priority": 40,
                "result_summary": open_web_row.result_summary,
            },
        )()
    )
    state.evidence_bundle = EvidenceBundle(
        normative=[
            QAEvidence(
                query_id=query.id,
                source_kind=EvidenceSourceKind.NORMATIVE,
                quote="Requirement one",
                chunk_text="Requirement one",
            ),
            QAEvidence(
                query_id=query.id,
                source_kind=EvidenceSourceKind.NORMATIVE,
                quote="Requirement two",
                chunk_text="Requirement two",
            ),
        ],
        trusted_web=[
            QAEvidence(
                query_id=query.id,
                source_kind=EvidenceSourceKind.TRUSTED_WEB,
                quote="Trusted clarification",
                chunk_text="Trusted clarification",
            )
        ],
        open_web=[],
    )
    state.open_web_fallback_allowed = True

    result = asyncio.run(orchestrator.schedule_open_web_fallback(query_id=query.id, state=state, scheduler=None))

    assert result is None
    assert query.used_open_web is False
    assert open_web_row.status == SubtaskStatus.SKIPPED


def _build_query_state(query_text: str):
    session_id = uuid4()
    return SimpleNamespace(
        session_id=session_id,
        query_id=uuid4(),
        message_id=uuid4(),
        query_text=query_text,
        build_prompt_context=lambda: PromptRenderContext(
            session_id=session_id,
            query_id=uuid4(),
            query_text=query_text,
            recent_messages=[QAMessage(session_id=session_id, role=MessageRole.USER, content=query_text)],
        ),
    )


def _build_runtime_state(query: QAQuery):
    return type(
        "_State",
        (),
        {
            "session_id": query.session_id,
            "query_id": query.id,
            "message_id": query.message_id,
            "query_text": query.query_text,
            "status": query.status,
            "query_type": query.query_type,
            "session_summary": None,
            "recent_messages": [],
            "subtasks": [],
            "evidence_bundle": EvidenceBundle(),
            "verification_attempt_count": 0,
            "repair_attempt_count": 0,
            "tool_call_count": 0,
            "attempt_deadline": None,
            "evidence_fingerprint": None,
            "verification_fingerprint": None,
            "used_open_web": False,
            "used_trusted_web": False,
            "open_web_fallback_allowed": False,
            "requires_freshness_check": False,
        },
    )()


def _build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_DummyTool("normative_search", "normative"))
    registry.register(_DummyTool("freshness_check", "freshness"))
    registry.register(_DummyTool("trusted_search", "trusted_web"))
    registry.register(_DummyTool("open_web_search", "open_web"))
    return registry


def _async_return(value):
    async def _runner(*args, **kwargs):
        return value

    return _runner
