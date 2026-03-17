"""High-level orchestration for one Stage 2A / Stage 2B query."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qanorm.db.session import create_session_factory
from qanorm.normalizers.codes import normalize_document_code
from qanorm.stage2a.agents import Composer, ControllerAgent, ControllerAgentResult, GroundingVerifier
from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import (
    AnswerClaimDTO,
    EvidenceItemDTO,
    RuntimeEventDTO,
    Stage2AAnswerDTO,
    Stage2AChatSessionDTO,
    Stage2AConversationalQueryRequest,
)
from qanorm.stage2a.providers import Stage2ADspyModelBundle, build_stage2a_dspy_models
from qanorm.stage2a.retrieval.engine import RetrievalEngine, RetrievalHit
from qanorm.stage2a.retrieval.query_parser import ParsedQuery
from qanorm.stage2a.session_memory import append_message, update_memory_after_answer


_AMBIGUOUS_QUERY_RE = re.compile(
    r"(С‡С‚Рѕ\s+С‚СЂРµР±СѓРµС‚СЃСЏ\s+РїРѕ|РєР°РєРёРµ\s+РЅРѕСЂРјС‹|С‡С‚Рѕ\s+РЅСѓР¶РЅРѕ\s+СѓС‡РёС‚С‹РІР°С‚СЊ|РєР°РєРёРµ\s+С‚СЂРµР±РѕРІР°РЅРёСЏ\s+Рє)",
    re.IGNORECASE,
)
_EXPAND_REQUEST_RE = re.compile(
    r"\b(РґРѕРїРѕР»РЅРё|РїСЂРѕРґРѕР»Р¶Рё|С‡С‚Рѕ\s+РµС‰Рµ|РµС‰Рµ|РїРѕРґСЂРѕР±РЅРµРµ|СЂР°СЃРєСЂРѕР№)\b",
    re.IGNORECASE,
)
_CLARIFY_REQUEST_RE = re.compile(
    r"\b(СѓС‚РѕС‡РЅРё|РєР°РєРѕР№\s+РёРјРµРЅРЅРѕ|РєР°РєРѕР№\s+РїСѓРЅРєС‚|РіРґРµ\s+СЌС‚Рѕ\s+РЅР°РїРёСЃР°РЅРѕ|РїСЂРёРІРµРґРё\s+СЃСЃС‹Р»РєСѓ|РєР°РєР°СЏ\s+СЃСЃС‹Р»РєР°)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_RE = re.compile(
    r"\b(Р°\s+С‡С‚Рѕ|Р°\s+РєР°Рє|Р°\s+РґР»СЏ|РїРѕ\s+РЅРµРјСѓ|РїРѕ\s+РЅРµР№|РїРѕ\s+РЅРёРј|РїРѕ\s+СЌС‚РѕРјСѓ|РїРѕ\s+СЌС‚РѕР№|РїРѕ\s+СЌС‚РёРј|РґР»СЏ\s+РЅРёС…|РґР»СЏ\s+РЅРµРіРѕ|РґР»СЏ\s+РЅРµРµ)\b",
    re.IGNORECASE,
)


_DOCUMENT_CODE_HINT_RE = re.compile(
    r"\b(?:СП|СНиП|СНИП|ГОСТ|SP|SNIP|GOST)\s*[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9.\-/]*",
    re.IGNORECASE,
)


class Stage2AQueryResult(BaseModel):
    """Full answer payload returned by the Stage 2A runtime."""

    controller: ControllerAgentResult
    answer: Stage2AAnswerDTO


class Stage2AConversationalQueryResult(BaseModel):
    """One Stage 2B conversational answer paired with the updated local session."""

    query_kind: Literal["new_question", "follow_up", "clarify", "expand_answer"]
    effective_query: str = Field(min_length=1)
    result: Stage2AQueryResult
    chat_session: Stage2AChatSessionDTO


class Stage2ARuntime:
    """Compose controller, composer, and verifier into one query workflow."""

    def __init__(
        self,
        *,
        config: Stage2AConfig | None = None,
        session_factory: sessionmaker[Session] | None = None,
        model_bundle: Stage2ADspyModelBundle | None = None,
        controller_factory=None,
        composer_factory=None,
        verifier_factory=None,
    ) -> None:
        self.config = config or get_stage2a_config()
        self.session_factory = session_factory or create_session_factory()
        self.model_bundle = model_bundle or build_stage2a_dspy_models(self.config)
        self._controller_factory = controller_factory or ControllerAgent
        self._composer_factory = composer_factory or Composer
        self._verifier_factory = verifier_factory or GroundingVerifier

    def answer_query(self, query_text: str) -> Stage2AQueryResult:
        """Run the full Stage 2A answer flow for one independent question."""

        final_result: Stage2AQueryResult | None = None
        for event in self.stream_answer_query(query_text):
            if event.event_type == "answer_ready":
                final_result = Stage2AQueryResult.model_validate(event.payload["result"])
        if final_result is None:
            raise RuntimeError("Runtime finished without answer_ready event")
        return final_result

    def stream_answer_query(self, query_text: str) -> Iterator[RuntimeEventDTO]:
        """Stream high-level runtime events for one independent Stage 2A query."""

        yield from self._execute_query_flow(
            retrieval_query_text=query_text,
            answer_query_text=query_text,
            query_kind="new_question",
        )

    def answer_conversation_turn(
        self,
        request: Stage2AConversationalQueryRequest,
    ) -> Stage2AConversationalQueryResult:
        """Run one conversational Stage 2B turn and update the in-memory chat session."""

        final_result: Stage2AConversationalQueryResult | None = None
        for event in self.stream_conversation_turn(request):
            if event.event_type == "answer_ready":
                final_result = Stage2AConversationalQueryResult.model_validate(event.payload["conversation_result"])
        if final_result is None:
            raise RuntimeError("Conversational runtime finished without answer_ready event")
        return final_result

    def stream_conversation_turn(
        self,
        request: Stage2AConversationalQueryRequest,
    ) -> Iterator[RuntimeEventDTO]:
        """Stream high-level runtime events for one Stage 2B conversational turn."""

        query_kind = _classify_conversation_turn(query_text=request.query_text, chat_session=request.chat_session)
        effective_query = _build_effective_query(
            query_text=request.query_text,
            query_kind=query_kind,
            chat_session=request.chat_session,
            config=self.config,
        )

        for event in self._execute_query_flow(
            retrieval_query_text=effective_query,
            answer_query_text=request.query_text,
            query_kind=query_kind,
        ):
            if event.event_type != "answer_ready":
                yield event
                continue

            result = Stage2AQueryResult.model_validate(event.payload["result"])
            updated_session = append_message(
                request.chat_session,
                role="user",
                content=request.query_text,
                config=self.config,
            )
            updated_session = append_message(
                updated_session,
                role="assistant",
                content=result.answer.answer_text,
                answer_mode=result.answer.mode,
                result_payload=result.model_dump(mode="json"),
                config=self.config,
            )
            updated_session = update_memory_after_answer(
                updated_session,
                query_text=request.query_text,
                answer=result.answer,
                config=self.config,
            )
            conversation_result = Stage2AConversationalQueryResult(
                query_kind=query_kind,
                effective_query=effective_query,
                result=result,
                chat_session=updated_session,
            )
            yield event.model_copy(
                update={
                    "payload": {
                        **event.payload,
                        "conversation_result": conversation_result.model_dump(mode="json"),
                    }
                }
            )

    def _execute_query_flow(
        self,
        *,
        retrieval_query_text: str,
        answer_query_text: str,
        query_kind: Literal["new_question", "follow_up", "clarify", "expand_answer"],
    ) -> Iterator[RuntimeEventDTO]:
        """Execute the Stage 2A pipeline and emit stage/runtime events."""

        yield _build_event(
            "query_received",
            f"Получен запрос ({query_kind}).",
            payload={"query_text": answer_query_text},
        )
        if retrieval_query_text != answer_query_text:
            yield _build_event(
                "query_rewritten",
                "Построен effective query с учетом session memory.",
                payload={"effective_query": retrieval_query_text},
            )

        session = self.session_factory()
        try:
            retrieval = RetrievalEngine(session)
            parsed = retrieval.parse_query(retrieval_query_text)

            yield _build_event(
                "controller_started",
                "Запущен controller runtime.",
                payload={"policy_hint": _build_policy_hint(parsed)},
            )

            controller = self._controller_factory(
                retrieval_engine=retrieval,
                config=self.config,
                model_bundle=self.model_bundle,
            )
            controller_result = _coerce_controller_result(controller.run(retrieval_query_text))
            for event in _build_controller_trajectory_events(controller_result):
                yield event

            runtime_evidence = _load_runtime_evidence_pack(retrieval, retrieval_query_text)
            controller_result = _enrich_controller_result(
                controller_result=controller_result,
                runtime_evidence=runtime_evidence,
                parsed_query=parsed,
                config=self.config,
            )
            controller_result = _apply_runtime_answer_policy(
                controller_result=controller_result,
                parsed_query=parsed,
                config=self.config,
            )

            yield _build_event(
                "evidence_updated",
                "Обновлен итоговый evidence pack.",
                payload={
                    "answer_mode": controller_result.answer_mode,
                    "evidence_count": len(controller_result.evidence),
                    "selected_evidence_ids": controller_result.selected_evidence_ids,
                },
            )

            if not controller_result.evidence:
                answer = Stage2AAnswerDTO(
                    mode=controller_result.answer_mode,
                    answer_text=controller_result.reasoning_summary,
                    claims=[],
                    evidence=[],
                    limitations=["РљРѕРЅС‚СЂРѕР»Р»РµСЂ РЅРµ СЃРѕР±СЂР°Р» РїРѕРґС‚РІРµСЂР¶РґРµРЅРЅС‹Рµ evidence."],
                    debug_trace=_build_debug_trace(controller_result, enabled=self.config.runtime.enable_debug_trace),
                )
                result = Stage2AQueryResult(controller=controller_result, answer=answer)
                yield _build_event(
                    "warning",
                    "Ответ ограничен: подтвержденные evidence не собраны.",
                    level="warning",
                    payload={"answer_mode": answer.mode, "limitations": answer.limitations},
                )
                yield _build_event(
                    "answer_ready",
                    "Финальный ответ готов.",
                    is_terminal=True,
                    payload={"result": result.model_dump(mode="json")},
                )
                return

            yield _build_event(
                "composer_started",
                "Запущен composer по собранному evidence pack.",
                payload={"answer_mode": controller_result.answer_mode},
            )
            composer = self._composer_factory(
                config=self.config,
                model_bundle=self.model_bundle,
            )
            draft = composer.compose(
                query_text=answer_query_text,
                answer_mode=controller_result.answer_mode,
                evidence=controller_result.evidence,
            )

            if controller_result.answer_mode == "direct":
                yield _build_event(
                    "verifier_started",
                    "Запущен grounding verifier.",
                    payload={"draft_mode": draft.answer_mode},
                )
                verifier = self._verifier_factory(
                    config=self.config,
                    model_bundle=self.model_bundle,
                )
                answer = verifier.verify(query_text=answer_query_text, draft=draft)
            else:
                yield _build_event(
                    "warning",
                    "Verifier пропущен, потому что interactive answer mode не direct.",
                    level="warning",
                    payload={"draft_mode": draft.answer_mode},
                )
                answer = _build_interactive_answer_from_draft(
                    draft,
                    parsed_query=parsed,
                )

            answer = answer.model_copy(
                update={"debug_trace": _build_debug_trace(controller_result, enabled=self.config.runtime.enable_debug_trace)}
            )
            if answer.limitations:
                yield _build_event(
                    "warning",
                    "Зафиксированы ограничения ответа.",
                    level="warning",
                    payload={"limitations": answer.limitations},
                )

            result = Stage2AQueryResult(controller=controller_result, answer=answer)
            yield _build_event(
                "answer_ready",
                "Финальный ответ готов.",
                is_terminal=True,
                payload={"result": result.model_dump(mode="json")},
            )
        finally:
            session.close()


def _classify_conversation_turn(
    *,
    query_text: str,
    chat_session: Stage2AChatSessionDTO,
) -> Literal["new_question", "follow_up", "clarify", "expand_answer"]:
    normalized = query_text.strip()
    if not normalized:
        return "new_question"

    has_context = bool(chat_session.messages or chat_session.memory.conversation_summary or chat_session.memory.active_document_hints)
    if not has_context:
        return "new_question"
    if _is_context_shift(query_text=query_text, chat_session=chat_session):
        return "new_question"

    lowered = normalized.casefold()

    if _EXPAND_REQUEST_RE.search(normalized) or _contains_any(lowered, ("дополни", "продолжи", "что еще", "ещё", "еще", "подробнее", "раскрой")):
        return "expand_answer"
    if _CLARIFY_REQUEST_RE.search(normalized) or _contains_any(
        lowered,
        ("уточни", "какой именно", "какой пункт", "где это написано", "приведи ссылку", "какая ссылка"),
    ):
        return "clarify"
    if _FOLLOW_UP_RE.search(normalized) or _contains_any(
        lowered,
        ("а что", "а как", "а для", "по нему", "по ней", "по ним", "по этому", "по этой", "по этим"),
    ):
        return "follow_up"

    if len(normalized.split()) <= 5:
        return "follow_up"
    return "new_question"


def _build_effective_query(
    *,
    query_text: str,
    query_kind: Literal["new_question", "follow_up", "clarify", "expand_answer"],
    chat_session: Stage2AChatSessionDTO,
    config: Stage2AConfig,
) -> str:
    if query_kind == "new_question":
        return query_text

    parts: list[str] = []
    memory = chat_session.memory
    if memory.conversation_summary:
        parts.append(f"Контекст беседы: {memory.conversation_summary}")
    if memory.active_document_hints:
        parts.append(f"Документы в фокусе: {', '.join(memory.active_document_hints)}.")
    if memory.active_locator_hints and query_kind in {"clarify", "expand_answer", "follow_up"}:
        parts.append(f"Локаторы в фокусе: {', '.join(memory.active_locator_hints)}.")
    if memory.open_threads:
        parts.append(f"Незакрытые темы: {'; '.join(memory.open_threads)}.")

    recent_messages = chat_session.messages[-2:]
    if recent_messages:
        parts.append(
            "Последние сообщения: "
            + " | ".join(f"{item.role}: {_normalize_inline_text(item.content)}" for item in recent_messages)
        )

    last_assistant_message = next((item for item in reversed(chat_session.messages) if item.role == "assistant"), None)
    if last_assistant_message is not None and query_kind in {"clarify", "expand_answer", "follow_up"}:
        parts.append(f"Предыдущий ответ: {_normalize_inline_text(last_assistant_message.content, limit=240)}")

    guidance = {
        "follow_up": (
            "Инструкция: продолжай текущую тему. Сначала используй активные документы и локаторы, "
            "но при необходимости расширяй evidence pack дополнительными retrieval units и соседним контекстом."
        ),
        "clarify": (
            "Инструкция: пользователь просит уточнить ответ или показать ссылку. Приоритетно проверяй активные "
            "document/locator hints, ищи точный пункт, citation и локальный контекст."
        ),
        "expand_answer": (
            "Инструкция: пользователь просит дополнить предыдущий ответ. Расширяй evidence pack, поднимай дополнительные "
            "locator hits и retrieval units в том же документе, чтобы partial мог стать direct."
        ),
    }
    if query_kind in guidance:
        parts.append(guidance[query_kind])

    prefix = {
        "new_question": "Вопрос пользователя",
        "follow_up": "Follow-up вопрос пользователя",
        "clarify": "Уточняющий вопрос пользователя",
        "expand_answer": "Пользователь просит дополнить предыдущий ответ",
    }[query_kind]
    parts.append(f"{prefix}: {query_text}")
    effective_query = "\n".join(part for part in parts if part).strip()
    return effective_query[: config.conversation.max_summary_chars * 2]


def _is_context_shift(*, query_text: str, chat_session: Stage2AChatSessionDTO) -> bool:
    active_documents = {_normalize_document_hint(value) for value in chat_session.memory.active_document_hints if value}
    active_documents.discard(None)
    if not active_documents:
        return False

    query_documents = {
        normalized
        for normalized in (
            _normalize_document_hint(match.group(0))
            for match in _DOCUMENT_CODE_HINT_RE.finditer(query_text)
        )
        if normalized
    }
    if not query_documents:
        return False
    return query_documents.isdisjoint(active_documents)


def _normalize_document_hint(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_document_code(value)
    if not normalized:
        return None
    prefix, _, rest = normalized.partition(" ")
    if not rest:
        return normalized.casefold()
    family = rest.split(".", 1)[0]
    if family:
        return f"{prefix} {family}".casefold()
    return normalized.casefold()


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


def _build_debug_trace(result: ControllerAgentResult, *, enabled: bool) -> list[str]:
    if not enabled:
        return []
    ordered_keys = sorted(result.trajectory.keys())
    return [f"{key}: {result.trajectory[key]}" for key in ordered_keys]


def _coerce_controller_result(value: ControllerAgentResult | object) -> ControllerAgentResult:
    if isinstance(value, ControllerAgentResult):
        return value
    if hasattr(value, "__dict__"):
        return ControllerAgentResult.model_validate(value.__dict__)
    return ControllerAgentResult.model_validate(value)


def retrieval_hit_to_evidence(hit: RetrievalHit, index: int) -> EvidenceItemDTO:
    return EvidenceItemDTO.from_hit(hit, evidence_id=f"ev-fallback-{index:02d}")


def _load_runtime_evidence_pack(retrieval: RetrievalEngine, query_text: str) -> list[EvidenceItemDTO]:
    if not hasattr(retrieval, "build_evidence_pack"):
        return []
    return [
        retrieval_hit_to_evidence(hit, index)
        for index, hit in enumerate(retrieval.build_evidence_pack(query_text), start=1)
    ]


def _enrich_controller_result(
    *,
    controller_result: ControllerAgentResult,
    runtime_evidence: list[EvidenceItemDTO],
    parsed_query: ParsedQuery,
    config: Stage2AConfig,
) -> ControllerAgentResult:
    if not runtime_evidence:
        return controller_result

    controller_quality = _score_evidence_pack(controller_result.evidence)
    runtime_quality = _score_evidence_pack(runtime_evidence)
    should_replace = False
    reason = ""

    if not controller_result.evidence:
        should_replace = True
        reason = "Runtime fallback used the deterministic evidence pack."
    elif _needs_context_enrichment(controller_result.evidence, config=config) and runtime_quality > controller_quality:
        should_replace = True
        reason = "Runtime replaced node-heavy evidence with a more contextual deterministic evidence pack."

    if not should_replace:
        return controller_result

    replacement_mode = _suggest_answer_mode_from_evidence(
        parsed_query=parsed_query,
        evidence=runtime_evidence,
        current_mode=controller_result.answer_mode,
        config=config,
    )

    return controller_result.model_copy(
        update={
            "answer_mode": replacement_mode,
            "selected_evidence_ids": [item.evidence_id for item in runtime_evidence],
            "evidence": runtime_evidence,
            "reasoning_summary": f"{controller_result.reasoning_summary} {reason}".strip(),
        }
    )


def _build_interactive_answer_from_draft(
    draft,
    *,
    parsed_query: ParsedQuery,
) -> Stage2AAnswerDTO:
    limitations = _dedupe_preserve_order(
        list(draft.limitations)
        + _derive_interactive_limitations(
            answer_mode=draft.answer_mode,
            evidence=draft.evidence,
            parsed_query=parsed_query,
        )
    )
    return Stage2AAnswerDTO(
        mode=draft.answer_mode,
        answer_text=draft.answer_text,
        claims=_normalize_claims_for_interactive(draft.claims, draft.evidence),
        evidence=draft.evidence,
        limitations=_dedupe_preserve_order(limitations),
    )


def _normalize_claims_for_interactive(
    claims: list[AnswerClaimDTO],
    evidence: list[EvidenceItemDTO],
) -> list[AnswerClaimDTO]:
    allowed_ids = {item.evidence_id for item in evidence}
    normalized: list[AnswerClaimDTO] = []
    for claim in claims:
        evidence_ids = [value for value in claim.evidence_ids if value in allowed_ids]
        if not evidence_ids:
            continue
        normalized.append(claim.model_copy(update={"evidence_ids": evidence_ids, "supported": True}))
    return normalized


def _score_evidence_pack(evidence: list[EvidenceItemDTO]) -> int:
    score = 0
    for item in evidence:
        if item.retrieval_unit_id is not None:
            score += 4
        elif item.node_id is not None:
            score += 1
        if item.locator:
            score += 1
        if item.heading_path:
            score += 1
    return score


def _needs_context_enrichment(evidence: list[EvidenceItemDTO], *, config: Stage2AConfig) -> bool:
    if not evidence:
        return True
    retrieval_unit_count = sum(1 for item in evidence if item.retrieval_unit_id is not None)
    node_count = sum(1 for item in evidence if item.node_id is not None and item.retrieval_unit_id is None)
    if retrieval_unit_count == 0 and node_count > 0:
        return True
    if len(evidence) < config.retrieval.min_direct_answer_evidence:
        return True
    return False


def _apply_runtime_answer_policy(
    *,
    controller_result: ControllerAgentResult,
    parsed_query: ParsedQuery,
    config: Stage2AConfig,
) -> ControllerAgentResult:
    suggested_mode = _suggest_answer_mode_from_evidence(
        parsed_query=parsed_query,
        evidence=controller_result.evidence,
        current_mode=controller_result.answer_mode,
        config=config,
    )
    if suggested_mode == controller_result.answer_mode:
        return controller_result

    reason = _runtime_policy_reason(parsed_query=parsed_query, evidence=controller_result.evidence, target_mode=suggested_mode)
    return controller_result.model_copy(
        update={
            "answer_mode": suggested_mode,
            "reasoning_summary": f"{controller_result.reasoning_summary} {reason}".strip(),
        }
    )


def _suggest_answer_mode_from_evidence(
    *,
    parsed_query: ParsedQuery,
    evidence: list[EvidenceItemDTO],
    current_mode: str,
    config: Stage2AConfig,
) -> str:
    if not evidence:
        return current_mode

    retrieval_unit_count = sum(1 for item in evidence if item.retrieval_unit_id is not None)
    document_counts = _document_counts(evidence)
    unique_documents = set(document_counts)
    dominant_document_hits = max(document_counts.values()) if document_counts else 0
    has_locator = any(bool(item.locator) for item in evidence)
    explicit_document = bool(parsed_query.explicit_document_codes)
    explicit_locator = bool(parsed_query.explicit_locator_values)
    strong_single_document = len(unique_documents) == 1 and (
        retrieval_unit_count >= config.retrieval.min_direct_answer_evidence
        or (retrieval_unit_count >= 1 and (has_locator or dominant_document_hits >= 3 or len(evidence) >= 4))
    )
    dominant_context_direct = (
        len(unique_documents) <= 2
        and retrieval_unit_count >= config.retrieval.min_direct_answer_evidence
        and dominant_document_hits >= 3
        and (has_locator or explicit_document or len(evidence) >= 4)
    )

    if _should_clarify(parsed_query=parsed_query, evidence=evidence):
        return "clarify"

    if current_mode in {"no_answer", "clarify", "partial"}:
        if strong_single_document or dominant_context_direct:
            if explicit_document or explicit_locator or has_locator or current_mode != "partial":
                return "direct"
        if evidence and current_mode == "no_answer":
            return "partial"
    return current_mode


def _should_clarify(*, parsed_query: ParsedQuery, evidence: list[EvidenceItemDTO]) -> bool:
    if parsed_query.explicit_document_codes or parsed_query.explicit_locator_values:
        return False
    document_counts = _document_counts(evidence)
    unique_document_count = len(document_counts)
    dominant_document_hits = max(document_counts.values()) if document_counts else 0
    has_locator = any(bool(item.locator) for item in evidence)
    retrieval_unit_count = sum(1 for item in evidence if item.retrieval_unit_id is not None)
    if _AMBIGUOUS_QUERY_RE.search(parsed_query.raw_text):
        if unique_document_count <= 1 and (has_locator or retrieval_unit_count >= 1 or dominant_document_hits >= 2):
            return False
        if unique_document_count == 2 and retrieval_unit_count >= 2 and dominant_document_hits >= 3:
            return False
        return True
    if len(parsed_query.lexical_tokens) > 4:
        return False

    if unique_document_count >= 2 and dominant_document_hits < 2 and not has_locator:
        return True
    return False


def _runtime_policy_reason(*, parsed_query: ParsedQuery, evidence: list[EvidenceItemDTO], target_mode: str) -> str:
    if target_mode == "clarify":
        return "Runtime switched the answer to clarify because the question is broad and the evidence spans multiple documents without a stable dominant context."
    if target_mode == "direct":
        return "Runtime promoted the answer to direct because one document produced enough retrieval-unit evidence for a grounded response."
    if target_mode == "partial":
        return "Runtime downgraded the answer to partial because evidence exists but remains incomplete."
    return ""


def _document_counts(evidence: list[EvidenceItemDTO]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        if not item.document_display_code:
            continue
        counts[item.document_display_code] = counts.get(item.document_display_code, 0) + 1
    return counts


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _derive_interactive_limitations(
    *,
    answer_mode: str,
    evidence: list[EvidenceItemDTO],
    parsed_query: ParsedQuery,
) -> list[str]:
    limitations: list[str] = []
    retrieval_unit_count = sum(1 for item in evidence if item.retrieval_unit_id is not None)
    node_only_count = sum(1 for item in evidence if item.node_id is not None and item.retrieval_unit_id is None)
    unique_documents = {item.document_display_code for item in evidence if item.document_display_code}
    has_locator = any(bool(item.locator) for item in evidence)

    if answer_mode == "clarify":
        limitations.append(
            "РћС‚РІРµС‚ РѕРіСЂР°РЅРёС‡РµРЅ: РІРѕРїСЂРѕСЃ СЃР»РёС€РєРѕРј С€РёСЂРѕРєРёР№ РґР»СЏ СѓРІРµСЂРµРЅРЅРѕРіРѕ РЅРѕСЂРјР°С‚РёРІРЅРѕРіРѕ РІС‹РІРѕРґР° Р±РµР· СѓС‚РѕС‡РЅРµРЅРёСЏ РѕР±СЉРµРєС‚Р°, С‚РёРїР° РєРѕРЅСЃС‚СЂСѓРєС†РёРё РёР»Рё РЅСѓР¶РЅРѕРіРѕ РґРѕРєСѓРјРµРЅС‚Р°."
        )
        if len(unique_documents) >= 2:
            limitations.append(
                "РќР°Р№РґРµРЅРѕ РЅРµСЃРєРѕР»СЊРєРѕ РєРѕРЅРєСѓСЂРёСЂСѓСЋС‰РёС… РЅРѕСЂРјР°С‚РёРІРЅС‹С… РІРµС‚РѕРє, РїРѕСЌС‚РѕРјСѓ СЃРёСЃС‚РµРјР° РїСЂРѕСЃРёС‚ СѓС‚РѕС‡РЅРµРЅРёРµ РІРјРµСЃС‚Рѕ СѓРІРµСЂРµРЅРЅРѕРіРѕ РїСЂСЏРјРѕРіРѕ РѕС‚РІРµС‚Р°."
            )
        return limitations

    if answer_mode == "partial":
        if retrieval_unit_count == 0 and node_only_count > 0:
            limitations.append(
                "РћС‚РІРµС‚ С‡Р°СЃС‚РёС‡РЅС‹Р№: РЅР°Р№РґРµРЅС‹ РІ РѕСЃРЅРѕРІРЅРѕРј С‚РѕС‡РµС‡РЅС‹Рµ node-level С„СЂР°РіРјРµРЅС‚С‹ Р±РµР· РґРѕСЃС‚Р°С‚РѕС‡РЅРѕРіРѕ СЃРµРјР°РЅС‚РёС‡РµСЃРєРѕРіРѕ Р±Р»РѕРєР° РІРѕРєСЂСѓРі РЅРёС…."
            )
        elif retrieval_unit_count < 2:
            limitations.append(
                "РћС‚РІРµС‚ С‡Р°СЃС‚РёС‡РЅС‹Р№: РЅР°Р№РґРµРЅРЅС‹С… РєРѕРЅС‚РµРєСЃС‚РЅС‹С… retrieval-unit С„СЂР°РіРјРµРЅС‚РѕРІ РїРѕРєР° РЅРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РґР»СЏ РїРѕР»РЅРѕСЃС‚СЊСЋ СѓРІРµСЂРµРЅРЅРѕРіРѕ РїСЂСЏРјРѕРіРѕ РІС‹РІРѕРґР°."
            )
        if parsed_query.explicit_locator_values and not has_locator:
            limitations.append(
                "РћС‚РІРµС‚ С‡Р°СЃС‚РёС‡РЅС‹Р№: СЂРµР»РµРІР°РЅС‚РЅС‹Р№ РґРѕРєСѓРјРµРЅС‚ РЅР°Р№РґРµРЅ, РЅРѕ РѕР¶РёРґР°РµРјС‹Р№ locator РЅРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅ РІ РёС‚РѕРіРѕРІРѕРј evidence-РїР°РєРµС‚Рµ."
            )
        limitations.append(
            "Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅР°СЏ LLM-РІРµСЂРёС„РёРєР°С†РёСЏ РІ interactive-СЂРµР¶РёРјРµ РїСЂРѕРїСѓС‰РµРЅР°, С‡С‚РѕР±С‹ РЅРµ СѓСЂРµР·Р°С‚СЊ СѓР¶Рµ РЅР°Р№РґРµРЅРЅС‹Р№ РєРѕРЅС‚РµРєСЃС‚."
        )
    return limitations


def _normalize_inline_text(text: str, limit: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _build_event(
    event_type: Literal[
        "query_received",
        "query_rewritten",
        "controller_started",
        "tool_started",
        "tool_finished",
        "evidence_updated",
        "composer_started",
        "verifier_started",
        "answer_ready",
        "warning",
    ],
    message: str,
    *,
    payload: dict | None = None,
    level: Literal["info", "warning"] = "info",
    is_terminal: bool = False,
) -> RuntimeEventDTO:
    return RuntimeEventDTO(
        event_type=event_type,
        message=message,
        payload=payload or {},
        level=level,
        is_terminal=is_terminal,
    )


def _build_controller_trajectory_events(result: ControllerAgentResult) -> list[RuntimeEventDTO]:
    events: list[RuntimeEventDTO] = []
    ordered_step_ids = sorted({key.rsplit("_", 1)[-1] for key in result.trajectory if "_" in key})
    for step_id in ordered_step_ids:
        tool_name_key = f"tool_name_{step_id}"
        observation_key = f"observation_{step_id}"
        tool_name = str(result.trajectory.get(tool_name_key, "unknown_tool"))
        if tool_name_key in result.trajectory:
            events.append(
                _build_event(
                    "tool_started",
                    f"Запущен tool `{tool_name}`.",
                    payload={"step_id": step_id, "tool_name": tool_name},
                )
            )
        if observation_key in result.trajectory:
            events.append(
                _build_event(
                    "tool_finished",
                    f"Tool `{tool_name}` вернул observation.",
                    payload={
                        "step_id": step_id,
                        "tool_name": tool_name,
                        "observation": _normalize_inline_text(str(result.trajectory[observation_key]), limit=300),
                    },
                )
            )
    return events
