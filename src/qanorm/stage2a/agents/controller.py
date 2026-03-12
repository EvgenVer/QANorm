"""DSPy-based retrieval controller for the Stage 2A runtime."""

from __future__ import annotations

import re
from typing import Any, Callable, Literal
from uuid import UUID

import dspy
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import DocumentCandidateDTO, EvidenceItemDTO
from qanorm.stage2a.providers import Stage2ADspyModelBundle, build_stage2a_dspy_models
from qanorm.stage2a.retrieval.engine import RetrievalEngine, RetrievalHit
from qanorm.stage2a.retrieval.query_parser import ParsedQuery


class ControllerSignature(dspy.Signature):
    """Use retrieval tools to gather grounded evidence before deciding between direct, partial, clarify, or no_answer."""

    query_text: str = dspy.InputField(desc="Original user question.")
    policy_hint: str = dspy.InputField(desc="Deterministic retrieval policy hint built from the parsed query.")
    retrieval_feedback: str = dspy.InputField(desc="Feedback from the previous failed pass. Empty on the first pass.")
    answer_mode: Literal["direct", "partial", "clarify", "no_answer"] = dspy.OutputField(
        desc="Choose direct only with enough evidence ids. Use partial when evidence exists but is weak."
    )
    reasoning_summary: str = dspy.OutputField(desc="Short summary of the retrieval outcome.")
    selected_evidence_ids: str = dspy.OutputField(
        desc="Comma-separated evidence ids taken exactly from tool observations, for example 'ev-0001, ev-0002'."
    )


class ControllerAgentResult(BaseModel):
    """Normalized controller output consumed by later answer modules."""

    query_text: str = Field(min_length=1)
    answer_mode: Literal["direct", "partial", "clarify", "no_answer"]
    reasoning_summary: str = Field(min_length=1)
    selected_evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItemDTO] = Field(default_factory=list)
    trajectory: dict[str, Any] = Field(default_factory=dict)
    policy_hint: str = Field(min_length=1)
    iterations_used: int = Field(ge=1)


class ControllerAgent:
    """Bounded ReAct-lite controller sitting on top of the custom retrieval engine."""

    def __init__(
        self,
        *,
        session: Session | None = None,
        retrieval_engine: RetrievalEngine | None = None,
        config: Stage2AConfig | None = None,
        model_bundle: Stage2ADspyModelBundle | None = None,
        react_factory: Callable[[list[Callable[..., str]]], Any] | None = None,
    ) -> None:
        self.config = config or get_stage2a_config()
        self.retrieval = retrieval_engine or RetrievalEngine(_require_session(session))
        self.models = model_bundle or build_stage2a_dspy_models(self.config)
        self._react_factory = react_factory or self._build_react_program

    def run(self, query_text: str) -> ControllerAgentResult:
        """Run the bounded DSPy controller loop for one user query."""

        parsed = self.retrieval.parse_query(query_text)
        policy_hint = _build_policy_hint(parsed)
        retrieval_feedback = ""
        result: ControllerAgentResult | None = None

        for iteration_index in range(self.config.runtime.max_corrective_iterations + 1):
            toolbox = _ControllerToolbox(self.retrieval)
            program = self._react_factory(toolbox.build_tools())
            with dspy.context(lm=self.models.controller):
                prediction = program(
                    query_text=query_text,
                    policy_hint=policy_hint,
                    retrieval_feedback=retrieval_feedback,
                )

            result = self._build_result(
                query_text=query_text,
                prediction=prediction,
                toolbox=toolbox,
                policy_hint=policy_hint,
                iterations_used=iteration_index + 1,
            )
            if self._is_terminal(result):
                return result
            retrieval_feedback = self._build_retrieval_feedback(result)

        if result is None:
            raise RuntimeError("Controller agent produced no result")
        return self._finalize_result(result)

    def _build_react_program(self, tools: list[Callable[..., str]]) -> Any:
        """Build one fresh DSPy ReAct program for the current query."""

        return dspy.ReAct(
            ControllerSignature,
            tools=tools,
            max_iters=self.config.runtime.max_tool_steps,
        )

    def _build_result(
        self,
        *,
        query_text: str,
        prediction: Any,
        toolbox: "_ControllerToolbox",
        policy_hint: str,
        iterations_used: int,
    ) -> ControllerAgentResult:
        selected_evidence_ids = _parse_evidence_ids(getattr(prediction, "selected_evidence_ids", ""))
        evidence = toolbox.collect_selected_evidence(selected_evidence_ids)
        if not evidence:
            evidence = toolbox.collect_observed_evidence(limit=self.config.retrieval.evidence_pack_size)
            if evidence and not selected_evidence_ids:
                selected_evidence_ids = [item.evidence_id for item in evidence]
        answer_mode = _normalize_answer_mode(getattr(prediction, "answer_mode", "no_answer"))
        answer_mode = self._apply_grounding_policy(answer_mode, evidence)
        reasoning_summary = (getattr(prediction, "reasoning_summary", "") or "").strip() or "Контроллер не вернул краткое резюме."
        trajectory = getattr(prediction, "trajectory", {}) or {}
        return ControllerAgentResult(
            query_text=query_text,
            answer_mode=answer_mode,
            reasoning_summary=reasoning_summary,
            selected_evidence_ids=selected_evidence_ids,
            evidence=evidence,
            trajectory=trajectory,
            policy_hint=policy_hint,
            iterations_used=iterations_used,
        )

    def _apply_grounding_policy(
        self,
        answer_mode: Literal["direct", "partial", "clarify", "no_answer"],
        evidence: list[EvidenceItemDTO],
    ) -> Literal["direct", "partial", "clarify", "no_answer"]:
        """Downgrade the mode when the selected evidence is too weak."""

        if not evidence:
            if answer_mode in {"clarify", "no_answer"}:
                return answer_mode
            return answer_mode
        if answer_mode == "direct" and len(evidence) < self.config.retrieval.min_direct_answer_evidence:
            if self.config.retrieval.enable_partial_answer_on_low_confidence:
                return "partial"
            return "clarify"
        return answer_mode

    def _is_terminal(self, result: ControllerAgentResult) -> bool:
        """Check whether another corrective pass would still add value."""

        if result.answer_mode in {"clarify", "no_answer"}:
            return True
        if result.answer_mode == "partial":
            return bool(result.evidence)
        if result.answer_mode == "direct":
            return len(result.evidence) >= self.config.retrieval.min_direct_answer_evidence
        return False

    def _build_retrieval_feedback(self, result: ControllerAgentResult) -> str:
        """Explain to the next pass why the previous result was insufficient."""

        if not result.evidence:
            return (
                "No valid evidence ids were selected. Call retrieval tools again and finish only after citing observed "
                "evidence ids such as ev-0001."
            )
        return (
            f"A direct answer requires at least {self.config.retrieval.min_direct_answer_evidence} evidence ids. "
            "Either gather more evidence or downgrade the answer mode to partial."
        )

    def _finalize_result(self, result: ControllerAgentResult) -> ControllerAgentResult:
        """Produce a stable terminal result after corrective retries are exhausted."""

        if result.evidence and result.answer_mode == "direct":
            return result.model_copy(update={"answer_mode": "partial"})
        if result.evidence:
            return result.model_copy(update={"answer_mode": "partial"})
        return result.model_copy(update={"answer_mode": "no_answer"})


