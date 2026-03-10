"""Query analysis for Stage 2 orchestration planning."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from qanorm.agents.planner.query_intent import (
    QueryIntent,
    QueryIntentResult,
    RetrievalMode,
    build_clarification_question,
    extract_constraints,
    extract_document_hints,
    extract_engineering_aspects,
    extract_locator_hints,
    extract_subject,
    infer_query_intent,
)
from qanorm.models.qa_state import QueryState
from qanorm.prompts.registry import PromptRegistry, create_prompt_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import ChatMessage, ChatModelProvider, ChatRequest, create_role_bound_providers
from qanorm.settings import RuntimeConfig, get_settings


JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


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
    intent: QueryIntent
    retrieval_mode: RetrievalMode
    clarification_required: bool
    requires_freshness_check: bool
    requires_trusted_web: bool
    requires_open_web: bool
    clarification_question: str | None = None
    document_hints: list[str] = field(default_factory=list)
    locator_hints: list[str] = field(default_factory=list)
    subject: str | None = None
    engineering_aspects: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    used_fallback: bool = False

    @property
    def requires_normative_retrieval(self) -> bool:
        """Return whether the planner should schedule normative retrieval."""

        return self.intent in {QueryIntent.NORMATIVE_RETRIEVAL, QueryIntent.MIXED_RETRIEVAL}

    def to_dict(self) -> dict[str, Any]:
        """Expose a JSON-serializable representation for audit storage."""

        payload = asdict(self)
        payload["complexity"] = self.complexity.value
        payload["intent"] = self.intent.value
        payload["retrieval_mode"] = self.retrieval_mode.value
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
            parsed = self._parse_analysis_response(response.content, query_text=state.query_text)
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
            "intent": "clarify|no_retrieval|normative_retrieval|mixed_retrieval",
            "retrieval_mode": "clarify|none|normative|mixed",
            "clarification_required": False,
            "clarification_question": "string|null",
            "requires_freshness_check": True,
            "requires_trusted_web": False,
            "requires_open_web": False,
            "document_hints": ["СП 63"],
            "locator_hints": ["п. 8.3"],
            "subject": "what the user is asking about",
            "engineering_aspects": ["aspect"],
            "constraints": ["constraint"],
            "assumptions": ["assumption"],
        }
        return (
            "Return only one JSON object using this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Query:\n{query_text}"
        )

    def _parse_analysis_response(self, content: str, *, query_text: str) -> QueryAnalysis | None:
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
            intent_result = self._normalize_intent_result(payload, query_text=query_text)
            return QueryAnalysis(
                query_type=str(payload["query_type"]).strip() or "mixed",
                complexity=complexity,
                intent=intent_result.intent,
                retrieval_mode=intent_result.retrieval_mode,
                clarification_required=intent_result.clarification_required,
                clarification_question=intent_result.clarification_question,
                requires_freshness_check=bool(payload["requires_freshness_check"]) or intent_result.requires_normative_retrieval,
                requires_trusted_web=intent_result.requires_trusted_web,
                requires_open_web=intent_result.requires_open_web,
                document_hints=intent_result.document_hints,
                locator_hints=intent_result.locator_hints,
                subject=intent_result.subject,
                engineering_aspects=intent_result.engineering_aspects,
                constraints=intent_result.constraints,
                assumptions=self._normalize_list(payload.get("assumptions")),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def _fallback_analysis(self, query_text: str) -> QueryAnalysis:
        """Deterministic fallback keeps the orchestrator moving without model output."""

        intent_result = infer_query_intent(query_text)
        query_type = self._infer_query_type(intent_result=intent_result)
        complexity = self._infer_complexity(query_text=query_text, aspect_count=len(intent_result.engineering_aspects))

        return QueryAnalysis(
            query_type=query_type,
            complexity=complexity,
            intent=intent_result.intent,
            retrieval_mode=intent_result.retrieval_mode,
            clarification_required=intent_result.clarification_required,
            clarification_question=intent_result.clarification_question,
            requires_freshness_check=intent_result.requires_normative_retrieval,
            requires_trusted_web=intent_result.requires_trusted_web,
            requires_open_web=intent_result.requires_open_web,
            document_hints=intent_result.document_hints,
            locator_hints=intent_result.locator_hints,
            subject=intent_result.subject,
            engineering_aspects=intent_result.engineering_aspects,
            constraints=intent_result.constraints,
            assumptions=[],
            used_fallback=True,
        )

    def _normalize_intent_result(self, payload: dict[str, Any], *, query_text: str) -> QueryIntentResult:
        """Normalize model output and overlay deterministic hints for stability."""

        intent = QueryIntent(str(payload["intent"]))
        retrieval_mode = RetrievalMode(str(payload["retrieval_mode"]))
        document_hints = self._normalize_list(payload.get("document_hints"))
        locator_hints = self._normalize_list(payload.get("locator_hints"))
        subject = str(payload.get("subject", "")).strip() or None
        engineering_aspects = self._normalize_list(payload.get("engineering_aspects"))
        constraints = self._normalize_list(payload.get("constraints"))
        clarification_required = bool(payload["clarification_required"])
        clarification_question = str(payload.get("clarification_question", "")).strip() or None

        heuristic = infer_query_intent(query_text)
        # Conservative override: ambiguous or non-actionable requests should not slip into retrieval.
        if heuristic.intent in {QueryIntent.CLARIFY, QueryIntent.NO_RETRIEVAL} and intent not in {QueryIntent.CLARIFY, QueryIntent.NO_RETRIEVAL}:
            intent = heuristic.intent
            retrieval_mode = heuristic.retrieval_mode
            clarification_required = heuristic.clarification_required
            clarification_question = heuristic.clarification_question

        extracted_documents = extract_document_hints(query_text)
        extracted_locators = extract_locator_hints(query_text)
        extracted_subject = extract_subject(query_text, document_hints=extracted_documents, locator_hints=extracted_locators)
        extracted_aspects = extract_engineering_aspects(query_text)
        extracted_constraints = extract_constraints(query_text)
        document_hints = _merge_unique(document_hints, extracted_documents)
        locator_hints = _merge_unique(locator_hints, extracted_locators)
        engineering_aspects = _merge_unique(engineering_aspects, extracted_aspects)
        constraints = _merge_unique(constraints, extracted_constraints)
        subject = subject or extracted_subject
        if clarification_required and not clarification_question:
            clarification_question = build_clarification_question(
                query_text=query_text,
                document_hints=document_hints,
                locator_hints=locator_hints,
                subject=subject,
            )

        return QueryIntentResult(
            intent=intent,
            retrieval_mode=retrieval_mode,
            clarification_required=clarification_required,
            clarification_question=clarification_question,
            document_hints=document_hints,
            locator_hints=locator_hints,
            subject=subject,
            engineering_aspects=engineering_aspects,
            constraints=constraints,
            requires_trusted_web=bool(payload["requires_trusted_web"]) or heuristic.requires_trusted_web,
            requires_open_web=bool(payload["requires_open_web"]) or heuristic.requires_open_web,
        )

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

    def _infer_query_type(self, *, intent_result: QueryIntentResult) -> str:
        """Map the new intent gate output back to the coarse query type used elsewhere."""

        if intent_result.intent is QueryIntent.NO_RETRIEVAL and intent_result.requires_open_web:
            return "web_only"
        if intent_result.intent is QueryIntent.MIXED_RETRIEVAL:
            return "mixed"
        if intent_result.intent is QueryIntent.NORMATIVE_RETRIEVAL:
            return "normative"
        return "consultative"


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    """Preserve list order while merging deterministic and model-derived hints."""

    merged = list(left)
    for item in right:
        if item not in merged:
            merged.append(item)
    return merged
