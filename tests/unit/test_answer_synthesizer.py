from __future__ import annotations

import asyncio
from uuid import uuid4

from qanorm.agents.answer_synthesizer import AnswerSynthesizer
from qanorm.agents.planner import QueryIntent
from qanorm.db.types import AnswerMode, CoverageStatus, EvidenceSourceKind, FreshnessStatus, MessageRole, QueryStatus
from qanorm.models import Document, QAEvidence, QAMessage, QAQuery
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.prompts.registry import create_prompt_registry
from qanorm.providers.base import ChatModelProvider, ChatRequest, ChatResponse, ProviderCapabilities, ProviderName
from tests.unit.test_provider_registry import _runtime_config


class _FakeChatProvider(ChatModelProvider):
    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(chat=True)

    def __init__(self, content: str) -> None:
        self.model = "answer-test"
        self._content = content

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(provider=self.provider_name, model=request.model, content=self._content)


class _AnswerRepositoryStub:
    def __init__(self) -> None:
        self.saved = None

    def get_by_query(self, query_id):
        return None

    def save(self, answer):
        self.saved = answer
        return answer


class _MessageRepositoryStub:
    def __init__(self) -> None:
        self.saved = None

    def add(self, message):
        self.saved = message
        return message


class _QueryRepositoryStub:
    def update_state(self, query, *, status, **kwargs):
        query.status = status
        return query


def test_answer_synthesizer_falls_back_to_evidence_sections_and_prioritizes_normative() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("invalid-json"),
        answer_repository=_AnswerRepositoryStub(),
        message_repository=_MessageRepositoryStub(),
        query_repository=_QueryRepositoryStub(),
    )
    state = _build_query_state()

    answer = asyncio.run(
        synthesizer.synthesize(
            state,
            assumptions=["Принят российский нормативный контекст."],
            limitations=["Не рассмотрены проектные исходные данные."],
        )
    )

    assert answer.sections[0].source_kind == EvidenceSourceKind.NORMATIVE
    assert answer.answer_mode == AnswerMode.PARTIAL_ANSWER
    assert answer.coverage_status == CoverageStatus.PARTIAL
    assert answer.has_external_sources is True
    assert "Ненормативные источники" in answer.markdown
    assert "Допущения" in answer.markdown
    assert "Ограничения" in answer.markdown


def test_answer_synthesizer_persists_answer_and_assistant_message() -> None:
    answer_repository = _AnswerRepositoryStub()
    message_repository = _MessageRepositoryStub()
    query_repository = _QueryRepositoryStub()
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("invalid-json"),
        answer_repository=answer_repository,
        message_repository=message_repository,
        query_repository=query_repository,
    )
    query = QAQuery(id=uuid4(), session_id=uuid4(), message_id=uuid4(), query_text="Какое требование?", status=QueryStatus.SYNTHESIZING)

    structured = asyncio.run(synthesizer.synthesize(_build_query_state()))
    saved_answer, assistant_message = synthesizer.persist_answer(query=query, answer=structured)

    assert saved_answer.status.value == "completed"
    assert saved_answer.coverage_status.value in {"complete", "partial", "insufficient"}
    assert assistant_message.role == MessageRole.ASSISTANT
    assert assistant_message.metadata_json["answer_format"] == "markdown"
    assert query.status == QueryStatus.COMPLETED


def test_answer_synthesizer_accepts_model_json_sections() -> None:
    provider = _FakeChatProvider(
        """
        {
          "sections": [
            {
              "heading": "Краткий вывод",
              "body": "Нормативные требования подтверждены.",
              "source_kind": "normative"
            },
            {
              "heading": "Дополнительные данные",
              "body": "Практические замечания требуют проверки.",
              "source_kind": "open_web"
            }
          ]
        }
        """
    )
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=provider,
        answer_repository=_AnswerRepositoryStub(),
        message_repository=_MessageRepositoryStub(),
        query_repository=_QueryRepositoryStub(),
    )

    answer = asyncio.run(synthesizer.synthesize(_build_query_state()))

    assert [section.heading for section in answer.sections] == ["Краткий вывод", "Дополнительные данные"]
    assert answer.sections[0].source_kind == EvidenceSourceKind.NORMATIVE
    assert answer.sections[1].source_kind == EvidenceSourceKind.OPEN_WEB


def test_answer_synthesizer_prompt_render_snapshot_contains_shared_policies() -> None:
    runtime_config = _runtime_config()
    registry = create_prompt_registry(runtime_config)
    prompt = registry.render("answer_synthesizer", context=_build_query_state().build_prompt_context())

    assert "You synthesize the final engineering answer from collected evidence." in prompt.text
    assert "Prefer normative evidence and cite it explicitly." in prompt.text
    assert "Mark non-normative information as requiring user verification." in prompt.text


def test_answer_synthesizer_short_circuits_clarify_path() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("unused"),
        answer_repository=_AnswerRepositoryStub(),
        message_repository=_MessageRepositoryStub(),
        query_repository=_QueryRepositoryStub(),
    )
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="СП 63",
        intent=QueryIntent.CLARIFY.value,
        clarification_required=True,
        clarification_question="Уточните, какой пункт СП 63 нужно проверить.",
    )

    answer = asyncio.run(synthesizer.synthesize(state))

    assert answer.coverage_status == CoverageStatus.INSUFFICIENT
    assert "Уточните" in answer.answer_text
    assert answer.model_name == "intent_gate:clarify"
    assert answer.answer_mode == AnswerMode.CLARIFY


def test_answer_synthesizer_short_circuits_no_retrieval_path() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("unused"),
        answer_repository=_AnswerRepositoryStub(),
        message_repository=_MessageRepositoryStub(),
        query_repository=_QueryRepositoryStub(),
    )
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="Привет",
        intent=QueryIntent.NO_RETRIEVAL.value,
    )

    answer = asyncio.run(synthesizer.synthesize(state))

    assert answer.coverage_status == CoverageStatus.INSUFFICIENT
    assert "не был отправлен в нормативный retrieval" in answer.answer_text
    assert answer.model_name == "intent_gate:no_retrieval"
    assert answer.answer_mode == AnswerMode.DECLINE


def _build_query_state() -> QueryState:
    session_id = uuid4()
    query_id = uuid4()
    document = Document(id=uuid4(), normalized_code="СП 1", display_code="СП 1", title="СП 1")
    normative = QAEvidence(
        query_id=query_id,
        source_kind=EvidenceSourceKind.NORMATIVE,
        document_id=document.id,
        document_version_id=uuid4(),
        locator="1.2",
        locator_end="1.2.1",
        quote="Требование должно выполняться.",
        chunk_text="Требование должно выполняться.",
        freshness_status=FreshnessStatus.FRESH,
        is_normative=True,
        requires_verification=False,
    )
    normative.document = document
    external = QAEvidence(
        query_id=query_id,
        source_kind=EvidenceSourceKind.OPEN_WEB,
        source_domain="example.com",
        locator="n/a",
        quote="Практическая рекомендация.",
        chunk_text="Практическая рекомендация.",
        freshness_status=FreshnessStatus.UNKNOWN,
        is_normative=False,
        requires_verification=True,
    )
    return QueryState(
        session_id=session_id,
        query_id=query_id,
        message_id=uuid4(),
        query_text="Какие требования и практические замечания применимы?",
        session_summary="Контекст сессии",
        recent_messages=[QAMessage(session_id=session_id, role=MessageRole.USER, content="Предыдущий вопрос")],
        evidence_bundle=EvidenceBundle(normative=[normative], open_web=[external]),
    )
