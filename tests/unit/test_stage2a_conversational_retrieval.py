from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.contracts import AnswerClaimDTO, ConversationMessageDTO, EvidenceItemDTO, Stage2AAnswerDTO, Stage2AConversationalQueryRequest
from qanorm.stage2a.runtime import Stage2ARuntime
from qanorm.stage2a.session_memory import create_chat_session


class _FakeSession:
    def close(self) -> None:
        self.closed = True


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


def _build_strong_evidence() -> list[EvidenceItemDTO]:
    document_id = uuid4()
    version_id = uuid4()
    return [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="retrieval_unit_lexical",
            document_id=document_id,
            document_version_id=version_id,
            document_display_code="СП 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator="10.3.8",
            heading_path="Раздел 10 > 10.3",
            score=0.96,
            text="Основной semantic block.",
        ),
        EvidenceItemDTO(
            evidence_id="ev-0002",
            source_kind="retrieval_unit_context",
            document_id=document_id,
            document_version_id=version_id,
            document_display_code="СП 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator="10.3.9",
            heading_path="Раздел 10 > 10.3",
            score=0.91,
            text="Соседний semantic block.",
        ),
    ]


def test_conversational_clarify_query_uses_previous_answer_and_locator(monkeypatch) -> None:
    captured: dict[str, str] = {}
    evidence = _build_strong_evidence()

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            captured["retrieval_query"] = query_text
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["clarify", "locator"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller reused locator hints.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence[:1],
                trajectory={"step": "clarify"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Уточняющий ответ по пункту.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence[:1],
                limitations=["Нужен точный контекст пункта."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Уточняющий ответ по пункту.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence[:1],
                limitations=[],
            )

    session = create_chat_session("session-clarify")
    session = session.model_copy(
        update={
            "messages": [
                ConversationMessageDTO(role="user", content="Что по шагу арматуры в плитах?"),
                ConversationMessageDTO(role="assistant", content="Частичный ответ по СП 63."),
            ],
            "memory": session.memory.model_copy(
                update={
                    "conversation_summary": "Обсуждается шаг арматуры в плитах по СП 63.",
                    "active_document_hints": ["СП 63.13330.2018"],
                    "active_locator_hints": ["10.3.8"],
                    "open_threads": ["шаг арматуры в плитах :: нужен точный пункт"],
                }
            ),
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
            query_text="Какой пункт?",
            chat_session=session,
        )
    )

    assert result.query_kind == "clarify"
    assert "10.3.8" in result.effective_query
    assert "Предыдущий ответ" in result.effective_query
    assert "ищи точный пункт" in captured["retrieval_query"]


def test_conversational_expand_answer_query_mentions_open_threads(monkeypatch) -> None:
    captured: dict[str, str] = {}
    evidence = _build_strong_evidence()[:1]

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            captured["retrieval_query"] = query_text
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_tokens=["expand", "answer"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller expanded the previous answer.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"step": "expand"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Дополненный ответ.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Нужно найти соседний фрагмент."],
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
                limitations=[],
            )

    session = create_chat_session("session-expand")
    session = session.model_copy(
        update={
            "messages": [
                ConversationMessageDTO(role="user", content="Что по шагу арматуры?"),
                ConversationMessageDTO(role="assistant", content="Частичный ответ по плитам."),
            ],
            "memory": session.memory.model_copy(
                update={
                    "conversation_summary": "Обсуждается шаг арматуры в плитах по СП 63.",
                    "active_document_hints": ["СП 63.13330.2018"],
                    "open_threads": ["шаг арматуры в плитах :: нужен соседний фрагмент"],
                }
            ),
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
    assert "Незакрытые темы" in result.effective_query
    assert "partial мог стать direct" in captured["retrieval_query"]


def test_conversational_follow_up_can_promote_partial_to_direct(monkeypatch) -> None:
    evidence = _build_strong_evidence()

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
                trajectory={"step": "follow-up"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["answer_mode"] == "direct"
            return SimpleNamespace(
                answer_mode="direct",
                answer_text="Полный ответ после follow-up.",
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
                answer_text="Ответ по новой теме.",
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
                answer_text="Полный ответ после follow-up.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    session = create_chat_session("session-followup")
    session = session.model_copy(
        update={
            "messages": [
                ConversationMessageDTO(role="user", content="Что по шагу арматуры?"),
                ConversationMessageDTO(role="assistant", content="Частичный ответ по плитам."),
            ],
            "memory": session.memory.model_copy(update={"active_document_hints": ["СП 63.13330.2018"]}),
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
    assert result.result.answer.mode == "direct"


def test_conversational_explicit_new_document_resets_previous_context(monkeypatch) -> None:
    captured: dict[str, str] = {}
    evidence = _build_strong_evidence()[:1]

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            captured["retrieval_query"] = query_text
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=["СП 20"],
                explicit_locator_values=[],
                lexical_tokens=["loads"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller switched to another document family.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"step": "new-question"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Ответ по новой теме.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Нужен дополнительный контекст."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Ответ по новой теме.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=[],
            )

    session = create_chat_session("session-shift")
    session = session.model_copy(
        update={
            "messages": [
                ConversationMessageDTO(role="user", content="Что по шагу арматуры?"),
                ConversationMessageDTO(role="assistant", content="Ответ по СП 63."),
            ],
            "memory": session.memory.model_copy(
                update={
                    "conversation_summary": "Обсуждается СП 63 и шаг арматуры в плитах.",
                    "active_document_hints": ["СП 63.13330.2018"],
                    "active_locator_hints": ["10.3.8"],
                }
            ),
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
            query_text="Что СП 20 говорит о сочетаниях нагрузок?",
            chat_session=session,
        )
    )

    assert result.query_kind == "new_question"
    assert result.effective_query == "Что СП 20 говорит о сочетаниях нагрузок?"
    assert captured["retrieval_query"] == "Что СП 20 говорит о сочетаниях нагрузок?"
