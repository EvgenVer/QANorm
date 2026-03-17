from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.contracts import (
    AnswerClaimDTO,
    ConversationMessageDTO,
    EvidenceItemDTO,
    Stage2AAnswerDTO,
    Stage2AConversationalQueryRequest,
)
from qanorm.stage2a.runtime import Stage2ARuntime
from qanorm.stage2a.session_memory import create_chat_session


class _FakeSession:
    def close(self) -> None:
        self.closed = True


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


class _ParseOnlyRetrieval:
    def parse_query(self, query_text: str):
        return SimpleNamespace(
            raw_text=query_text,
            normalized_text=query_text,
            explicit_document_codes=[],
            explicit_locator_values=[],
            lexical_tokens=["need", "clarification"],
        )

    def build_evidence_pack(self, query_text: str):
        return []


def _build_evidence() -> list[EvidenceItemDTO]:
    document_id = uuid4()
    version_id = uuid4()
    return [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="document_node_locator",
            document_id=document_id,
            document_version_id=version_id,
            document_display_code="SP 63.13330.2018",
            node_id=uuid4(),
            retrieval_unit_id=None,
            locator="5.1",
            heading_path="Section 5",
            score=1.0,
            text="Structural requirement text.",
        )
    ]


def _build_unit_evidence() -> list[EvidenceItemDTO]:
    document_id = uuid4()
    version_id = uuid4()
    return [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="retrieval_unit_lexical",
            document_id=document_id,
            document_version_id=version_id,
            document_display_code="SP 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator="10.3.8",
            heading_path="Section 10 > 10.3",
            score=0.96,
            text="Main semantic block.",
        ),
        EvidenceItemDTO(
            evidence_id="ev-0002",
            source_kind="retrieval_unit_context",
            document_id=document_id,
            document_version_id=version_id,
            document_display_code="SP 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator="10.3.8",
            heading_path="Section 10 > 10.3",
            score=0.9,
            text="Neighbor semantic block.",
        ),
    ]


def test_runtime_skips_answer_modules_when_controller_has_no_evidence(monkeypatch) -> None:
    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="clarify",
                reasoning_summary="Need more context.",
                selected_evidence_ids=[],
                evidence=[],
                trajectory={"step": "none"},
                policy_hint="discover first",
                iterations_used=1,
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("composer must not be called")),
        verifier_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("verifier must not be called")),
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _ParseOnlyRetrieval())

    result = runtime.answer_query("Need clarification")

    assert result.answer.mode == "clarify"
    assert result.answer.evidence == []
    assert "evidence" in result.answer.limitations[0]


def test_runtime_runs_full_answer_flow(monkeypatch) -> None:
    evidence = _build_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="direct",
                reasoning_summary="One supported fragment was found.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"tool_name_0": "lookup_locator"},
                policy_hint="resolve then lookup",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Draft answer [ev-0001].",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Draft limitation."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="partial",
                answer_text="Verified answer [ev-0001].",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["One supported fragment remained."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _ParseOnlyRetrieval())

    result = runtime.answer_query("What does SP 63 say in 5.1?")

    assert result.answer.mode == "partial"
    assert result.answer.answer_text == "Verified answer [ev-0001]."
    assert result.answer.debug_trace[0].startswith("tool_name_0")


