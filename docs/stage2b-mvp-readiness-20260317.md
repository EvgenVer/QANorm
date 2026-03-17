# Stage 2B Readiness Report

Date: 2026-03-17

## Scope

Stage 2B extends the Stage 2A chat MVP with conversational memory inside one browser session, local multi-session UI state, streamed runtime trace, and stable markdown rendering after streamed answers.

Out of scope for this stage:
- authentication;
- persistent chat storage in PostgreSQL;
- restoring sessions after browser reload or a new browser visit.

## Delivered

- session-aware runtime with bounded chat memory and effective-query rewriting;
- support for follow-up, clarification, and "expand previous answer" turns;
- browser-scoped local chat sessions stored in `st.session_state`;
- sidebar actions for `Новая сессия`, switching sessions, and resetting only the active session;
- streamed runtime/debug events directly inside the assistant message;
- final markdown re-render after streaming, so formatting is correct without a second prompt;
- automated isolation coverage for multiple local chats in one UI state.

## Automated Coverage

The Stage 2B implementation is covered by:
- session-memory DTO and normalization tests;
- session-aware runtime and effective-query tests;
- runtime event-stream tests;
- local UI session-state helper tests;
- markdown/rendering helper tests;
- conversational retrieval policy tests;
- integration tests for conversational flow and local session isolation.

## Manual Acceptance

Manual smoke scenarios are documented in:
- [stage2b-streamlit-smoke.md](/d:/my program/QANorm/docs/stage2b-streamlit-smoke.md)

The manual checklist covers:
- question -> follow-up -> clarification;
- partial answer -> expand answer;
- new local session;
- two independent local sessions in one UI;
- streamed debug trace in chat;
- markdown formatting without waiting for the next rerun.

## Readiness Status

Stage 2B is implementation-ready for manual acceptance.

Key constraints that remain intentional:
- chat sessions are ephemeral and disappear after browser reload;
- session state is isolated per Streamlit browser session, not shared globally;
- no database persistence is used for chat history at this stage.

## Recommendation

Proceed with manual acceptance against the Stage 2B smoke checklist. If the checklist passes, the stage can be treated as complete without adding backend persistence or auth-related infrastructure.
