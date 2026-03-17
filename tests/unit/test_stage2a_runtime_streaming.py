from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.contracts import (
    AnswerClaimDTO,
    ConversationMessageDTO,
    EvidenceItemDTO,
    RuntimeEventDTO,
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


def test_runtime_stream_answer_query_emits_stage_events(monkeypatch) -> None:
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
                trajectory={
                    "tool_name_0": "lookup_locator",
                    "observation_0": "Located clause 5.1 in the active document.",
                },
                policy_hint="resolve then lookup",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="direct",
                answer_text="Draft answer [ev-0001].",
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
                answer_text="Verified answer [ev-0001].",
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
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _ParseOnlyRetrieval())

    events = list(runtime.stream_answer_query("What does SP 63 say in 5.1?"))
    event_types = [event.event_type for event in events]

    assert event_types == [
        "query_received",
        "controller_started",
        "tool_started",
        "tool_finished",
        "evidence_updated",
        "composer_started",
        "verifier_started",
        "answer_ready",
    ]
    assert all(isinstance(event, RuntimeEventDTO) for event in events)
    assert events[-1].is_terminal is True
    assert events[-1].payload["result"]["answer"]["answer_text"] == "Verified answer [ev-0001]."


def test_runtime_stream_answer_query_emits_warning_for_missing_evidence(monkeypatch) -> None:
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

    events = list(runtime.stream_answer_query("Need clarification"))

    assert [event.event_type for event in events] == [
        "query_received",
        "controller_started",
        "evidence_updated",
        "warning",
        "answer_ready",
    ]
    assert events[-2].level == "warning"
    assert "evidence" in events[-2].message


def test_runtime_stream_answer_query_emits_warning_when_verifier_is_skipped(monkeypatch) -> None:
    evidence = _build_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller stayed conservative.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"tool_name_0": "search_lexical", "observation_0": "Found one fragment."},
                policy_hint="search then compose",
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
                limitations=["One supported fragment remained."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("verifier must not be called")),
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _ParseOnlyRetrieval())

    events = list(runtime.stream_answer_query("What does SP 63 say?"))
    warnings = [event for event in events if event.event_type == "warning"]

    assert any("Verifier" in event.message for event in warnings)
    assert any("огранич" in event.message.casefold() for event in warnings)
    assert events[-1].event_type == "answer_ready"


def test_runtime_stream_conversation_turn_emits_rewritten_query_and_updated_session(monkeypatch) -> None:
    document_id = uuid4()
    version_id = uuid4()
    evidence = [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="retrieval_unit_lexical",
            document_id=document_id,
            document_version_id=version_id,
            document_display_code="SP 63.13330.2018",
            retrieval_unit_id=uuid4(),
            locator=None,
            heading_path="Section 10",
            score=0.88,
            text="Expanded semantic block for the follow-up question.",
        )
    ]

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Controller used session memory.",
                selected_evidence_ids=[item.evidence_id for item in evidence],
                evidence=evidence,
                trajectory={"tool_name_0": "expand_neighbors", "observation_0": "Expanded the surrounding context."},
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
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Expanded answer.",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Need a bit more context."],
            )

    session = create_chat_session("session-3")
    session = session.model_copy(
        update={
            "messages": [
                ConversationMessageDTO(role="user", content="Что по шагу арматуры?"),
                ConversationMessageDTO(role="assistant", content="Частичный ответ по плитам."),
            ],
            "memory": session.memory.model_copy(
                update={
                    "conversation_summary": "Обсуждался шаг арматуры в плитах по СП 63.",
                    "active_document_hints": ["СП 63.13330.2018"],
                    "active_locator_hints": ["10.3.8"],
                    "open_threads": ["шаг арматуры в плитах :: нужен дополнительный контекст"],
                }
            ),
        }
    )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("verifier must not be called")),
    )
    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    events = list(
        runtime.stream_conversation_turn(
            Stage2AConversationalQueryRequest(
                query_text="А для фундаментов?",
                chat_session=session,
            )
        )
    )

    event_types = [event.event_type for event in events]
    assert event_types[0] == "query_received"
    assert "query_rewritten" in event_types
    assert "tool_started" in event_types
    assert "tool_finished" in event_types
    assert event_types[-1] == "answer_ready"
    conversation_result = events[-1].payload["conversation_result"]
    assert conversation_result["query_kind"] == "follow_up"
    assert conversation_result["effective_query"] != "А для фундаментов?"
    assert conversation_result["chat_session"]["messages"][-1]["role"] == "assistant"
