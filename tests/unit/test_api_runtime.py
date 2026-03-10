from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from qanorm.api.app import create_app
from qanorm.api.dependencies import get_arq_redis, get_db_session, get_redis_client
from qanorm.db.types import MessageRole, SessionChannel, SessionStatus
from qanorm.models import QAMessage, QASession


class _FakeRedis:
    async def ping(self) -> bool:
        return True

    async def publish(self, channel: str, payload: str) -> int:
        self.last_publish = (channel, payload)
        return 1

    def pubsub(self):
        return _FakePubSub()


class _FakePubSub:
    async def subscribe(self, channel: str) -> None:
        self.channel = channel

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
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


def _build_client(db: MagicMock | None = None) -> tuple[TestClient, MagicMock, _FakeRedis, _FakeArqRedis]:
    app = create_app()
    db_session = db or MagicMock()
    fake_redis = _FakeRedis()
    fake_arq = _FakeArqRedis()

    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    app.dependency_overrides[get_arq_redis] = lambda: fake_arq
    return TestClient(app), db_session, fake_redis, fake_arq


def test_create_app_registers_expected_routes() -> None:
    app = create_app()

    paths = {route.path for route in app.routes}

    assert "/health/live" in paths
    assert "/health/ready" in paths
    assert "/sessions" in paths
    assert "/sessions/{session_id}/queries" in paths
    assert "/queries/{query_id}" in paths
    assert "/queries/{query_id}/events" in paths
    assert "/metrics" in paths


def test_create_app_enables_cors_for_web_origin() -> None:
    client, _, _, _ = _build_client()

    response = client.options(
        "/sessions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_live_endpoint_returns_ok() -> None:
    client, _, _, _ = _build_client()

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_metrics_endpoint_exports_payload() -> None:
    client, _, _, _ = _build_client()

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_ready_endpoint_checks_database_redis_and_arq() -> None:
    client, db_session, _, _ = _build_client()

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {
        "database": True,
        "redis": True,
        "arq_publish": True,
    }
    db_session.execute.assert_called_once()


def test_create_session_endpoint_returns_session_payload() -> None:
    client, _, _, _ = _build_client()

    response = client.post(
        "/sessions",
        json={"channel": "web", "external_user_id": "user-1", "replace_existing": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["channel"] == "web"
    assert payload["external_user_id"] == "user-1"
    assert payload["status"] == "active"


def test_create_session_endpoint_rejects_web_session_without_browser_identity() -> None:
    client, _, _, _ = _build_client()

    response = client.post("/sessions", json={"channel": "web"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "web_identity_required"


def test_get_session_endpoint_returns_404_for_unknown_session() -> None:
    client, db_session, _, _ = _build_client()
    db_session.get.return_value = None

    response = client.get(f"/sessions/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_create_query_endpoint_binds_session_message_and_query() -> None:
    client, db_session, fake_redis, fake_arq = _build_client()
    session_id = uuid4()
    db_session.get.return_value = QASession(id=session_id, channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)

    response = client.post(
        f"/sessions/{session_id}/queries",
        json={"content": "Need clause reference", "query_type": "normative"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == str(session_id)
    assert payload["query_id"] is not None
    assert payload["role"] == "user"
    assert fake_redis.last_publish[0].endswith(":events")
    assert fake_arq.last_enqueue is not None
    assert fake_arq.last_enqueue[0][0] == "process_query_job"


def test_list_messages_endpoint_serializes_session_history() -> None:
    client, db_session, _, _ = _build_client()
    session_id = uuid4()
    db_session.get.return_value = QASession(id=session_id, channel=SessionChannel.WEB, status=SessionStatus.ACTIVE)
    db_session.execute.return_value.scalars.return_value.all.return_value = [
        QAMessage(id=uuid4(), session_id=session_id, role=MessageRole.USER, content="hello"),
    ]

    response = client.get(f"/sessions/{session_id}/messages")

    assert response.status_code == 200
    assert response.json()[0]["content"] == "hello"