class _ControllerToolbox:
    """Stateful tool wrappers that expose the custom retrieval engine to DSPy ReAct."""

    def __init__(self, retrieval: RetrievalEngine) -> None:
        self.retrieval = retrieval
        self._evidence_by_key: dict[tuple[str, str], EvidenceItemDTO] = {}
        self._evidence_order: list[str] = []
        self._next_evidence_index = 1

    def build_tools(self) -> list[Callable[..., str]]:
        """Return the bounded set of retrieval tools exposed to the controller."""

        return [
            self.resolve_document,
            self.discover_documents,
            self.lookup_locator,
            self.search_lexical,
            self.read_node,
            self.expand_neighbors,
        ]

    def collect_selected_evidence(self, evidence_ids: list[str]) -> list[EvidenceItemDTO]:
        """Resolve evidence ids chosen by the controller back to DTOs."""

        selected: list[EvidenceItemDTO] = []
        by_id = {item.evidence_id: item for item in self._evidence_by_key.values()}
        for evidence_id in evidence_ids:
            evidence = by_id.get(evidence_id)
            if evidence is None:
                continue
            selected.append(evidence)
        return selected

    def collect_observed_evidence(self, *, limit: int) -> list[EvidenceItemDTO]:
        """Return the strongest observed evidence when the model fails to name evidence ids explicitly."""

        by_id = {item.evidence_id: item for item in self._evidence_by_key.values()}
        ordered: list[EvidenceItemDTO] = []
        for evidence_id in self._evidence_order:
            evidence = by_id.get(evidence_id)
            if evidence is None:
                continue
            ordered.append(evidence)
            if len(ordered) >= limit:
                break
        return ordered

    def resolve_document(self, query_text: str) -> str:
        """Resolve explicit document references from the query."""

        parsed = self.retrieval.parse_query(query_text)
        candidates = [DocumentCandidateDTO.from_candidate(item) for item in self.retrieval.resolve_document(parsed)]
        return _format_document_candidates("resolve_document", candidates)

    def discover_documents(self, query_text: str) -> str:
        """Discover candidate documents when the question has no explicit code."""

        parsed = self.retrieval.parse_query(query_text)
        candidates = [DocumentCandidateDTO.from_candidate(item) for item in self.retrieval.discover_documents(parsed)]
        return _format_document_candidates("discover_documents", candidates)

    def lookup_locator(self, document_version_id: str, locator: str) -> str:
        """Look up an explicit locator inside one resolved document version."""

        version_id = UUID(document_version_id)
        hits = self.retrieval.lookup_locator(document_version_id=version_id, locator=locator)
        evidence = self._register_hits(hits)
        return _format_evidence_observation("lookup_locator", evidence, f"Locator {locator} inside {document_version_id}")

    def search_lexical(self, query_text: str, document_version_ids: list[str]) -> str:
        """Run scoped lexical retrieval for one or more document versions."""

        version_ids = [UUID(value) for value in document_version_ids]
        hits = self.retrieval.search_lexical(query_text, document_version_ids=version_ids)
        evidence = self._register_hits(hits)
        return _format_evidence_observation("search_lexical", evidence, f"Scoped lexical search over {len(version_ids)} document versions")

    def read_node(self, node_id: str) -> str:
        """Read one node when the controller wants a precise citation anchor."""

        hit = self.retrieval.read_node(UUID(node_id))
        evidence = self._register_hits([hit] if hit is not None else [])
        return _format_evidence_observation("read_node", evidence, f"Read node {node_id}")

    def expand_neighbors(self, document_version_id: str, node_id: str) -> str:
        """Expand local context around one anchor node."""

        hits = self.retrieval.expand_neighbors(
            document_version_id=UUID(document_version_id),
            node_id=UUID(node_id),
        )
        evidence = self._register_hits(hits)
        return _format_evidence_observation("expand_neighbors", evidence, f"Expanded local context around node {node_id}")

    def _register_hits(self, hits: list[RetrievalHit]) -> list[EvidenceItemDTO]:
        evidence: list[EvidenceItemDTO] = []
        for hit in hits:
            key = _hit_identity(hit)
            existing = self._evidence_by_key.get(key)
            if existing is not None:
                evidence.append(existing)
                continue
            evidence_id = f"ev-{self._next_evidence_index:04d}"
            self._next_evidence_index += 1
            item = EvidenceItemDTO.from_hit(hit, evidence_id=evidence_id)
            self._evidence_by_key[key] = item
            self._evidence_order.append(item.evidence_id)
            evidence.append(item)
        return evidence


