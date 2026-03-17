"""Helpers for browser-scoped multi-session Stage 2B UI state."""

from __future__ import annotations

from collections.abc import MutableMapping
from uuid import uuid4

from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import Stage2AChatSessionDTO
from qanorm.stage2a.session_memory import create_chat_session


_SESSIONS_KEY = "stage2a_sessions"
_ACTIVE_SESSION_ID_KEY = "stage2a_active_session_id"


def ensure_ui_sessions(
    state: MutableMapping[str, object],
    *,
    config: Stage2AConfig | None = None,
) -> None:
    """Ensure the browser-scoped session store has at least one active chat session."""

    cfg = config or get_stage2a_config()
    sessions = state.get(_SESSIONS_KEY)
    if not isinstance(sessions, dict):
        sessions = {}
        state[_SESSIONS_KEY] = sessions

    active_session_id = state.get(_ACTIVE_SESSION_ID_KEY)
    if isinstance(active_session_id, str) and active_session_id in sessions:
        return

    if sessions:
        state[_ACTIVE_SESSION_ID_KEY] = next(iter(sessions))
        return

    session = create_chat_session(_new_session_id(), config=cfg)
    sessions[session.session_id] = session
    state[_ACTIVE_SESSION_ID_KEY] = session.session_id


def create_new_ui_session(
    state: MutableMapping[str, object],
    *,
    title: str | None = None,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Create one new local chat session and switch the UI to it."""

    cfg = config or get_stage2a_config()
    ensure_ui_sessions(state, config=cfg)
    sessions = _get_sessions(state)
    session = create_chat_session(_new_session_id(), title=title, config=cfg)
    sessions[session.session_id] = session
    state[_ACTIVE_SESSION_ID_KEY] = session.session_id
    return session


def set_active_ui_session(state: MutableMapping[str, object], session_id: str) -> None:
    """Switch the active local chat session."""

    sessions = _get_sessions(state)
    if session_id not in sessions:
        raise KeyError(f"Unknown session_id: {session_id}")
    state[_ACTIVE_SESSION_ID_KEY] = session_id


def get_active_ui_session(state: MutableMapping[str, object]) -> Stage2AChatSessionDTO:
    """Return the current active local chat session."""

    ensure_ui_sessions(state)
    sessions = _get_sessions(state)
    session_id = state[_ACTIVE_SESSION_ID_KEY]
    return sessions[str(session_id)]


def replace_active_ui_session(state: MutableMapping[str, object], session: Stage2AChatSessionDTO) -> None:
    """Persist the updated state of the current chat session."""

    sessions = _get_sessions(state)
    sessions[session.session_id] = session
    state[_ACTIVE_SESSION_ID_KEY] = session.session_id


def reset_active_ui_session(
    state: MutableMapping[str, object],
    *,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Reset only the active session and keep all other sessions untouched."""

    cfg = config or get_stage2a_config()
    current = get_active_ui_session(state)
    reset_session = create_chat_session(current.session_id, title="Новая сессия", config=cfg)
    replace_active_ui_session(state, reset_session)
    return reset_session


def list_ui_sessions(state: MutableMapping[str, object]) -> list[Stage2AChatSessionDTO]:
    """Return all local chat sessions in insertion order."""

    ensure_ui_sessions(state)
    return list(_get_sessions(state).values())


def _get_sessions(state: MutableMapping[str, object]) -> dict[str, Stage2AChatSessionDTO]:
    sessions = state.get(_SESSIONS_KEY)
    if not isinstance(sessions, dict):
        raise KeyError("stage2a session store is not initialized")
    return sessions


def _new_session_id() -> str:
    return f"session-{uuid4().hex[:12]}"
