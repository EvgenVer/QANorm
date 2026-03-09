"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { APIClient } from "../lib/api";
import type { ActivityEvent, Message, QueryDetail, Session } from "../lib/types";

type ChatShellProps = {
  apiBaseUrl: string;
};

type PendingQueryState = {
  queryId: string;
  partialAnswer: string;
};

const SOURCE_KIND_LABELS: Record<string, string> = {
  normative: "Normative",
  trusted_web: "Trusted Web",
  open_web: "Open Web",
};

const FRESHNESS_LABELS: Record<string, string> = {
  fresh: "Fresh",
  stale: "Stale",
  refresh_in_progress: "Refreshing",
  refresh_failed: "Refresh failed",
  unknown: "Unknown",
};

export function ChatShell({ apiBaseUrl }: ChatShellProps) {
  const client = useMemo(() => new APIClient(apiBaseUrl), [apiBaseUrl]);
  const pollHandle = useRef<number | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [queryDetail, setQueryDetail] = useState<QueryDetail | null>(null);
  const [pendingQuery, setPendingQuery] = useState<PendingQueryState | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void bootstrap();
    return () => {
      if (pollHandle.current !== null) {
        window.clearInterval(pollHandle.current);
      }
    };
  }, []);

  async function bootstrap() {
    setLoading(true);
    setError(null);
    try {
      const knownSessions = await client.listSessions();
      setSessions(knownSessions);
      const session = knownSessions[0] ?? (await client.createSession());
      if (knownSessions.length === 0) {
        setSessions([session]);
      }
      await selectSession(session.id);
    } catch (caughtError) {
      setError(errorMessage(caughtError));
    } finally {
      setLoading(false);
    }
  }

  async function selectSession(sessionId: string) {
    setActiveSessionId(sessionId);
    setQueryDetail(null);
    setPendingQuery(null);
    setActivity([]);
    setError(null);
    try {
      const history = await client.listMessages(sessionId);
      setMessages(history);
    } catch (caughtError) {
      setError(errorMessage(caughtError));
    }
  }

  async function createSession() {
    setError(null);
    try {
      const session = await client.createSession();
      setSessions((current) => [session, ...current]);
      await selectSession(session.id);
    } catch (caughtError) {
      setError(errorMessage(caughtError));
    }
  }

  async function submitMessage() {
    if (!activeSessionId || !draft.trim() || sending) {
      return;
    }
    const content = draft.trim();
    setSending(true);
    setError(null);
    setDraft("");
    try {
      const created = await client.createQuery(activeSessionId, content);
      setMessages((current) => [
        ...current,
        {
          message_id: created.message_id,
          session_id: created.session_id,
          query_id: created.query_id,
          role: "user",
          content: created.content,
          created_at: created.created_at,
        },
      ]);
      if (created.query_id) {
        startStreaming(created.query_id);
      }
    } catch (caughtError) {
      setError(errorMessage(caughtError));
    } finally {
      setSending(false);
    }
  }

  function startStreaming(queryId: string) {
    setPendingQuery({ queryId, partialAnswer: "" });
    setActivity([]);
    const eventSource = client.streamQuery(
      queryId,
      (event) => {
        setActivity((current) => [
          {
            id: `${event.query_id}:${event.event}:${event.created_at}:${current.length}`,
            event: event.event,
            created_at: event.created_at,
            data: event.data,
          },
          ...current,
        ]);
        const partialMarkdown = typeof event.data.partial_markdown === "string" ? event.data.partial_markdown : null;
        if (partialMarkdown) {
          setPendingQuery({ queryId, partialAnswer: partialMarkdown });
        }
        if (event.event === "answer_completed" || event.event === "query_failed") {
          eventSource.close();
        }
      },
      () => {
        eventSource.close();
      },
    );
    if (pollHandle.current !== null) {
      window.clearInterval(pollHandle.current);
    }
    pollHandle.current = window.setInterval(async () => {
      try {
        const detail = await client.getQuery(queryId);
        const finalAnswer = detail.answer;
        setQueryDetail(detail);
        if (finalAnswer) {
          setMessages((current) => {
            const withoutDuplicatedAssistant = current.filter(
              (message) => !(message.role === "assistant" && message.query_id === queryId),
            );
            return [
              ...withoutDuplicatedAssistant,
              {
                message_id: `assistant-${queryId}`,
                session_id: detail.session_id,
                query_id: queryId,
                role: "assistant",
                content: finalAnswer.markdown,
                created_at: detail.finished_at,
              },
            ];
          });
        }
        if (detail.status === "completed" || detail.status === "failed" || detail.status === "cancelled") {
          if (pollHandle.current !== null) {
            window.clearInterval(pollHandle.current);
            pollHandle.current = null;
          }
          setPendingQuery(null);
          eventSource.close();
        }
      } catch (_error) {
        // Polling should stay best-effort because the SSE stream is still active.
      }
    }, 1200);
  }

  const displayedAnswer = queryDetail?.answer?.markdown ?? pendingQuery?.partialAnswer ?? "";

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <p className="eyebrow">QANorm Stage 2</p>
          <h1>Engineering assistant</h1>
          <button type="button" className="action-button" onClick={() => void createSession()}>
            New session
          </button>
        </div>
        <div className="session-list">
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={`session-card${session.id === activeSessionId ? " session-card-active" : ""}`}
              onClick={() => void selectSession(session.id)}
            >
              <strong>{session.session_summary || `Session ${session.id.slice(0, 8)}`}</strong>
              <span>{session.status}</span>
            </button>
          ))}
        </div>
      </aside>
      <section className="chat-panel">
        <header className="panel-header">
          <div>
            <p className="eyebrow">Session</p>
            <h2>{activeSessionId ? activeSessionId.slice(0, 8) : "Loading"}</h2>
          </div>
          <div className="status-strip">
            {pendingQuery ? <span className="status-pill status-busy">Streaming</span> : null}
            {queryDetail?.answer?.has_stale_sources ? <span className="status-pill status-warning">Stale sources</span> : null}
            {queryDetail?.answer?.has_external_sources ? <span className="status-pill status-info">External sources</span> : null}
          </div>
        </header>
        <div className="conversation-grid">
          <section className="messages-panel">
            <div className="messages-list">
              {loading ? <p className="muted">Loading session...</p> : null}
              {error ? (
                <div className="error-card">
                  <p>{error}</p>
                  <button type="button" className="action-button" onClick={() => void bootstrap()}>
                    Retry
                  </button>
                </div>
              ) : null}
              {messages.map((message) => (
                <article key={message.message_id} className={`message-bubble role-${message.role}`}>
                  <p className="message-role">{message.role}</p>
                  <pre>{message.content}</pre>
                </article>
              ))}
              {displayedAnswer && !messages.some((message) => message.role === "assistant" && message.query_id === pendingQuery?.queryId) ? (
                <article className="message-bubble role-assistant">
                  <p className="message-role">assistant</p>
                  <pre>{displayedAnswer}</pre>
                </article>
              ) : null}
            </div>
            <div className="composer">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Describe the engineering issue. The assistant will decide what to retrieve."
              />
              <div className="composer-actions">
                <span className="muted">{sending ? "Submitting..." : "Session memory stays scoped to this chat."}</span>
                <button type="button" className="action-button" onClick={() => void submitMessage()} disabled={sending || !draft.trim()}>
                  Send
                </button>
              </div>
            </div>
          </section>
          <aside className="details-panel">
            <div className="details-card">
              <h3>Answer</h3>
              {queryDetail?.answer ? (
                <>
                  <p className="coverage-label">Coverage: {queryDetail.answer.coverage_status}</p>
                  {queryDetail.answer.warnings.map((warning) => (
                    <p key={warning} className="warning-banner">
                      {warning}
                    </p>
                  ))}
                  {queryDetail.answer.limitations.length ? (
                    <div className="meta-block">
                      <strong>Limitations</strong>
                      <ul>
                        {queryDetail.answer.limitations.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {queryDetail.answer.assumptions.length ? (
                    <div className="meta-block">
                      <strong>Assumptions</strong>
                      <ul>
                        {queryDetail.answer.assumptions.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </>
              ) : (
                <p className="muted">No persisted answer yet.</p>
              )}
            </div>
            <div className="details-card">
              <h3>Evidence</h3>
              {queryDetail?.evidence.length ? (
                <div className="evidence-list">
                  {queryDetail.evidence.map((item) => (
                    <article key={item.id} className="evidence-card">
                      <div className="evidence-header">
                        <span className={`source-badge source-${item.source_kind}`}>{SOURCE_KIND_LABELS[item.source_kind]}</span>
                        <span className="freshness-badge">{FRESHNESS_LABELS[item.freshness_status] ?? item.freshness_status}</span>
                      </div>
                      <p className="evidence-title">{item.source_domain || item.document_id || "Source"}</p>
                      <p className="evidence-meta">
                        {item.edition_label ? `Edition: ${item.edition_label}` : "Edition unknown"}
                        {item.locator ? ` • Locator: ${item.locator}` : ""}
                        {item.locator_end ? ` → ${item.locator_end}` : ""}
                      </p>
                      <p className="evidence-quote">{item.quote || item.chunk_text || "No quote available."}</p>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="muted">Evidence blocks will appear here.</p>
              )}
            </div>
            <div className="details-card">
              <h3>Activity</h3>
              {activity.length ? (
                <div className="activity-list">
                  {activity.map((item) => (
                    <article key={item.id} className="activity-row">
                      <strong>{item.event}</strong>
                      <span>{new Date(item.created_at).toLocaleTimeString()}</span>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="muted">Streaming events will appear here.</p>
              )}
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected frontend error.";
}