def test_runtime_uses_fallback_evidence_pack_when_controller_selected_none(monkeypatch) -> None:
    evidence = _build_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="no_answer",
                reasoning_summary="Controller failed to name explicit evidence ids.",
                selected_evidence_ids=[],
                evidence=[],
                trajectory={"step": "fallback"},
                policy_hint="discover first",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["sp63", "requirements"],
            )

        def build_evidence_pack(self, query_text: str):
            from qanorm.stage2a.retrieval.engine import RetrievalHit

            item = evidence[0]
            return [
                RetrievalHit(
                    source_kind=item.source_kind,
                    score=item.score,
                    document_id=item.document_id,
                    document_version_id=item.document_version_id,
                    node_id=item.node_id,
                    retrieval_unit_id=item.retrieval_unit_id,
                    order_index=1,
                    locator=item.locator,
                    heading_path=item.heading_path,
                    text=item.text,
                    document_display_code=item.document_display_code,
                )
            ]

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["evidence"][0].evidence_id.startswith("ev-fallback-")
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Draft answer from fallback evidence.",
                claims=[AnswerClaimDTO(text="Fallback claim.", evidence_ids=[kwargs["evidence"][0].evidence_id])],
                evidence=kwargs["evidence"],
                limitations=["Fallback path used."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("verifier must be skipped for partial answers")),
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_query("What does SP63 say?")

    assert result.answer.mode == "partial"
    assert result.answer.evidence[0].evidence_id.startswith("ev-fallback-")
    assert "Runtime fallback used the deterministic evidence pack." in result.controller.reasoning_summary
    assert result.answer.limitations


