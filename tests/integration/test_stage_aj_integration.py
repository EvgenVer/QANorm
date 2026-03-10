from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from qanorm.agents.orchestrator import QueryOrchestrator
from qanorm.agents.answer_synthesizer import AnswerSynthesizer
from qanorm.db.types import CoverageStatus, EvidenceSourceKind, FreshnessCheckStatus, FreshnessStatus, MessageRole, QueryStatus, SessionChannel, SessionStatus
from qanorm.models import AuditEvent, Document, FreshnessCheck, QAEvidence, QAMessage, QAQuery, QASession
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.prompts.registry import create_prompt_registry
from qanorm.providers.base import ChatModelProvider, ChatRequest, ChatResponse, ProviderCapabilities, ProviderName
from qanorm.services.qa.freshness_service import annotate_answer_with_freshness
from qanorm.tools.base import Tool, ToolDefinition, ToolRegistry
from tests.unit.test_provider_registry import _runtime_config


class _FakeChatProvider(ChatModelProvider):
    """Return invalid JSON so the integration path uses evidence-backed fallback sections."""

    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(chat=True)

    def __init__(self) -> None:
        self.model = "freshness-integration-test"

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(provider=self.provider_name, model=request.model, content="invalid-json")


def test_406_integration_answer_includes_stale_warning_and_edition_annotations() -> None:
    synthesizer = AnswerSynthesizer(
        session=None,  # type: ignore[arg-type]
        runtime_config=_runtime_config(),
        prompt_registry=create_prompt_registry(_runtime_config()),
        provider=_FakeChatProvider(),
    )
    stale_check = FreshnessCheck(
        id=uuid4(),
        query_id=uuid4(),
        document_id=uuid4(),
        local_edition_label="2023",
        remote_edition_label="2024",
        check_status=FreshnessCheckStatus.STALE,
        details_json={"document_code": "SP 35.13330.2011"},
    )

    structured = asyncio.run(synthesizer.synthesize(_build_query_state()))
    annotated = annotate_answer_with_freshness(structured, checks=[stale_check])

    assert annotated.coverage_status in {CoverageStatus.COMPLETE, CoverageStatus.PARTIAL}
    assert annotated.has_stale_sources is True
    assert "### Freshness" in annotated.markdown
    assert "SP 35.13330.2011" in annotated.markdown
    assert "2023" in annotated.markdown
    assert "2024" in annotated.markdown


def test_407_integration_orchestrator_connects_non_blocking_freshness_branch() -> None:
    query = QAQuery(
        id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        query_text="What does the bridges code require?",
        status=QueryStatus.RETRIEVING,
    )
    query.requires_freshness_check = True
    session = _RecordingSession()
    audit_repository = _InMemoryAuditRepository()
    scheduled_check = FreshnessCheck(id=uuid4(), query_id=query.id, document_id=uuid4(), check_status=FreshnessCheckStatus.PENDING)
    evidence = QAEvidence(query_id=query.id, source_kind=EvidenceSourceKind.NORMATIVE, document_id=scheduled_check.document_id)
    scheduled_ids: list[str] = []

    async def _scheduler(check: FreshnessCheck):
        scheduled_ids.append(str(check.id))
        return {"status": "queued"}

    orchestrator = QueryOrchestrator(
        session,
        tool_registry=_build_tool_registry(),
        runtime_config=_runtime_config(),
        context_service=_ContextServiceStub(query),
        query_repository=_InMemoryQueryRepository(query),
        subtask_repository=_InMemorySubtaskRepository(),
        audit_repository=audit_repository,
    )

    async def _run():
        from unittest.mock import patch

        with patch(
            "qanorm.agents.orchestrator.runtime.connect_freshness_branch",
            return_value=[scheduled_check],
        ):
            return await orchestrator.schedule_freshness_branch(
                query_id=query.id,
                evidence_rows=[evidence],
                scheduler=_scheduler,
            )

    result = asyncio.run(_run())

    assert result == [scheduled_check]
    assert any(event.event_type == "freshness_branch_connected" for event in audit_repository.rows)


class _DummyTool(Tool):
    """Advertise one scope to the registry without executing anything."""

    def __init__(self, name: str, scope: str) -> None:
        self.definition = ToolDefinition(name=name, scope=scope, description="integration test tool")

    async def execute(self, context, payload):  # pragma: no cover - not exercised here.
        raise AssertionError("Tool execution is outside freshness integration coverage")


@dataclass
class _InMemoryQueryRepository:
    """Return one stored query row by id."""

    query: QAQuery

    def get(self, query_id):
        assert query_id == self.query.id
        return self.query


@dataclass
class _InMemorySubtaskRepository:
    """Unused here but required by the orchestrator constructor."""

    rows: list = field(default_factory=list)

    def add(self, subtask):
        self.rows.append(subtask)
        return subtask


@dataclass
class _InMemoryAuditRepository:
    """Capture orchestrator transition events."""

    rows: list = field(default_factory=list)

    def add(self, event):
        self.rows.append(event)
        return event


class _ContextServiceStub:
    """Return deterministic prompt context for the orchestrator."""

    def __init__(self, query: QAQuery) -> None:
        self._session = QASession(id=query.session_id, channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
        self._message = QAMessage(id=query.message_id, session_id=query.session_id, role=MessageRole.USER, content=query.query_text)

    def load_prompt_context(self, session_id, query_text, query_id):
        return QueryState(
            session_id=session_id,
            query_id=query_id,
            message_id=self._message.id,
            query_text=query_text,
            recent_messages=[self._message],
        ).build_prompt_context()


class _RecordingSession:
    """Capture ORM writes performed by helper repositories."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)

    def flush(self) -> None:
        return None


def _build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_DummyTool("normative_search", "normative"))
    registry.register(_DummyTool("freshness_check", "freshness"))
    registry.register(_DummyTool("trusted_search", "trusted_web"))
    registry.register(_DummyTool("open_web_search", "open_web"))
    return registry


def _build_query_state() -> QueryState:
    session_id = uuid4()
    query_id = uuid4()
    document = Document(id=uuid4(), normalized_code="SP 35.13330.2011", display_code="SP 35.13330.2011", title="Bridges and culverts")
    normative = QAEvidence(
        query_id=query_id,
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
    return QueryState(
        session_id=session_id,
        query_id=query_id,
        message_id=uuid4(),
        query_text="What does the bridges and culverts code require?",
        recent_messages=[QAMessage(session_id=session_id, role=MessageRole.USER, content="What does the code require?")],
        evidence_bundle=EvidenceBundle(normative=[normative]),
    )
