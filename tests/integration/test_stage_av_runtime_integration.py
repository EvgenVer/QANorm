from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from qanorm.db.session import session_scope
from qanorm.db.types import EvidenceSourceKind, MessageRole, QueryStatus, SessionChannel, SessionStatus, StatusNormalized
from qanorm.fetchers.trusted_sources import TrustedSourcePage, TrustedSourceSearchCandidate
from qanorm.integrations.telegram.bot import ensure_telegram_session, submit_telegram_query
from qanorm.models import Document, DocumentVersion, QAEvidence, QAMessage, QAQuery, QASession
from qanorm.providers import create_provider_registry
from qanorm.providers.base import create_role_bound_providers
from qanorm.services.qa.freshness_service import connect_freshness_branch
from qanorm.services.qa.query_service import QueryService
from qanorm.services.qa.session_service import SessionService
from qanorm.services.qa.trusted_sources_service import search_trusted_sources
from qanorm.settings import ProviderSelection, TrustedSourceAdapterConfig
from tests.unit.test_provider_registry import _runtime_config


class _FakeRedis:
    def __init__(self, *, pubsub_messages: list[dict[str, str]] | None = None) -> None:
        self.last_publish = None
        self._pubsub_messages = list(pubsub_messages or [])

    async def ping(self) -> bool:
        return True

    async def publish(self, channel: str, payload: str) -> int:
        self.last_publish = (channel, payload)
        return 1

    def pubsub(self):
        return _FakePubSub(list(self._pubsub_messages))


class _FakePubSub:
    def __init__(self, messages: list[dict[str, str]]) -> None:
        self._messages = messages

    async def subscribe(self, channel: str) -> None:
        self.channel = channel

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        if self._messages:
            return self._messages.pop(0)
        return None

    async def unsubscribe(self, channel: str) -> None:
        self.channel = channel

    async def aclose(self) -> None:
        return None


class _FakeArqRedis:
    def __init__(self) -> None:
        self.last_enqueue = None

    async def enqueue_job(self, *args, **kwargs):
        self.last_enqueue = (args, kwargs)
        return object()


def _build_client(*, fake_redis: _FakeRedis | None = None, fake_arq: _FakeArqRedis | None = None) -> tuple[TestClient, _FakeRedis, _FakeArqRedis]:
    from qanorm.api.app import create_app
    from qanorm.api.dependencies import get_arq_redis, get_redis_client

    app = create_app()
    redis = fake_redis or _FakeRedis()
    arq = fake_arq or _FakeArqRedis()
    app.dependency_overrides[get_redis_client] = lambda: redis
    app.dependency_overrides[get_arq_redis] = lambda: arq
    return TestClient(app), redis, arq


def test_955_integration_session_create_and_resume_on_live_db() -> None:
    external_user_id = f"web-user-{uuid4()}"

    with session_scope() as session:
        service = SessionService(session)
        created = service.create_session(channel=SessionChannel.WEB, external_user_id=external_user_id, replace_existing=True)
        resumed = service.resume_session(channel=SessionChannel.WEB, external_user_id=external_user_id)
        created_id = created.id
        resumed_id = resumed.id if resumed is not None else None
        resumed_status = resumed.status if resumed is not None else None

    assert resumed is not None
    assert resumed_id == created_id
    assert resumed_status == SessionStatus.ACTIVE


def test_956_integration_message_history_persists_and_lists_over_api() -> None:
    external_user_id = f"history-user-{uuid4()}"
    with session_scope() as session:
        qa_session = SessionService(session).create_session(
            channel=SessionChannel.WEB,
            external_user_id=external_user_id,
            replace_existing=True,
        )
        query_service = QueryService(session)
        query_service.create_query_from_message(session_id=qa_session.id, content="First question", query_type="normative")
        query_service.create_query_from_message(session_id=qa_session.id, content="Second question", query_type="normative")
        session_id = qa_session.id

    client, _, _ = _build_client()
    response = client.get(f"/sessions/{session_id}/messages")

    assert response.status_code == 200
    payload = response.json()
    assert [item["content"] for item in payload[-2:]] == ["First question", "Second question"]


