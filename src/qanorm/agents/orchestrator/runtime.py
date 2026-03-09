"""Orchestrator and planning loop for Stage 2 queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from redis import asyncio as redis_asyncio
from sqlalchemy.orm import Session

from qanorm.agents.planner import PlannedSubtask, QueryAnalysis, QueryAnalyzer, QueryTaskDecomposer
from qanorm.db.types import QueryStatus, SubtaskStatus
from qanorm.models import AuditEvent, QAQuery, QASubtask
from qanorm.models.qa_state import QueryState, SubtaskState
from qanorm.repositories import AuditEventRepository, QAQueryRepository, QASubtaskRepository
from qanorm.services.qa import ContextService
from qanorm.settings import RuntimeConfig, get_qa_config, get_settings
from qanorm.tools.base import ToolRegistry
from qanorm.workers.stage2 import publish_progress_event


ProgressPublisher = Callable[[UUID, str, dict[str, Any] | None], Awaitable[None]]
ROUTE_SCOPE_MAP = {
    "normative": "normative",
    "freshness": "freshness",
    "trusted_web": "trusted_web",
    "open_web": "open_web",
}
QUERY_ROUTE_FLAGS = {
    "trusted_web": "used_trusted_web",
    "open_web": "used_open_web",
}


@dataclass(slots=True, frozen=True)
class QueryPlanningResult:
    """Planning output returned by the orchestrator before retrieval starts."""

    state: QueryState
    analysis: QueryAnalysis
    planned_subtasks: list[PlannedSubtask]


class QueryOrchestrator:
    """Run the analysis and planning loop for one stored query."""

    def __init__(
        self,
        session: Session,
        *,
        tool_registry: ToolRegistry,
        runtime_config: RuntimeConfig | None = None,
        query_analyzer: QueryAnalyzer | None = None,
        task_decomposer: QueryTaskDecomposer | None = None,
        progress_publisher: ProgressPublisher | None = None,
        context_service: ContextService | None = None,
        query_repository: QAQueryRepository | None = None,
        subtask_repository: QASubtaskRepository | None = None,
        audit_repository: AuditEventRepository | None = None,
    ) -> None:
        self.session = session
        self.runtime_config = runtime_config or get_settings()
        self.tool_registry = tool_registry
        self.context_service = context_service or ContextService(session, qa_config=get_qa_config())
        self.query_repository = query_repository or QAQueryRepository(session)
        self.subtask_repository = subtask_repository or QASubtaskRepository(session)
        self.audit_repository = audit_repository or AuditEventRepository(session)
        self.query_analyzer = query_analyzer or QueryAnalyzer(runtime_config=self.runtime_config)
        self.task_decomposer = task_decomposer or QueryTaskDecomposer(runtime_config=self.runtime_config)
        self.progress_publisher = progress_publisher

    @classmethod
    def with_redis_progress(
        cls,
        session: Session,
        *,
        tool_registry: ToolRegistry,
        redis: redis_asyncio.Redis,
        runtime_config: RuntimeConfig | None = None,
        query_analyzer: QueryAnalyzer | None = None,
        task_decomposer: QueryTaskDecomposer | None = None,
    ) -> "QueryOrchestrator":
        """Build an orchestrator that publishes progress into the Redis SSE bridge."""

        async def _publisher(query_id: UUID, event: str, data: dict[str, Any] | None = None) -> None:
            await publish_progress_event(redis, query_id=query_id, event=event, data=data)

        return cls(
            session,
            tool_registry=tool_registry,
            runtime_config=runtime_config,
            query_analyzer=query_analyzer,
            task_decomposer=task_decomposer,
            progress_publisher=_publisher,
        )

    async def analyze_and_plan(self, *, query_id: UUID) -> QueryPlanningResult:
        """Load one stored query, analyze it, persist subtasks, and emit progress."""

        query = self._require_query(query_id)
        state = self._load_state(query)
        await self._publish(query.id, "analysis_started", {"query_id": str(query.id)})
        self._record_transition(query=query, event_type="analysis_started", payload={"status": QueryStatus.ANALYZING.value})

        query.status = QueryStatus.ANALYZING
        self.session.flush()

        analysis = await self.query_analyzer.analyze(state)
        self._apply_analysis(query=query, state=state, analysis=analysis)
        self._record_transition(query=query, event_type="analysis_completed", payload=analysis.to_dict())
        await self._publish(
            query.id,
            "analysis_completed",
            {
                "query_type": analysis.query_type,
                "complexity": analysis.complexity.value,
                "used_fallback": analysis.used_fallback,
            },
        )

        planned_subtasks = await self.task_decomposer.decompose(state, analysis)
        if not planned_subtasks:
            planned_subtasks = self.task_decomposer.fallback_subtasks(state.query_text, analysis)
        self._ensure_routes_supported(planned_subtasks)
        persisted = self._persist_subtasks(query=query, state=state, planned_subtasks=planned_subtasks)
        query.status = QueryStatus.RETRIEVING
        persisted.status = QueryStatus.RETRIEVING
        self.session.flush()
        self._record_transition(
            query=query,
            event_type="planning_completed",
            payload={
                "subtask_count": len(planned_subtasks),
                "routes": [task.route for task in planned_subtasks],
                "status": QueryStatus.RETRIEVING.value,
            },
        )
        await self._publish(
            query.id,
            "planning_completed",
            {
                "subtask_count": len(planned_subtasks),
                "routes": [task.route for task in planned_subtasks],
                "status": QueryStatus.RETRIEVING.value,
            },
        )
        return QueryPlanningResult(state=persisted, analysis=analysis, planned_subtasks=planned_subtasks)

    def _require_query(self, query_id: UUID) -> QAQuery:
        """Load one stored query or fail fast."""

        query = self.query_repository.get(query_id)
        if query is None:
            raise ValueError(f"Query not found: {query_id}")
        return query

    def _load_state(self, query: QAQuery) -> QueryState:
        """Build the runtime QueryState from persisted session context."""

        prompt_context = self.context_service.load_prompt_context(
            session_id=query.session_id,
            query_text=query.query_text,
            query_id=query.id,
        )
        if prompt_context is None:
            raise ValueError(f"Session not found for query {query.id}")
        return QueryState(
            session_id=query.session_id,
            query_id=query.id,
            message_id=query.message_id,
            query_text=query.query_text,
            status=query.status,
            query_type=query.query_type,
            session_summary=prompt_context.session_summary,
            recent_messages=prompt_context.recent_messages,
        )

    def _apply_analysis(self, *, query: QAQuery, state: QueryState, analysis: QueryAnalysis) -> None:
        """Write the analysis output into query state and durable query flags."""

        state.query_type = analysis.query_type
        state.requires_freshness_check = analysis.requires_freshness_check
        state.used_trusted_web = analysis.requires_trusted_web
        state.used_open_web = analysis.requires_open_web

        query.query_type = analysis.query_type
        query.requires_freshness_check = analysis.requires_freshness_check
        query.used_trusted_web = analysis.requires_trusted_web
        query.used_open_web = analysis.requires_open_web
        self.session.flush()

    def _persist_subtasks(
        self,
        *,
        query: QAQuery,
        state: QueryState,
        planned_subtasks: list[PlannedSubtask],
    ) -> QueryState:
        """Persist the planned tasks into qa_subtasks and mirror them into QueryState."""

        created_rows: list[QASubtask] = []
        for task in planned_subtasks:
            row = self.subtask_repository.add(
                QASubtask(
                    id=uuid4(),
                    query_id=query.id,
                    parent_subtask_id=None if task.parent_index is None else created_rows[task.parent_index].id,
                    subtask_type=task.subtask_type,
                    description=task.description,
                    status=SubtaskStatus.PENDING,
                    priority=task.priority,
                )
            )
            created_rows.append(row)
            state.subtasks.append(
                SubtaskState(
                    subtask_id=row.id,
                    parent_subtask_id=row.parent_subtask_id,
                    subtask_type=row.subtask_type,
                    description=row.description,
                    status=row.status,
                    priority=row.priority,
                )
            )
        return state

    def _ensure_routes_supported(self, planned_subtasks: list[PlannedSubtask]) -> None:
        """Require the shared tool registry to expose scopes for all planned routes."""

        available_scopes = {definition.scope for definition in self.tool_registry.list_registered().values()}
        missing_scopes = sorted(
            {
                ROUTE_SCOPE_MAP[task.route]
                for task in planned_subtasks
                if task.route in ROUTE_SCOPE_MAP and ROUTE_SCOPE_MAP[task.route] not in available_scopes
            }
        )
        if missing_scopes:
            joined = ", ".join(missing_scopes)
            raise ValueError(f"Tool registry does not expose required scopes: {joined}")

    def _record_transition(self, *, query: QAQuery, event_type: str, payload: dict[str, Any]) -> None:
        """Persist one orchestrator audit event."""

        self.audit_repository.add(
            AuditEvent(
                session_id=query.session_id,
                query_id=query.id,
                event_type=event_type,
                actor_kind="orchestrator",
                payload_json=payload,
            )
        )

    async def _publish(self, query_id: UUID, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit one progress event when the orchestrator was configured with a publisher."""

        if self.progress_publisher is None:
            return
        await self.progress_publisher(query_id, event, data)
