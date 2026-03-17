from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.contracts import AnswerClaimDTO, Stage2AAnswerDTO, Stage2AConversationalQueryRequest
from qanorm.stage2a.runtime import Stage2ARuntime
from qanorm.stage2a.session_memory import create_chat_session


class _FakeSession:
    def close(self) -> None:
        self.closed = True


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


def test_conversational_turn_updates_session_memory(monkeypatch) -> None:
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
                reasoning_summary="Controller used conversation context.",
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
                limitations=["Нужно уточнение по виду плиты."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    session = create_chat_session("session-integration")
    session = session.model_copy(
        update={
            "memory": session.memory.model_copy(
                update={
                    "conversation_summary": "Ранее обсуждался шаг арматуры в плитах по СП 63.13330.2018.",
                    "active_document_hints": ["СП 63.13330.2018"],
                }
            )
        }
    )

    result = runtime.answer_conversation_turn(
        Stage2AConversationalQueryRequest(
            query_text="А что для плит?",
            chat_session=session,
        )
    )

    assert result.query_kind == "follow_up"
    assert result.result.answer.mode == "direct"
    assert result.chat_session.messages[-1].content == "Дополнительный ответ по плитам."
    assert result.chat_session.memory.active_document_hints == ["СП 63.13330.2018"]
    assert result.chat_session.memory.active_locator_hints == ["10.3.8"]
    assert result.chat_session.memory.open_threads == []
    assert result.chat_session.last_result is not None
