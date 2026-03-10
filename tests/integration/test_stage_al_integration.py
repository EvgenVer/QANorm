from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from qanorm.agents.orchestrator import QueryOrchestrator
from qanorm.db.types import EvidenceSourceKind, MessageRole, QueryStatus, SessionChannel, SessionStatus, SubtaskStatus
from qanorm.models import QAEvidence, QAMessage, QAQuery, QASession
from qanorm.models.qa_state import EvidenceBundle, PromptRenderContext, QueryState, SubtaskState
from qanorm.tools.base import Tool, ToolDefinition, ToolRegistry
from tests.unit.test_provider_registry import _runtime_config


class _RecordingSession:
    """Capture writes emitted by the fallback path without requiring a database."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, row: object) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flush_count += 1


class _DummyTool(Tool):
    """Advertise one scope so the orchestrator can validate planned routes."""

    def __init__(self, name: str, scope: str) -> None:
        self.definition = ToolDefinition(name=name, scope=scope, description="integration test tool")

    async def execute(self, context, payload):  # pragma: no cover - not executed here.
        raise AssertionError("Tool execution is outside AL integration coverage")


@dataclass
class _InMemoryQueryRepository:
    query: QAQuery

    def get(self, query_id):
        assert query_id == self.query.id
        return self.query


@dataclass
class _InMemorySubtaskRepository:
    rows: list = field(default_factory=list)

    def add(self, subtask):
        self.rows.append(subtask)
        return subtask

    def save(self, subtask):
        return subtask

    def list_for_query(self, query_id):
        return [row for row in self.rows if row.query_id == query_id]


@dataclass
class _InMemoryAuditRepository:
    rows: list = field(default_factory=list)

    def add(self, event):
        self.rows.append(event)
        return event


class _ContextServiceStub:
    """Return a deterministic prompt context for one stored query."""

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


def test_408_integration_orchestrator_defers_open_web_until_normative_and_trusted_are_insufficient() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="Find any remaining public guidance for an unresolved facade detailing question, installation tolerances.",
        status=QueryStatus.RETRIEVING,
        query_type="mixed",
        used_open_web=False,
    )
    session = _RecordingSession()
    subtask_repository = _InMemorySubtaskRepository()
    audit_repository = _InMemoryAuditRepository()
    deferred = subtask_repository.add(
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

    orchestrator = QueryOrchestrator(
        session,
        tool_registry=_build_tool_registry(),
        runtime_config=_runtime_config(),
        context_service=_ContextServiceStub(query),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=subtask_repository,
        audit_repository=audit_repository,
    )
    state = QueryState(
        session_id=query.session_id,
        query_id=query.id,
        message_id=query.message_id,
        query_text=query.query_text,
        status=QueryStatus.RETRIEVING,
        query_type="mixed",
        evidence_bundle=EvidenceBundle(
            normative=[
                QAEvidence(
                    query_id=query.id,
                    source_kind=EvidenceSourceKind.NORMATIVE,
                    quote="Only one narrow requirement was found.",
                    chunk_text="Only one narrow requirement was found.",
                )
            ],
            trusted_web=[],
            open_web=[],
        ),
        subtasks=[
            SubtaskState(
                subtask_id=deferred.id,
                parent_subtask_id=None,
                subtask_type="open_web_search",
                description=deferred.description,
                status=SubtaskStatus.SKIPPED,
                priority=40,
                result_summary=deferred.result_summary,
            )
        ],
        open_web_fallback_allowed=True,
    )

    async def _scheduler(payload):
        return {"result_count": 2, "payload": payload}

    result = asyncio.run(
        orchestrator.schedule_open_web_fallback(
            query_id=query.id,
            state=state,
            scheduler=_scheduler,
        )
    )

    assert result is not None
    assert query.used_open_web is True
    assert deferred.status == SubtaskStatus.COMPLETED
    assert any(event.event_type == "open_web_fallback_scheduled" for event in audit_repository.rows)


def _build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_DummyTool("normative_search", "normative"))
    registry.register(_DummyTool("freshness_check", "freshness"))
    registry.register(_DummyTool("trusted_search", "trusted_web"))
    registry.register(_DummyTool("open_web_search", "open_web"))
    return registry
