"""Query analysis for Stage 2 orchestration planning."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from qanorm.models.qa_state import QueryState
from qanorm.prompts.registry import PromptRegistry, create_prompt_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import ChatMessage, ChatModelProvider, ChatRequest, create_role_bound_providers
from qanorm.settings import RuntimeConfig, get_settings


JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
NORMATIVE_CODE_RE = re.compile(r"\b(?:ГОСТ|СП|СНиП|РД|СТО|ВСП|ISO|EN)\b", re.IGNORECASE)
NORMATIVE_HINTS = ("требован", "норм", "пункт", "раздел", "стать", "допускается", "обязательно")
TRUSTED_WEB_HINTS = ("пример", "практик", "рекомендац", "обзор", "разъяснен", "комментар")
OPEN_WEB_HINTS = ("в интернете", "поиск", "web", "в сети", "найди")
CONSTRAINT_MARKERS = ("при ", "если ", "для ", "без ", "с учетом ", "в условиях ")


class QueryComplexity(StrEnum):
    """Coarse complexity bands used to route planning effort."""

    SIMPLE = "simple"
    MULTI_ASPECT = "multi_aspect"
    COMPLEX = "complex"


@dataclass(slots=True, frozen=True)
class QueryAnalysis:
    """Normalized analysis output consumed by the task decomposer."""

    query_type: str
    complexity: QueryComplexity
    requires_normative_retrieval: bool
    requires_freshness_check: bool
    requires_trusted_web: bool
    requires_open_web: bool
    engineering_aspects: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    used_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Expose a JSON-serializable representation for audit storage."""

        payload = asdict(self)
        payload["complexity"] = self.complexity.value
        return payload


class QueryAnalyzer:
    """Analyze the incoming query with model assistance and deterministic fallback."""

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

    async def analyze(self, state: QueryState) -> QueryAnalysis:
        """Analyze the query and fall back to heuristics if the model output is unusable."""

        prompt = self.prompt_registry.render("query_analyzer", context=state.build_prompt_context())
        try:
            response = await self.provider.generate(
                ChatRequest(
                    model=self.provider.model,
                    messages=[
                        ChatMessage(role="system", content=prompt.text),
                        ChatMessage(role="user", content=self._analysis_instruction(state.query_text)),
                    ],
                    temperature=0.0,
                    max_tokens=700,
                    metadata={"prompt_metadata": prompt.metadata},
                )
            )
            parsed = self._parse_analysis_response(response.content)
        except Exception:
            parsed = None
        if parsed is None:
            return self._fallback_analysis(state.query_text)
        return parsed

    def _analysis_instruction(self, query_text: str) -> str:
        """Force a machine-readable contract for the planner handoff."""

        schema = {
            "query_type": "normative|consultative|mixed|web_only",
            "complexity": "simple|multi_aspect|complex",
            "requires_normative_retrieval": True,
            "requires_freshness_check": True,
            "requires_trusted_web": False,
            "requires_open_web": False,
            "engineering_aspects": ["aspect"],
            "constraints": ["constraint"],
            "assumptions": ["assumption"],
        }
        return (
            "Return only one JSON object using this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Query:\n{query_text}"
        )

    def _parse_analysis_response(self, content: str) -> QueryAnalysis | None:
        """Parse the first JSON object returned by the model, if any."""

        match = JSON_OBJECT_RE.search(content)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        try:
            complexity = QueryComplexity(str(payload["complexity"]))
            return QueryAnalysis(
                query_type=str(payload["query_type"]).strip() or "mixed",
                complexity=complexity,
                requires_normative_retrieval=bool(payload["requires_normative_retrieval"]),
                requires_freshness_check=bool(payload["requires_freshness_check"]),
                requires_trusted_web=bool(payload["requires_trusted_web"]),
                requires_open_web=bool(payload["requires_open_web"]),
                engineering_aspects=self._normalize_list(payload.get("engineering_aspects")),
                constraints=self._normalize_list(payload.get("constraints")),
                assumptions=self._normalize_list(payload.get("assumptions")),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def _fallback_analysis(self, query_text: str) -> QueryAnalysis:
        """Deterministic fallback keeps the orchestrator moving without model output."""

        lowered = query_text.lower()
        has_normative_signal = bool(NORMATIVE_CODE_RE.search(query_text)) or any(token in lowered for token in NORMATIVE_HINTS)
        has_trusted_signal = any(token in lowered for token in TRUSTED_WEB_HINTS)
        explicit_open_web = any(token in lowered for token in OPEN_WEB_HINTS)
        aspects = self._extract_aspects(query_text)
        constraints = self._extract_constraints(query_text)

        query_type = "normative" if has_normative_signal and not has_trusted_signal and not explicit_open_web else "mixed"
        if not has_normative_signal and explicit_open_web:
            query_type = "web_only"
        complexity = self._infer_complexity(query_text=query_text, aspect_count=len(aspects))
        requires_normative_retrieval = has_normative_signal or query_type != "web_only"
        requires_trusted_web = has_trusted_signal
        requires_open_web = explicit_open_web or (not requires_normative_retrieval and not requires_trusted_web)

        return QueryAnalysis(
            query_type=query_type,
            complexity=complexity,
            requires_normative_retrieval=requires_normative_retrieval,
            requires_freshness_check=requires_normative_retrieval,
            requires_trusted_web=requires_trusted_web,
            requires_open_web=requires_open_web,
            engineering_aspects=aspects,
            constraints=constraints,
            assumptions=[],
            used_fallback=True,
        )

    def _extract_aspects(self, query_text: str) -> list[str]:
        """Split the query into coarse engineering aspects for decomposition."""

        parts = re.split(r"[?;]|(?:,?\s+и\s+)", query_text)
        aspects = [part.strip(" .,\n\t") for part in parts if len(part.strip()) >= 12]
        return aspects[:6] or [query_text.strip()]

    def _extract_constraints(self, query_text: str) -> list[str]:
        """Extract short constraint clauses that affect routing and evidence needs."""

        lowered = query_text.lower()
        constraints = []
        for marker in CONSTRAINT_MARKERS:
            index = lowered.find(marker)
            if index >= 0:
                snippet = query_text[index : index + 120].strip(" .,")
                if snippet:
                    constraints.append(snippet)
        return constraints[:4]

    def _infer_complexity(self, *, query_text: str, aspect_count: int) -> QueryComplexity:
        """Infer the amount of planning needed from surface complexity."""

        if aspect_count >= 3 or len(query_text) > 220:
            return QueryComplexity.COMPLEX
        if aspect_count >= 2 or len(query_text) > 120:
            return QueryComplexity.MULTI_ASPECT
        return QueryComplexity.SIMPLE

    def _normalize_list(self, payload: Any) -> list[str]:
        """Normalize model-provided arrays into short strings."""

        if not isinstance(payload, list):
            return []
        return [str(item).strip() for item in payload if str(item).strip()]
