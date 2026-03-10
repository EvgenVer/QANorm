from __future__ import annotations

import asyncio
from uuid import uuid4

from qanorm.agents.answer_synthesizer import AnswerSynthesizer
from qanorm.db.types import CoverageStatus, EvidenceSourceKind, FreshnessStatus, MessageRole, QueryStatus
from qanorm.models import Document, QAEvidence, QAMessage, QAQuery
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.prompts.registry import create_prompt_registry
from qanorm.providers.base import ChatModelProvider, ChatRequest, ChatResponse, ProviderCapabilities, ProviderName
from tests.unit.test_provider_registry import _runtime_config


class _FakeChatProvider(ChatModelProvider):
    """Return deterministic payloads so synthesis smoke stays stable."""

    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(chat=True)

    def __init__(self, content: str) -> None:
        self.model = "answer-integration-test"
        self._content = content

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(provider=self.provider_name, model=request.model, content=self._content)


class _AnswerRepositoryStub:
    """Capture the persisted structured answer row."""

    def __init__(self) -> None:
        self.saved = None

    def get_by_query(self, query_id):
        return None

    def save(self, answer):
        self.saved = answer
        return answer


class _MessageRepositoryStub:
    """Capture the persisted assistant message."""

    def __init__(self) -> None:
        self.saved = None

    def add(self, message):
        self.saved = message
        return message


class _QueryRepositoryStub:
    """Apply status transitions directly to the in-memory query row."""

    def update_state(self, query, *, status, **kwargs):
        query.status = status
        return query


def test_404_integration_answer_smoke_with_mixed_normative_and_external_evidence() -> None:
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
    query = QAQuery(id=uuid4(), session_id=uuid4(), message_id=uuid4(), query_text="Which code requirement applies and what external practice is commonly used?", status=QueryStatus.SYNTHESIZING)
    state = _build_query_state(query=query, include_external=True, query_text=query.query_text)

    structured = asyncio.run(synthesizer.synthesize(state))
    saved_answer, assistant_message = synthesizer.persist_answer(query=query, answer=structured)

    assert structured.sections[0].source_kind == EvidenceSourceKind.NORMATIVE
    assert structured.has_external_sources is True
    assert any(section.source_kind is EvidenceSourceKind.OPEN_WEB for section in structured.sections)
    assert structured.coverage_status in {CoverageStatus.COMPLETE, CoverageStatus.PARTIAL}
    assert saved_answer.has_external_sources is True
    assert assistant_message.role == MessageRole.ASSISTANT
    assert assistant_message.metadata_json["has_external_sources"] is True
    assert query.status == QueryStatus.COMPLETED


def test_405_integration_answer_smoke_marks_incomplete_coverage() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider("invalid-json"),
        answer_repository=_AnswerRepositoryStub(),
        message_repository=_MessageRepositoryStub(),
        query_repository=_QueryRepositoryStub(),
    )
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="Compare fire safety, evacuation, and smoke extraction requirements for the same facility.",
        status=QueryStatus.SYNTHESIZING,
    )
    state = _build_query_state(query=query, include_external=False, query_text=query.query_text)

    structured = asyncio.run(
        synthesizer.synthesize(
            state,
            limitations=["Only one normative evidence block was available for this smoke run."],
        )
    )

    assert structured.coverage_status == CoverageStatus.PARTIAL
    assert structured.has_external_sources is False
    assert any("coverage" in warning.lower() or "огранич" in warning.lower() for warning in structured.warnings)
    assert any(section.source_kind is EvidenceSourceKind.NORMATIVE for section in structured.sections)


def _build_query_state(*, query: QAQuery, include_external: bool, query_text: str) -> QueryState:
    document = Document(id=uuid4(), normalized_code="SP 35.13330.2011", display_code="SP 35.13330.2011", title="Bridges and culverts")
    normative = QAEvidence(
        query_id=query.id,
        source_kind=EvidenceSourceKind.NORMATIVE,
        document_id=document.id,
        document_version_id=uuid4(),
        locator="5.24",
        locator_end="5.24",
        quote="Bridge and culvert loads shall be evaluated using the active design provisions.",
        chunk_text="Bridge and culvert loads shall be evaluated using the active design provisions.",
        freshness_status=FreshnessStatus.FRESH,
        is_normative=True,
        requires_verification=False,
    )
    normative.document = document

    open_web: list[QAEvidence] = []
    if include_external:
        open_web.append(
            QAEvidence(
                query_id=query.id,
                source_kind=EvidenceSourceKind.OPEN_WEB,
                source_domain="example.com",
                locator="n/a",
                quote="External engineering practice still needs user verification.",
                chunk_text="External engineering practice still needs user verification.",
                freshness_status=FreshnessStatus.UNKNOWN,
                is_normative=False,
                requires_verification=True,
            )
        )

    return QueryState(
        session_id=query.session_id,
        query_id=query.id,
        message_id=query.message_id,
        query_text=query_text,
        session_summary="Session summary",
        recent_messages=[QAMessage(session_id=query.session_id, role=MessageRole.USER, content=query_text)],
        evidence_bundle=EvidenceBundle(normative=[normative], open_web=open_web),
    )
