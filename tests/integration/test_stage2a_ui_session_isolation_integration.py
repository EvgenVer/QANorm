from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.config import get_stage2a_config
from qanorm.stage2a.contracts import (
    AnswerClaimDTO,
    Stage2AAnswerDTO,
    Stage2AConversationalQueryRequest,
)
from qanorm.stage2a.runtime import Stage2ARuntime
from qanorm.stage2a.ui.session_state import (
    create_new_ui_session,
    ensure_ui_sessions,
    get_active_ui_session,
    replace_active_ui_session,
    set_active_ui_session,
)


class _FakeSession:
    def close(self) -> None:
        self.closed = True


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


def test_local_chat_sessions_stay_isolated(monkeypatch) -> None:
    document_id = uuid4()
    version_id = uuid4()
    evidence = [
        {
            "evidence_id": "ev-0001",
            "source_kind": "retrieval_unit_lexical",
            "document_id": str(document_id),
            "document_version_id": str(version_id),
            "document_display_code": "СП 63.13330.2018",
            "document_title": "СП 63",
            "retrieval_unit_id": str(uuid4()),
            "locator": "10.3.8",
            "heading_path": "Раздел 10",
            "score": 0.95,
            "text": "Максимальный шаг арматуры в плитах не должен превышать ...",
        }
    ]

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=["СП 63"],
                explicit_locator_values=[],
                lexical_tokens=["плиты", "арматура"],
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
                reasoning_summary="Controller used session context.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"step": "conversation"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Дополнительный ответ по плитам.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=kwargs["evidence"],
                limitations=["Нужно уточнение по виду плиты."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Дополнительный ответ по плитам.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=kwargs["draft"].evidence,
                limitations=[],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(
            controller=object(),
            composer=object(),
            verifier=object(),
            reranker=object(),
            provider_name="gemini",
        ),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    state: dict[str, object] = {}
    config = get_stage2a_config()
    ensure_ui_sessions(state, config=config)

    first_session = get_active_ui_session(state)
    second_session = create_new_ui_session(state, config=config)

    set_active_ui_session(state, first_session.session_id)
    result = runtime.answer_conversation_turn(
        Stage2AConversationalQueryRequest(
            query_text="А что для плит?",
            chat_session=get_active_ui_session(state),
        )
    )
    replace_active_ui_session(state, result.chat_session)

    set_active_ui_session(state, second_session.session_id)
    active_second = get_active_ui_session(state)
    assert active_second.messages == []
    assert active_second.memory.conversation_summary == ""
    assert active_second.runtime_events == []

    set_active_ui_session(state, first_session.session_id)
    active_first = get_active_ui_session(state)
    assert active_first.messages[-1].role == "assistant"
    assert active_first.memory.active_document_hints == ["СП 63.13330.2018"]
    assert active_first.last_result is not None


def test_resetting_active_session_does_not_touch_other_local_chats(monkeypatch) -> None:
    document_id = uuid4()
    version_id = uuid4()
    evidence = [
        {
            "evidence_id": "ev-0001",
            "source_kind": "retrieval_unit_lexical",
            "document_id": str(document_id),
            "document_version_id": str(version_id),
            "document_display_code": "СП 1.13130.2020",
            "document_title": "СП 1",
            "retrieval_unit_id": str(uuid4()),
            "locator": "4.2",
            "heading_path": "Пути эвакуации",
            "score": 0.91,
            "text": "Минимальные ширины путей эвакуации определяются ...",
        }
    ]

    class _FakeRetrieval:
        def parse_query(self, query_text: str):
            return SimpleNamespace(
                raw_text=query_text,
                normalized_text=query_text,
                explicit_document_codes=["СП 1"],
                explicit_locator_values=[],
                lexical_tokens=["эвакуация", "выход"],
            )

        def build_evidence_pack(self, query_text: str):
            return []

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="direct",
                reasoning_summary="Controller used fire-safety context.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"step": "conversation"},
                policy_hint="conversation aware",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="direct",
                answer_text="Пожарный ответ по путям эвакуации.",
                claims=[AnswerClaimDTO(text="Supported fire-safety claim.", evidence_ids=["ev-0001"])],
                evidence=kwargs["evidence"],
                limitations=[],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="direct",
                answer_text="Пожарный ответ по путям эвакуации.",
                claims=[AnswerClaimDTO(text="Supported fire-safety claim.", evidence_ids=["ev-0001"])],
                evidence=kwargs["draft"].evidence,
                limitations=[],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(
            controller=object(),
            composer=object(),
            verifier=object(),
            reranker=object(),
            provider_name="gemini",
        ),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    state: dict[str, object] = {}
    config = get_stage2a_config()
    ensure_ui_sessions(state, config=config)

    first_session = get_active_ui_session(state)
    second_session = create_new_ui_session(state, config=config)

    set_active_ui_session(state, second_session.session_id)
    result = runtime.answer_conversation_turn(
        Stage2AConversationalQueryRequest(
            query_text="Что СП 1 говорит про пути эвакуации?",
            chat_session=get_active_ui_session(state),
        )
    )
    replace_active_ui_session(state, result.chat_session)

    set_active_ui_session(state, first_session.session_id)
    untouched_first = get_active_ui_session(state)
    assert untouched_first.messages == []
    assert untouched_first.memory.active_document_hints == []
    assert untouched_first.last_result is None

    set_active_ui_session(state, second_session.session_id)
    active_second = get_active_ui_session(state)
    assert active_second.messages[-1].content == "Пожарный ответ по путям эвакуации."
    assert active_second.memory.active_document_hints == ["СП 1.13130.2020"]
    assert active_second.last_result is not None