def _require_session(session: Session | None) -> Session:
    if session is None:
        raise ValueError("session is required when retrieval_engine is not provided")
    return session


def _build_policy_hint(parsed: ParsedQuery) -> str:
    if parsed.explicit_document_codes and parsed.explicit_locator_values:
        return (
            "The query contains an explicit document code and locator. Resolve the document first, then use "
            "lookup_locator, and only then use search_lexical if more evidence is needed."
        )
    if parsed.explicit_document_codes:
        return (
            "The query contains an explicit document code. Resolve the document first and keep lexical retrieval "
            "scoped to the returned document_version_id values."
        )
    if parsed.explicit_locator_values:
        return (
            "The query contains an explicit locator but no trusted document code. Discover likely documents, then use "
            "lookup_locator for each shortlisted document before broader lexical search."
        )
    return (
        "The query has no explicit norm. Discover likely documents first, then run scoped lexical retrieval and read "
        "or expand neighbors only for the most relevant hits."
    )


def _parse_evidence_ids(raw_value: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for evidence_id in re.findall(r"ev-\d{4}", raw_value or ""):
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        ordered.append(evidence_id)
    return ordered


def _normalize_answer_mode(raw_value: str) -> Literal["direct", "partial", "clarify", "no_answer"]:
    value = (raw_value or "").strip().casefold()
    if value in {"direct", "partial", "clarify", "no_answer"}:
        return value  # type: ignore[return-value]
    return "no_answer"


def _format_document_candidates(tool_name: str, candidates: list[DocumentCandidateDTO]) -> str:
    if not candidates:
        return f"{tool_name}: no document candidates found."
    lines = [f"{tool_name}: found {len(candidates)} candidate documents."]
    for candidate in candidates:
        lines.append(
            " - "
            f"document_id={candidate.document_id} | "
            f"document_version_id={candidate.document_version_id} | "
            f"code={candidate.display_code} | "
            f"score={candidate.score:.2f} | "
            f"reason={candidate.reason} | "
            f"title={candidate.title or '-'}"
        )
    return "\n".join(lines)


def _format_evidence_observation(tool_name: str, evidence: list[EvidenceItemDTO], message: str) -> str:
    if not evidence:
        return f"{tool_name}: {message}. No evidence hits found."
    lines = [f"{tool_name}: {message}. Evidence hits: {len(evidence)}."]
    for item in evidence:
        lines.append(
            " - "
            f"{item.evidence_id} | "
            f"citation={_format_citation(item)} | "
            f"document_version_id={item.document_version_id} | "
            f"node_id={item.node_id} | "
            f"score={item.score:.2f} | "
            f"text={_truncate_text(item.text)}"
        )
    return "\n".join(lines)


def _hit_identity(hit: RetrievalHit) -> tuple[str, str]:
    primary = str(hit.node_id or hit.retrieval_unit_id or hit.document_version_id)
    secondary = hit.source_kind
    return secondary, primary


def _truncate_text(text: str, *, limit: int = 700) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."


def _format_citation(item: EvidenceItemDTO) -> str:
    parts: list[str] = []
    if item.document_display_code:
        parts.append(item.document_display_code)
    if item.locator:
        parts.append(f"п. {item.locator}")
    if item.heading_path:
        parts.append(item.heading_path)
    return " | ".join(parts) if parts else "-"