def test_957_integration_post_queries_endpoint_persists_query_and_enqueues_worker() -> None:
    external_user_id = f"query-user-{uuid4()}"
    with session_scope() as session:
        qa_session = SessionService(session).create_session(
            channel=SessionChannel.WEB,
            external_user_id=external_user_id,
            replace_existing=True,
        )
        session_id = qa_session.id

    client, fake_redis, fake_arq = _build_client()
    response = client.post(
        f"/sessions/{session_id}/queries",
        json={"content": "Need clause reference", "query_type": "normative"},
    )

    assert response.status_code == 200
    payload = response.json()
    query_id = payload["query_id"]
    assert payload["session_id"] == str(session_id)
    assert fake_redis.last_publish is not None
    assert fake_arq.last_enqueue is not None
    with session_scope() as session:
        stored = session.get(QAQuery, query_id)
        assert stored is not None
        assert stored.session_id == session_id


def test_958_integration_sse_streaming_contract_emits_bootstrap_and_payload() -> None:
    from qanorm.api.routes.chat import stream_query_events

    query_id = uuid4()
    fake_redis = _FakeRedis(
        pubsub_messages=[
            {
                "data": json.dumps(
                    {
                        "event": "answer_completed",
                        "query_id": str(query_id),
                        "data": {"partial_markdown": "done"},
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            }
        ]
    )

    class _RequestStub:
        """Disconnect after the first payload event so the SSE iterator terminates deterministically."""

        def __init__(self) -> None:
            self._checks = 0

        async def is_disconnected(self) -> bool:
            self._checks += 1
            return self._checks > 1

    async def _collect_chunks() -> list[str]:
        response = await stream_query_events(query_id=query_id, request=_RequestStub(), redis=fake_redis)
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            if text.strip():
                chunks.append(text)
        return chunks

    chunks = asyncio.run(_collect_chunks())

    assert chunks
    assert any("'event': 'stream_ready'" in chunk for chunk in chunks)
    assert any("'event': 'answer_completed'" in chunk for chunk in chunks)
    assert any('"partial_markdown": "done"' in chunk for chunk in chunks)


def test_959_integration_provider_switching_follows_runtime_config() -> None:
    runtime_config = _runtime_config()
    runtime_config.qa.providers.orchestration = ProviderSelection(provider="lmstudio", model="orchestrator-model")
    runtime_config.qa.providers.synthesis = ProviderSelection(provider="ollama", model="synth-model")
    runtime_config.qa.providers.embeddings = ProviderSelection(provider="lmstudio", model="embedding-model")

    bindings = create_role_bound_providers(registry=create_provider_registry(), runtime_config=runtime_config)

    assert bindings.orchestration.provider_name == "lmstudio"
    assert bindings.synthesis.provider_name == "ollama"
    assert bindings.embeddings.provider_name == "lmstudio"


def test_961_integration_freshness_branch_is_non_blocking() -> None:
    document_code = f"SP-{uuid4()}"

    with session_scope() as session:
        qa_session = QASession(id=uuid4(), channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
        message = QAMessage(id=uuid4(), session_id=qa_session.id, role=MessageRole.USER, content="Need freshness check")
        query = QAQuery(
            id=uuid4(),
            session_id=qa_session.id,
            message_id=message.id,
            query_text="Need freshness check",
            status=QueryStatus.RETRIEVING,
            requires_freshness_check=True,
        )
        document = Document(
            id=uuid4(),
            normalized_code=document_code,
            display_code=document_code,
            title=document_code,
            status_normalized=StatusNormalized.ACTIVE,
        )
        version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=True, edition_label="2024")
        evidence = QAEvidence(
            query_id=query.id,
            source_kind=EvidenceSourceKind.NORMATIVE,
            document_id=document.id,
            document_version_id=version.id,
            quote="Requirement",
            chunk_text="Requirement",
            is_normative=True,
            requires_verification=False,
        )
        session.add(qa_session)
        session.add(message)
        session.add(query)
        session.add(document)
        session.add(version)
        session.flush()
        document.current_version_id = version.id
        session.add(evidence)
        session.flush()

        scheduled_ids: list[str] = []
        result = asyncio.run(
            connect_freshness_branch(
                session,
                query=query,
                evidence_rows=[evidence],
                scheduler=lambda check: scheduled_ids.append(str(check.id)),
            )
        )

        assert result
        assert scheduled_ids == [str(result[0].id)]
        assert query.status == QueryStatus.RETRIEVING


def test_962_integration_trusted_source_fallback_reuses_ttl_cache(monkeypatch) -> None:
    search_calls = {"search": 0, "page": 0, "fragment": 0}
    query_text = f"ventilation smoke control {uuid4()}"
    source_url = f"https://example.com/{uuid4()}"

    async def _fake_search(*, query_text, source, provider):
        search_calls["search"] += 1
        return [
            TrustedSourceSearchCandidate(
                source_id=source.source_id or source.domain,
                source_domain=source.domain,
                source_language=source.language,
                url=source_url,
                title="Guide",
                snippet="Trusted engineering guidance",
                score=0.9,
                metadata={"engine": "stub"},
            )
        ]

    def _fake_page(url, *, source):
        search_calls["page"] += 1
        return TrustedSourcePage(
            source_id=source.source_id or source.domain,
            url=url,
            title="Guide",
            text="Trusted engineering guidance",
            source_domain=source.domain,
            source_language="ru",
            content_hash=f"hash:{source_url}",
            metadata={"source": "stub"},
        )

    def _fake_fragments(text, *, max_chars=1200):
        search_calls["fragment"] += 1
        return ["Trusted engineering guidance for ventilation and smoke control."]

    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.search_trusted_source_urls", _fake_search)
    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.fetch_trusted_source_page", _fake_page)
    monkeypatch.setattr("qanorm.services.qa.trusted_sources_service.fragment_trusted_source_text", _fake_fragments)

    source = TrustedSourceAdapterConfig(domain="example.com")
    router = type("_Router", (), {"select_sources": lambda self, query_text, allowed_domains: [source]})()

    with session_scope() as session:
        first_hits = asyncio.run(
            search_trusted_sources(
                session,
                query_id=None,
                subtask_id=None,
                query_text=query_text,
                allowed_domains=["example.com"],
                router=router,
                limit=2,
            )
        )
        second_hits = asyncio.run(
            search_trusted_sources(
                session,
                query_id=None,
                subtask_id=None,
                query_text=query_text,
                allowed_domains=["example.com"],
                router=router,
                limit=2,
            )
        )

    assert first_hits
    assert second_hits[0].cache_hit is True
    assert search_calls == {"search": 1, "page": 1, "fragment": 1}


def test_965_integration_parallel_web_session_lock_blocks_same_session() -> None:
    session_id = uuid4()

    async def _exercise() -> None:
        from qanorm.workers.stage2 import SessionLockError, create_redis_client, session_lock

        redis = create_redis_client()
        try:
            async with session_lock(redis, session_id):
                with pytest.raises(SessionLockError):
                    async with session_lock(redis, session_id):
                        pass
        finally:
            await redis.aclose()

    asyncio.run(_exercise())


def test_966_integration_web_and_telegram_sessions_stay_isolated() -> None:
    external_user_id = f"iso-user-{uuid4()}"
    with session_scope() as session:
        web_session = SessionService(session).create_session(
            channel=SessionChannel.WEB,
            external_user_id=external_user_id,
            replace_existing=True,
        )
        _, web_query = QueryService(session).create_query_from_message(
            session_id=web_session.id,
            content="Web question",
            query_type="normative",
        )
        web_session_id = web_session.id
        web_query_id = web_query.id

    telegram_binding = ensure_telegram_session(chat_id=int(uuid4().int % 10_000_000), user_id=int(uuid4().int % 10_000_000))
    telegram_query_id = submit_telegram_query(session_id=telegram_binding.session_id, text="Telegram question")

    with session_scope() as session:
        web_query = session.get(QAQuery, web_query_id)
        telegram_query = session.get(QAQuery, telegram_query_id)
        assert web_query is not None
        assert telegram_query is not None
        assert web_query.session_id == web_session_id
        assert telegram_query.session_id != web_query.session_id


def test_969_integration_multiple_session_locks_can_progress_concurrently() -> None:
    session_ids = [uuid4() for _ in range(4)]

    async def _worker(redis, session_id):
        from qanorm.workers.stage2 import session_lock

        async with session_lock(redis, session_id):
            await asyncio.sleep(0.01)
            return str(session_id)

    async def _exercise() -> list[str]:
        from qanorm.workers.stage2 import create_redis_client

        redis = create_redis_client()
        try:
            return await asyncio.gather(*[_worker(redis, session_id) for session_id in session_ids])
        finally:
            await redis.aclose()

    result = asyncio.run(_exercise())

    assert sorted(result) == sorted(str(item) for item in session_ids)
