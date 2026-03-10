"""Task decomposition for Stage 2 orchestration."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from qanorm.agents.planner.query_analyzer import JSON_OBJECT_RE, QueryAnalysis, QueryComplexity
from qanorm.models.qa_state import QueryState
from qanorm.prompts.registry import PromptRegistry, create_prompt_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import ChatMessage, ChatModelProvider, ChatRequest, create_role_bound_providers
from qanorm.settings import RuntimeConfig, get_settings


@dataclass(slots=True, frozen=True)
class PlannedSubtask:
    """One decomposed work item ready to persist into qa_subtasks."""

    subtask_type: str
    description: str
    priority: int
    route: str
    parent_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Expose a JSON-serializable shape for audit logging."""

        return asdict(self)


class QueryTaskDecomposer:
    """Turn one analyzed query into a bounded list of routed subtasks."""

    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig | None = None,
        prompt_registry: PromptRegistry | None = None,
        provider: ChatModelProvider | None = None,
    ) -> None:
        self.runtime_config = runtime_config or get_settings()
        self.prompt_registry = prompt_registry or create_prompt_registry(self.runtime_config)
        if provider is None:
            bindings = create_role_bound_providers(
                registry=create_provider_registry(),
                runtime_config=self.runtime_config,
            )
            provider = bindings.orchestration
        self.provider = provider

    async def decompose(self, state: QueryState, analysis: QueryAnalysis) -> list[PlannedSubtask]:
        """Decompose the query into routed subtasks with deterministic fallback."""

        prompt = self.prompt_registry.render("task_decomposer", context=state.build_prompt_context())
        try:
            response = await self.provider.generate(
                ChatRequest(
                    model=self.provider.model,
                    messages=[
                        ChatMessage(role="system", content=prompt.text),
                        ChatMessage(
                            role="user",
                            content=self._decomposition_instruction(state.query_text, analysis),
                        ),
                    ],
                    temperature=0.0,
                    max_tokens=900,
                    metadata={"prompt_metadata": prompt.metadata},
                )
            )
            parsed = self._parse_decomposition_response(response.content)
        except Exception:
            parsed = []
        if parsed:
            return self._normalize_priorities(parsed)
        return self.fallback_subtasks(state.query_text, analysis)

    def _decomposition_instruction(self, query_text: str, analysis: QueryAnalysis) -> str:
        """Force JSON output that can be persisted into qa_subtasks."""

        schema = {
            "subtasks": [
                {
                    "subtask_type": "normative_retrieval|freshness_check|trusted_web_search|open_web_search",
                    "description": "short description",
                    "priority": 10,
                    "route": "normative|freshness|trusted_web|open_web",
                    "parent_index": None,
                }
            ]
        }
        return (
            "Return only one JSON object using this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Query:\n{query_text}\n\nAnalysis:\n{json.dumps(analysis.to_dict(), ensure_ascii=False)}"
        )

    def _parse_decomposition_response(self, content: str) -> list[PlannedSubtask]:
        """Parse the first JSON task list returned by the model."""

        match = JSON_OBJECT_RE.search(content)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        raw_subtasks = payload.get("subtasks")
        if not isinstance(raw_subtasks, list):
            return []

        planned: list[PlannedSubtask] = []
        for item in raw_subtasks:
            if not isinstance(item, dict):
                return []
            try:
                planned.append(
                    PlannedSubtask(
                        subtask_type=str(item["subtask_type"]).strip(),
                        description=str(item["description"]).strip(),
                        priority=int(item["priority"]),
                        route=str(item["route"]).strip(),
                        parent_index=int(item["parent_index"]) if item.get("parent_index") is not None else None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                return []
        return planned

    def fallback_subtasks(self, query_text: str, analysis: QueryAnalysis) -> list[PlannedSubtask]:
        """Deterministic decomposition aligned to the agreed source precedence."""

        tasks: list[PlannedSubtask] = []
        summary = _summarize_query(query_text)
        if analysis.requires_normative_retrieval:
            tasks.append(
                PlannedSubtask(
                    subtask_type="normative_retrieval",
                    description=f"Retrieve normative evidence for: {summary}",
                    priority=10,
                    route="normative",
                )
            )
        if analysis.requires_freshness_check:
            tasks.append(
                PlannedSubtask(
                    subtask_type="freshness_check",
                    description=f"Check document freshness for: {summary}",
                    priority=20,
                    route="freshness",
                )
            )
        if analysis.requires_trusted_web:
            tasks.append(
                PlannedSubtask(
                    subtask_type="trusted_web_search",
                    description=f"Search trusted sources for: {summary}",
                    priority=30,
                    route="trusted_web",
                )
            )
        if analysis.requires_open_web:
            tasks.append(
                PlannedSubtask(
                    subtask_type="open_web_search",
                    description=f"Search open web for unresolved aspects of: {summary}",
                    priority=40,
                    route="open_web",
                )
            )
        if analysis.complexity == QueryComplexity.COMPLEX and len(tasks) > 1:
            for index, task in enumerate(tasks, start=1):
                tasks[index - 1] = PlannedSubtask(
                    subtask_type=task.subtask_type,
                    description=task.description,
                    priority=task.priority,
                    route=task.route,
                    parent_index=None,
                )
        return tasks

    def _normalize_priorities(self, tasks: list[PlannedSubtask]) -> list[PlannedSubtask]:
        """Re-sort tasks into deterministic execution order."""

        return sorted(tasks, key=lambda item: (item.priority, item.route, item.subtask_type))


def _summarize_query(query_text: str) -> str:
    """Build a short human-readable query summary for subtask descriptions."""

    compact = re.sub(r"\s+", " ", query_text).strip()
    return compact[:120]
