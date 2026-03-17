from __future__ import annotations

from qanorm.stage2a.ui.session_state import (
    create_new_ui_session,
    ensure_ui_sessions,
    get_active_ui_session,
    list_ui_sessions,
    replace_active_ui_session,
    reset_active_ui_session,
    set_active_ui_session,
)


def test_ensure_ui_sessions_creates_first_default_session() -> None:
    state: dict[str, object] = {}

    ensure_ui_sessions(state)

    active_session = get_active_ui_session(state)
    assert active_session.title == "Новая сессия"
    assert len(list_ui_sessions(state)) == 1


def test_create_new_ui_session_adds_and_activates_new_chat() -> None:
    state: dict[str, object] = {}
    ensure_ui_sessions(state)
    first_session = get_active_ui_session(state)

    created = create_new_ui_session(state, title="Плиты")

    assert created.session_id != first_session.session_id
    assert get_active_ui_session(state).session_id == created.session_id
    assert len(list_ui_sessions(state)) == 2


def test_set_active_ui_session_switches_between_existing_sessions() -> None:
    state: dict[str, object] = {}
    ensure_ui_sessions(state)
    first_session = get_active_ui_session(state)
    second_session = create_new_ui_session(state, title="Фундаменты")

    set_active_ui_session(state, first_session.session_id)

    assert get_active_ui_session(state).session_id == first_session.session_id
    set_active_ui_session(state, second_session.session_id)
    assert get_active_ui_session(state).session_id == second_session.session_id


def test_reset_active_ui_session_only_clears_current_session() -> None:
    state: dict[str, object] = {}
    ensure_ui_sessions(state)
    first_session = get_active_ui_session(state)
    first_session = first_session.model_copy(update={"title": "Старая сессия"})
    replace_active_ui_session(state, first_session)
    second_session = create_new_ui_session(state, title="Вторая сессия")

    reset_active_ui_session(state)

    active_session = get_active_ui_session(state)
    sessions = {session.session_id: session for session in list_ui_sessions(state)}

    assert active_session.session_id == second_session.session_id
    assert active_session.title == "Новая сессия"
    assert sessions[first_session.session_id].title == "Старая сессия"
