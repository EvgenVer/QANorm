"""Orchestrator and planning loop for Stage 2 queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4
import re

from redis import asyncio as redis_asyncio
from sqlalchemy.orm import Session

from qanorm.agents.planner.query_intent import QueryIntent
from qanorm.agents.planner import PlannedSubtask, QueryAnalysis, QueryAnalyzer, QueryTaskDecomposer
from qanorm.audit import AuditWriter
from qanorm.db.types import QueryStatus, SubtaskStatus
from qanorm.models import AuditEvent, QAEvidence, QAQuery, QASubtask
from qanorm.models.qa_state import QueryState, SubtaskState
from qanorm.repositories import AuditEventRepository, QAQueryRepository, QASubtaskRepository
from qanorm.services.qa import ContextService
from qanorm.services.qa.freshness_service import connect_freshness_branch
from qanorm.settings import RuntimeConfig, get_qa_config, get_settings
from qanorm.tools.base import ToolRegistry
from qanorm.workers.stage2 import publish_progress_event


ProgressPublisher = Callable[[UUID, str, dict[str, Any] | None], Awaitable[None]]
OpenWebFallbackScheduler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None] | dict[str, Any] | None]
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
QUESTION_SPLIT_RE = re.compile(r"(?:,|;|\?| и | or )", re.IGNORECASE)


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
        AuditWriter(self.session).write(
            session_id=query.session_id,
            query_id=query.id,
            event_type="planner_bindings_used",
            actor_kind="orchestrator",
            payload_json={
                "query_analyzer_provider": getattr(getattr(self.query_analyzer, "provider", None), "provider_name", "unknown"),
                "query_analyzer_model": getattr(getattr(self.query_analyzer, "provider", None), "model", "unknown"),
                "query_analyzer_prompt": "query_analyzer",
                "query_analyzer_prompt_version": self._resolve_prompt_version(self.query_analyzer, "query_analyzer"),
                "task_decomposer_provider": getattr(getattr(self.task_decomposer, "provider", None), "provider_name", "unknown"),
                "task_decomposer_model": getattr(getattr(self.task_decomposer, "provider", None), "model", "unknown"),
                "task_decomposer_prompt": "task_decomposer",
                "task_decomposer_prompt_version": self._resolve_prompt_version(self.task_decomposer, "task_decomposer"),
            },
        )
        self._record_transition(query=query, event_type="analysis_completed", payload=analysis.to_dict())
        await self._publish(
            query.id,
            "analysis_completed",
            {
                "query_type": analysis.query_type,
                "intent": analysis.intent.value,
                "retrieval_mode": analysis.retrieval_mode.value,
                "clarification_required": analysis.clarification_required,
                "complexity": analysis.complexity.value,
                "used_fallback": analysis.used_fallback,
            },
        )

        planned_subtasks = await self.task_decomposer.decompose(state, analysis)
        if not planned_subtasks:
            planned_subtasks = self.task_decomposer.fallback_subtasks(state.query_text, analysis)
        self._ensure_routes_supported(planned_subtasks)
        persisted = self._persist_subtasks(query=query, state=state, planned_subtasks=planned_subtasks)
        next_status = QueryStatus.SYNTHESIZING if analysis.intent in {QueryIntent.CLARIFY, QueryIntent.NO_RETRIEVAL} else QueryStatus.RETRIEVING
        query.status = next_status
        persisted.status = next_status
        self.session.flush()
        self._record_transition(
            query=query,
            event_type="planning_completed",
            payload={
                "subtask_count": len(planned_subtasks),
                "routes": [task.route for task in planned_subtasks],
                "status": next_status.value,
            },
        )
        await self._publish(
            query.id,
            "planning_completed",
            {
                "subtask_count": len(planned_subtasks),
                "routes": [task.route for task in planned_subtasks],
                "status": next_status.value,
            },
        )
        return QueryPlanningResult(state=persisted, analysis=analysis, planned_subtasks=planned_subtasks)

    async def schedule_freshness_branch(
        self,
        *,
        query_id: UUID,
        evidence_rows: list[QAEvidence],
        scheduler: Callable[[Any], Awaitable[dict[str, Any]] | dict[str, Any] | None] | None = None,
    ) -> list[Any]:
        """Attach the non-blocking freshness branch after normative evidence is available."""

        query = self._require_query(query_id)
        checks = await connect_freshness_branch(
            self.session,
            query=query,
            evidence_rows=evidence_rows,
            scheduler=scheduler,
        )
        if not checks:
            return []
        self._record_transition(
            query=query,
            event_type="freshness_branch_connected",
            payload={"freshness_check_count": len(checks), "non_blocking": True},
        )
        await self._publish(
            query.id,
            "freshness_branch_connected",
            {"freshness_check_count": len(checks), "non_blocking": True},
        )
        return checks

    async def schedule_open_web_fallback(
        self,
        *,
        query_id: UUID,
        state: QueryState,
        scheduler: OpenWebFallbackScheduler | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | None:
        """Activate deferred open-web search only when normative/trusted evidence is insufficient."""

        query = self._require_query(query_id)
        decision = self._assess_open_web_fallback(state=state)
        payload = {
            "reason": decision["reason"],
            "normative_count": decision["normative_count"],
            "trusted_count": decision["trusted_count"],
            "required_support": decision["required_support"],
        }
        if not decision["should_activate"]:
            self._record_transition(query=query, event_type="open_web_fallback_skipped", payload=payload)
            await self._publish(query.id, "open_web_fallback_skipped", payload)
            return None

        subtask = self._activate_or_create_open_web_subtask(query=query, state=state)
        query.used_open_web = True
        state.used_open_web = True
        self.session.flush()

        job_payload = {
            "query_id": str(query.id),
            "subtask_id": str(subtask.id),
            "query_text": state.query_text,
            "limit": int(limit or self.runtime_config.qa.search.open_web_max_results),
            "allowed_domains": [],
        }
        scheduler_result: dict[str, Any] | None = None
        if scheduler is not None:
            scheduled = scheduler(job_payload)
            scheduler_result = await scheduled if hasattr(scheduled, "__await__") else scheduled
            if isinstance(scheduler_result, dict):
                subtask.status = SubtaskStatus.COMPLETED
                subtask.result_summary = f"open_web_results={int(scheduler_result.get('result_count', 0))}"
                self.subtask_repository.save(subtask)
                for item in state.subtasks:
                    if item.subtask_id == subtask.id:
                        item.status = SubtaskStatus.COMPLETED
                        item.result_summary = subtask.result_summary
                        break

        event_payload = payload | {"subtask_id": str(subtask.id), "scheduled": scheduler is not None}
        self._record_transition(query=query, event_type="open_web_fallback_scheduled", payload=event_payload)
        await self._publish(query.id, "open_web_fallback_scheduled", event_payload)
        return scheduler_result or job_payload

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
            intent=query.intent,
            retrieval_mode=query.retrieval_mode,
            clarification_required=query.clarification_required,
            document_hints=list(query.document_hints or []),
            locator_hints=list(query.locator_hints or []),
            recent_messages=prompt_context.recent_messages,
        )

    def _apply_analysis(self, *, query: QAQuery, state: QueryState, analysis: QueryAnalysis) -> None:
        """Write the analysis output into query state and durable query flags."""

        state.query_type = analysis.query_type
        state.intent = analysis.intent.value
        state.retrieval_mode = analysis.retrieval_mode.value
        state.clarification_required = analysis.clarification_required
        state.clarification_question = analysis.clarification_question
        state.document_hints = list(analysis.document_hints)
        state.locator_hints = list(analysis.locator_hints)
        state.subject = analysis.subject
        state.engineering_aspects = list(analysis.engineering_aspects)
        state.constraints = list(analysis.constraints)
        state.requires_freshness_check = analysis.requires_freshness_check
        state.used_trusted_web = False
        state.open_web_fallback_allowed = analysis.requires_open_web
        state.used_open_web = False

        query.query_type = analysis.query_type
        query.intent = analysis.intent.value
        query.clarification_required = analysis.clarification_required
        query.document_hints = list(analysis.document_hints)
        query.locator_hints = list(analysis.locator_hints)
        query.retrieval_mode = analysis.retrieval_mode.value
        query.requires_freshness_check = analysis.requires_freshness_check
        query.used_trusted_web = False
        query.used_open_web = False
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
        has_prior_coverage_routes = any(task.route in {"normative", "trusted_web"} for task in planned_subtasks)
        for task in planned_subtasks:
            initial_status = (
                SubtaskStatus.SKIPPED
                if task.route == "open_web" and has_prior_coverage_routes
                else SubtaskStatus.PENDING
            )
            row = self.subtask_repository.add(
                QASubtask(
                    id=uuid4(),
                    query_id=query.id,
                    parent_subtask_id=None if task.parent_index is None else created_rows[task.parent_index].id,
                    subtask_type=task.subtask_type,
                    description=task.description,
                    status=initial_status,
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
                    result_summary=(
                        "deferred_until_normative_and_trusted_coverage_is_insufficient"
                        if initial_status is SubtaskStatus.SKIPPED and task.route == "open_web"
                        else None
                    ),
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

    def _resolve_prompt_version(self, component: Any, prompt_name: str) -> str:
        """Load prompt version metadata defensively for stubs used in tests."""

        prompt_registry = getattr(component, "prompt_registry", None)
        if prompt_registry is None or not hasattr(prompt_registry, "resolve_version"):
            return "unknown"
        return str(prompt_registry.resolve_version(prompt_name))

    def _assess_open_web_fallback(self, *, state: QueryState) -> dict[str, Any]:
        """Decide whether deferred open-web search should be activated for the current query state."""

        open_web_planned = state.open_web_fallback_allowed or any(
            item.subtask_type == "open_web_search" for item in state.subtasks
        )
        normative_count = len(state.evidence_bundle.normative)
        trusted_count = len(state.evidence_bundle.trusted_web)
        required_support = self._required_support_count(state.query_text)
        combined_support = normative_count + trusted_count

        if not open_web_planned:
            return {
                "should_activate": False,
                "reason": "open_web_not_planned",
                "normative_count": normative_count,
                "trusted_count": trusted_count,
                "required_support": required_support,
            }
        if state.evidence_bundle.open_web:
            return {
                "should_activate": False,
                "reason": "open_web_already_collected",
                "normative_count": normative_count,
                "trusted_count": trusted_count,
                "required_support": required_support,
            }
        if combined_support >= required_support:
            return {
                "should_activate": False,
                "reason": "coverage_sufficient_without_open_web",
                "normative_count": normative_count,
                "trusted_count": trusted_count,
                "required_support": required_support,
            }
        return {
            "should_activate": True,
            "reason": "normative_and_trusted_coverage_insufficient",
            "normative_count": normative_count,
            "trusted_count": trusted_count,
            "required_support": required_support,
        }

    def _activate_or_create_open_web_subtask(self, *, query: QAQuery, state: QueryState) -> QASubtask:
        """Turn a deferred open-web subtask into a runnable one, or create it on demand."""

        existing_rows = self.subtask_repository.list_for_query(query.id)
        deferred_row = next(
            (item for item in existing_rows if item.subtask_type == "open_web_search" and item.status == SubtaskStatus.SKIPPED),
            None,
        )
        if deferred_row is None:
            deferred_row = self.subtask_repository.add(
                QASubtask(
                    id=uuid4(),
                    query_id=query.id,
                    parent_subtask_id=None,
                    subtask_type="open_web_search",
                    description=f"Search open web for unresolved aspects of: {state.query_text[:120]}",
                    status=SubtaskStatus.PENDING,
                    priority=40,
                )
            )
            state.subtasks.append(
                SubtaskState(
                    subtask_id=deferred_row.id,
                    parent_subtask_id=None,
                    subtask_type=deferred_row.subtask_type,
                    description=deferred_row.description,
                    status=deferred_row.status,
                    priority=deferred_row.priority,
                )
            )
            return deferred_row

        deferred_row.status = SubtaskStatus.PENDING
        deferred_row.result_summary = None
        self.subtask_repository.save(deferred_row)
        for item in state.subtasks:
            if item.subtask_id == deferred_row.id:
                item.status = SubtaskStatus.PENDING
                item.result_summary = None
                break
        return deferred_row

    def _required_support_count(self, query_text: str) -> int:
        """Estimate the minimum amount of non-open-web evidence needed before falling back."""

        aspects = [item.strip() for item in QUESTION_SPLIT_RE.split(query_text) if item.strip()]
        return max(1, min(2, len(aspects) or 1))