def test_runtime_promotes_partial_to_direct_when_one_document_has_enough_context(monkeypatch) -> None:
    evidence = _build_unit_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller stayed conservative.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"step": "lexical"},
                policy_hint="resolve then search",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=["SP 63"],
                explicit_locator_values=[],
                lexical_tokens=["step", "reinforcement", "slabs"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["answer_mode"] == "direct"
            return SimpleNamespace(
                answer_mode="direct",
                answer_text="Direct grounded answer.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Verified direct answer.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_query("What does SP63 say about slab reinforcement spacing?")

    assert result.controller.answer_mode == "direct"
    assert result.answer.mode == "direct"
    assert "promoted the answer to direct" in result.controller.reasoning_summary


def test_runtime_switches_to_clarify_for_broad_multi_document_query(monkeypatch) -> None:
    evidence = [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="retrieval_unit_lexical",
            document_id=uuid4(),
            document_version_id=uuid4(),
            document_display_code="SP 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator=None,
            heading_path="Section 10",
            score=0.7,
            text="Joints in reinforced concrete.",
        ),
        EvidenceItemDTO(
            evidence_id="ev-0002",
            source_kind="retrieval_unit_lexical",
            document_id=uuid4(),
            document_version_id=uuid4(),
            document_display_code="SP 17.13330.2017",
            retrieval_unit_id=uuid4(),
            locator=None,
            heading_path="Section 5",
            score=0.69,
            text="Joints in roofing systems.",
        ),
    ]

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="direct",
                reasoning_summary="Controller answered directly.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"step": "discover"},
                policy_hint="discover first",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["deformation", "joints"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["answer_mode"] == "clarify"
            return SimpleNamespace(
                answer_mode="clarify",
                answer_text="Need clarification.",
                claims=[],
                evidence=evidence,
                limitations=["Broad question."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("verifier must not be called")),
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_query("What is required for deformation joints?")

    assert result.controller.answer_mode == "clarify"
    assert result.answer.mode == "clarify"
    assert "switched the answer to clarify" in result.controller.reasoning_summary
    assert result.answer.limitations


def test_runtime_keeps_direct_for_broad_query_when_one_document_has_strong_context(monkeypatch) -> None:
    base_evidence = _build_unit_evidence()
    evidence = base_evidence + [
        EvidenceItemDTO(
            evidence_id="ev-0003",
            source_kind="retrieval_unit_context",
            document_id=base_evidence[0].document_id,
            document_version_id=base_evidence[0].document_version_id,
            document_display_code="SP 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator="10.3.9",
            heading_path="Section 10 > 10.3",
            score=0.88,
            text="Additional contextual block from the same document.",
        )
    ]

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="clarify",
                reasoning_summary="Controller asked for clarification too early.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"step": "discover"},
                policy_hint="discover first",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["what", "requirements", "slabs"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["answer_mode"] == "direct"
            return SimpleNamespace(
                answer_mode="direct",
                answer_text="Direct grounded answer.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Verified direct answer.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_query("What requirements apply to slab reinforcement?")

    assert result.controller.answer_mode == "direct"
    assert result.answer.mode == "direct"
    assert "promoted the answer to direct" in result.controller.reasoning_summary


def test_runtime_promotes_partial_to_direct_when_one_document_dominates_two_document_pack(monkeypatch) -> None:
    primary = _build_unit_evidence()
    evidence = primary + [
        EvidenceItemDTO(
            evidence_id="ev-0003",
            source_kind="retrieval_unit_context",
            document_id=primary[0].document_id,
            document_version_id=primary[0].document_version_id,
            document_display_code="SP 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator="10.3.8",
            heading_path="Section 10 > 10.3",
            score=0.85,
            text="More context from the same document.",
        ),
        EvidenceItemDTO(
            evidence_id="ev-0004",
            source_kind="retrieval_unit_lexical",
            document_id=uuid4(),
            document_version_id=uuid4(),
            document_display_code="SNIP II-90-81",
            retrieval_unit_id=uuid4(),
            locator=None,
            heading_path="Legacy section",
            score=0.3,
            text="A weak legacy tail that should not block a direct answer.",
        ),
    ]

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller stayed conservative due to extra tail evidence.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"step": "discover"},
                policy_hint="discover first",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["requirements", "reinforcement", "slabs"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["answer_mode"] == "direct"
            return SimpleNamespace(
                answer_mode="direct",
                answer_text="Direct grounded answer from the dominant document.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Verified direct answer.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_query("What requirements apply to slab reinforcement?")

    assert result.controller.answer_mode == "direct"
    assert result.answer.mode == "direct"


def test_runtime_answer_conversation_turn_builds_effective_query_from_session_context(monkeypatch) -> None:
    captured: dict[str, str] = {}
    evidence = _build_unit_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            captured["controller_query"] = query_text
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller used conversational context.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"step": "conversational"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["follow", "up"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            captured["composer_query"] = kwargs["query_text"]
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Дополнение по фундаментам.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Нужно уточнение по типу фундамента."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Дополнение по фундаментам.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Нужно уточнение по типу фундамента."],
            )

    session = create_chat_session("session-1")
    session = session.model_copy(
        update={
            "memory": session.memory.model_copy(
                update={
                    "conversation_summary": "Документы в фокусе: СП 63.13330.2018. Незакрытая тема: шаг арматуры в плитах.",
                    "active_document_hints": ["СП 63.13330.2018"],
                    "active_locator_hints": ["10.3.8"],
                    "open_threads": ["шаг арматуры в плитах :: нужен контекст по другой конструкции"],
                }
            )
        }
    )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_conversation_turn(
        Stage2AConversationalQueryRequest(
            query_text="А для фундаментов?",
            chat_session=session,
        )
    )

    assert result.query_kind == "follow_up"
    assert "Контекст беседы" in result.effective_query
    assert "СП 63.13330.2018" in captured["controller_query"]
    assert captured["composer_query"] == "А для фундаментов?"
    assert result.chat_session.messages[-2].role == "user"
    assert result.chat_session.messages[-1].role == "assistant"
    assert result.chat_session.memory.open_threads


def test_runtime_answer_conversation_turn_classifies_expand_request(monkeypatch) -> None:
    evidence = _build_unit_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller used previous answer.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"step": "expand"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["дополни", "ответ"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Дополненный ответ.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Требуются дополнительные детали."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Дополненный ответ.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Требуются дополнительные детали."],
            )

    session = create_chat_session("session-2")
    session = session.model_copy(
        update={
            "messages": [
                ConversationMessageDTO(role="user", content="Что по шагу арматуры?"),
                ConversationMessageDTO(role="assistant", content="Частичный ответ по плитам."),
            ],
            "memory": session.memory.model_copy(update={"conversation_summary": "Обсуждался шаг арматуры в плитах."}),
        }
    )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_conversation_turn(
        Stage2AConversationalQueryRequest(
            query_text="Дополни ответ",
            chat_session=session,
        )
    )

    assert result.query_kind == "expand_answer"
    assert "Пользователь просит дополнить предыдущий ответ" in result.effective_query
