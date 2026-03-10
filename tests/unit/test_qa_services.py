from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import MessageRole, QueryStatus, SessionChannel, SessionStatus
from qanorm.models import QAMessage, QASession
from qanorm.models.qa_state import EvidenceBundle, QueryState, SubtaskState
from qanorm.services.qa import ContextService, QueryService, SessionService
from qanorm.settings import (
    ProviderSelection,
    ProvidersRuntimeConfig,
    QAFileConfig,
    SearchRuntimeConfig,
    SessionRuntimeConfig,
    TelegramRuntimeConfig,
    WebRuntimeConfig,
)


def _mock_session() -> MagicMock:
    return MagicMock()


def _qa_config() -> QAFileConfig:
    return QAFileConfig(
        session=SessionRuntimeConfig(
            ttl_hours=24,
            summary_trigger_messages=3,
            summary_keep_recent_messages=2,
            max_parallel_queries_per_session=1,
        ),
        providers=ProvidersRuntimeConfig(
            orchestration=ProviderSelection(provider="ollama", model="qwen2.5:7b-instruct"),
            synthesis=ProviderSelection(provider="ollama", model="qwen2.5:14b-instruct"),
            embeddings=ProviderSelection(provider="ollama", model="bge-m3"),
            prompt_catalog_dir=Path("src/qanorm/prompts/templates"),
        ),
        web=WebRuntimeConfig(stream_transport="sse", session_cookie_name="qanorm_session_id"),
        telegram=TelegramRuntimeConfig(enabled=False, use_webhook=False),
        search=SearchRuntimeConfig(open_web_provider="searxng", open_web_max_results=5, trusted_domains=[]),
    )


def test_query_state_refreshes_evidence_and_verification_fingerprints() -> None:
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="How to design this joint?",
        subtasks=[SubtaskState(subtask_id=None, parent_subtask_id=None, subtask_type="normative", description="Find norms")],
        evidence_bundle=EvidenceBundle(),
    )

    evidence_fp = state.refresh_evidence_fingerprint()
    verification_fp = state.refresh_verification_fingerprint(["coverage:warning", "citation:pass"])

    assert len(evidence_fp) == 64
    assert len(verification_fp) == 64


def test_query_state_build_prompt_context_copies_runtime_snapshot() -> None:
    session_id = uuid4()
    message = QAMessage(session_id=session_id, role=MessageRole.USER, content="hello")
    state = QueryState(
        session_id=session_id,
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="hello",
        session_summary="summary",
        recent_messages=[message],
    )

    prompt_context = state.build_prompt_context()

    assert prompt_context.session_id == session_id
    assert prompt_context.session_summary == "summary"
    assert prompt_context.recent_messages == [message]


def test_session_service_create_session_sets_expiration() -> None:
    session = _mock_session()
    service = SessionService(session, qa_config=_qa_config())
    now = datetime(2026, 3, 6, tzinfo=timezone.utc)

    created = service.create_session(channel=SessionChannel.WEB, external_user_id="user-1", now=now)

    assert created.status == SessionStatus.ACTIVE
    assert created.expires_at == datetime(2026, 3, 7, tzinfo=timezone.utc)
    session.add.assert_called_once()
    session.flush.assert_called_once()


def test_session_service_create_session_replaces_previous_web_session() -> None:
    session = _mock_session()
    repository = MagicMock()
    repository.list_by_channel_identifiers.return_value = [QASession(channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)]
    repository.add.side_effect = lambda item: item
    service = SessionService(session, qa_config=_qa_config(), repository=repository)

    created = service.create_session(
        channel=SessionChannel.WEB,
        external_user_id="browser-1",
        replace_existing=True,
        now=datetime(2026, 3, 6, tzinfo=timezone.utc),
    )

    assert created.external_user_id == "browser-1"
    repository.list_by_channel_identifiers.assert_called_once_with(
        SessionChannel.WEB,
        external_user_id="browser-1",
        external_chat_id=None,
    )
    repository.delete.assert_called_once()
    repository.add.assert_called_once()


def test_session_service_resume_session_extends_existing_ttl() -> None:
    session = _mock_session()
    existing = QASession(channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = existing
    service = SessionService(session, qa_config=_qa_config())

    resumed = service.resume_session(channel=SessionChannel.WEB, external_user_id="user-1", now=datetime(2026, 3, 6, tzinfo=timezone.utc))

    assert resumed is existing
    assert resumed.expires_at == datetime(2026, 3, 7, tzinfo=timezone.utc)
    session.flush.assert_called_once()


def test_session_service_cleanup_expired_sessions_deletes_roots() -> None:
    session = _mock_session()
    expired_session = QASession(channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
    session.execute.return_value.scalars.return_value.all.return_value = [expired_session]
    service = SessionService(session, qa_config=_qa_config())

    removed = service.cleanup_expired_sessions(now=datetime(2026, 3, 6, tzinfo=timezone.utc))

    assert removed == 1
    session.delete.assert_called_once_with(expired_session)
    session.flush.assert_called_once()


def test_context_service_load_prompt_context_compacts_old_history() -> None:
    session = _mock_session()
    qa_session = QASession(channel=SessionChannel.WEB, status=SessionStatus.ACTIVE, session_summary="summary")
    messages = [
        QAMessage(session_id=uuid4(), role=MessageRole.USER, content="m1"),
        QAMessage(session_id=uuid4(), role=MessageRole.ASSISTANT, content="m2"),
        QAMessage(session_id=uuid4(), role=MessageRole.USER, content="m3"),
    ]
    session.get.return_value = qa_session
    session.execute.return_value.scalars.return_value.all.return_value = messages
    service = ContextService(session, qa_config=_qa_config())

    context = service.load_prompt_context(session_id=uuid4(), query_text="next question", query_id=uuid4())

    assert context is not None
    assert context.session_summary == "summary"
    assert [message.content for message in context.recent_messages] == ["m2", "m3"]


def test_context_service_compact_history_returns_full_history_below_threshold() -> None:
    service = ContextService(_mock_session(), qa_config=_qa_config())
    messages = [QAMessage(session_id=uuid4(), role=MessageRole.USER, content="m1")]

    summarized, recent = service.compact_history(messages)

    assert summarized == []
    assert recent == messages


def test_query_service_create_query_from_message_binds_message_and_query() -> None:
    session = _mock_session()
    service = QueryService(session)
    session_id = uuid4()

    message, query = service.create_query_from_message(session_id=session_id, content="Need a clause reference", query_type="normative")

    assert message.session_id == session_id
    assert message.role == MessageRole.USER
    assert query.session_id == session_id
    assert query.message_id == message.id
    assert query.query_text == "Need a clause reference"
    assert query.status == QueryStatus.PENDING
    assert session.add.call_count == 2
    assert session.flush.call_count == 2
