from __future__ import annotations

import asyncio
from uuid import uuid4

from qanorm.agents.answer_synthesizer import AnswerSynthesizer
from qanorm.agents.planner import QueryIntent
from qanorm.db.types import AnswerMode, EvidenceSourceKind, FreshnessStatus, MessageRole
from qanorm.models import Document, QAEvidence, QAMessage
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.prompts.registry import create_prompt_registry
from qanorm.services.qa.verification_service import VerificationService
from tests.unit.test_provider_registry import _runtime_config


class _FakeChatProvider:
    provider_name = "ollama"
    capabilities = type("_Caps", (), {"chat": True})()

    def __init__(self, content: str = "invalid-json") -> None:
        self.model = "stage-at-test"
        self._content = content

    async def generate(self, request):
        return type("_Response", (), {"content": self._content})()


class _ReportRepository:
    def add(self, report):
        return report


def test_946_integration_direct_answer_mode_uses_primary_evidence() -> None:
    state = _build_primary_normative_state()
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider(),
    )
    verification = VerificationService(
        _SessionStub(),
        runtime_config=_runtime_config(),
        provider=_FakeChatProvider('{"findings": []}'),
        report_repository=_ReportRepository(),
    )

    answer = asyncio.run(synthesizer.synthesize(state))
    verified_answer, outcome = asyncio.run(
        verification.run_bounded_repair_loop(
            state=state,
            initial_answer=answer,
            repair_callback=lambda current_answer, findings: synthesizer.repair_answer(
                state,
                current_answer=current_answer,
                findings=findings,
            ),
        )
    )

    assert verified_answer.answer_mode == AnswerMode.DIRECT_ANSWER
    assert outcome.has_blocking_failures is False


def test_947_integration_clarify_path_returns_clarify_mode() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("unused"),
    )
    verification = VerificationService(
        _SessionStub(),
        runtime_config=_runtime_config(),
        provider=_FakeChatProvider('{"findings": []}'),
        report_repository=_ReportRepository(),
    )
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="СП 63",
        intent=QueryIntent.CLARIFY.value,
        clarification_required=True,
        clarification_question="Уточните пункт СП 63.",
    )

    answer = asyncio.run(synthesizer.synthesize(state))
    outcome = asyncio.run(verification.verify_answer(state=state, answer=answer))

    assert answer.answer_mode == AnswerMode.CLARIFY
    assert outcome.has_blocking_failures is False


def test_948_integration_decline_path_returns_no_retrieval_mode() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("unused"),
    )
    verification = VerificationService(
        _SessionStub(),
        runtime_config=_runtime_config(),
        provider=_FakeChatProvider('{"findings": []}'),
        report_repository=_ReportRepository(),
    )
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="Привет",
        intent=QueryIntent.NO_RETRIEVAL.value,
    )

    answer = asyncio.run(synthesizer.synthesize(state))
    outcome = asyncio.run(verification.verify_answer(state=state, answer=answer))

    assert answer.answer_mode == AnswerMode.DECLINE
    assert outcome.has_blocking_failures is False


class _SessionStub:
    def add(self, item):
        return None

    def flush(self):
        return None


def _build_primary_normative_state() -> QueryState:
    session_id = uuid4()
    query_id = uuid4()
    document = Document(id=uuid4(), normalized_code="СП 35.13330.2011", display_code="СП 35.13330.2011", title="Мосты и трубы")
    evidence = QAEvidence(
        query_id=query_id,
        source_kind=EvidenceSourceKind.NORMATIVE,
        document_id=document.id,
        document_version_id=uuid4(),
        locator="5.24",
        quote="Нагрузки на мосты и трубы следует определять по действующим расчетным положениям.",
        chunk_text="Нагрузки на мосты и трубы следует определять по действующим расчетным положениям.",
        freshness_status=FreshnessStatus.FRESH,
        is_normative=True,
        requires_verification=False,
        selection_metadata={"selection_tier": "primary"},
    )
    evidence.document = document
    return QueryState(
        session_id=session_id,
        query_id=query_id,
        message_id=uuid4(),
        query_text="Что требует п. 5.24 СП 35 для нагрузок на мосты?",
        recent_messages=[QAMessage(session_id=session_id, role=MessageRole.USER, content="Что требует п. 5.24 СП 35 для нагрузок на мосты?")],
        evidence_bundle=EvidenceBundle(normative=[evidence]),
    )
