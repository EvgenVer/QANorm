"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { APIClient } from "../lib/api";
import type { Evidence, Message, QueryDetail } from "../lib/types";

type ChatShellProps = {
  apiBaseUrl: string;
};

type PendingQueryState = {
  queryId: string;
  partialAnswer: string;
};

const WEB_CLIENT_ID_KEY = "qanorm:web-client-id";

const SOURCE_KIND_LABELS: Record<string, string> = {
  normative: "Нормативный источник",
  trusted_web: "Доверенный внешний источник",
  open_web: "Веб-поиск",
};

const FRESHNESS_LABELS: Record<string, string> = {
  fresh: "Актуально",
  stale: "Есть риск устаревания локальной редакции",
  refresh_in_progress: "Идет проверка актуальности",
  refresh_failed: "Проверка актуальности не завершена",
  unknown: "Актуальность не подтверждена",
};

const COVERAGE_LABELS: Record<string, string> = {
  complete: "Полное покрытие",
  partial: "Частичное покрытие",
  insufficient: "Недостаточно подтверждений",
  unsupported: "Нет подтвержденного ответа",
};

const LOCATOR_LABELS: Record<string, string> = {
  title: "Заголовок",
  section: "Раздел",
  subsection: "Подраздел",
  point: "Пункт",
  subpoint: "Подпункт",
  paragraph: "Абзац",
  appendix: "Приложение",
  table: "Таблица",
  note: "Примечание",
  fragment: "Фрагмент",
};

export function ChatShell({ apiBaseUrl }: ChatShellProps) {
  const client = useMemo(() => new APIClient(apiBaseUrl), [apiBaseUrl]);
  const pollHandle = useRef<number | null>(null);
  const eventSourceHandle = useRef<EventSource | null>(null);
  const scrollAnchor = useRef<HTMLDivElement | null>(null);

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [queryDetails, setQueryDetails] = useState<Record<string, QueryDetail>>({});
  const [pendingQuery, setPendingQuery] = useState<PendingQueryState | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isEvidenceOpen, setIsEvidenceOpen] = useState(false);

  useEffect(() => {
    void bootstrap();
    return () => {
      clearAsyncHandles();
    };
  }, [client]);

  useEffect(() => {
    scrollAnchor.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pendingQuery]);

  const latestCompletedDetail = useMemo(() => {
    return (
      Object.values(queryDetails)
        .filter((detail) => detail.answer !== null)
        .sort((left, right) => {
          const leftDate = Date.parse(left.finished_at ?? left.created_at ?? "");
          const rightDate = Date.parse(right.finished_at ?? right.created_at ?? "");
          return rightDate - leftDate;
        })[0] ?? null
    );
  }, [queryDetails]);

  async function bootstrap() {
    setLoading(true);
    setError(null);
    try {
      await createFreshSession();
    } catch (caughtError) {
      setError(errorMessage(caughtError));
    } finally {
      setLoading(false);
    }
  }

  async function createFreshSession() {
    clearConversationState();
    const browserClientId = getOrCreateBrowserClientId();
    const session = await client.createWebSession(browserClientId);
    setActiveSessionId(session.id);
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
        startTrackingQuery(created.query_id);
      }
    } catch (caughtError) {
      setError(errorMessage(caughtError));
    } finally {
      setSending(false);
    }
  }

  function startTrackingQuery(queryId: string) {
    clearAsyncHandles();
    setPendingQuery({ queryId, partialAnswer: "Формирую ответ..." });

    const eventSource = client.streamQuery(
      queryId,
      () => {
        // The UI does not pseudo-stream the answer text. SSE is used only to keep
        // the browser aware that the request is still alive and to detect completion.
      },
      () => {
        eventSource.close();
        eventSourceHandle.current = null;
      },
    );
    eventSourceHandle.current = eventSource;

    pollHandle.current = window.setInterval(async () => {
      try {
        const detail = await client.getQuery(queryId);
        setQueryDetails((current) => ({ ...current, [queryId]: detail }));

        if (detail.answer !== null) {
          upsertAssistantMessage(detail);
          setPendingQuery(null);
        }

        if (detail.status === "completed" || detail.status === "failed" || detail.status === "cancelled") {
          if (detail.answer === null) {
            setPendingQuery(null);
          }
          clearAsyncHandles();
        }
      } catch (_error) {
        // Polling stays best-effort. The next tick may still observe the final state.
      }
    }, 1100);
  }

  function upsertAssistantMessage(detail: QueryDetail) {
    const answer = detail.answer;
    if (answer === null) {
      return;
    }

    setMessages((current) => {
      const withoutExisting = current.filter(
        (message) => !(message.role === "assistant" && message.query_id === detail.id),
      );
      return [
        ...withoutExisting,
        {
          message_id: `assistant-${detail.id}`,
          session_id: detail.session_id,
          query_id: detail.id,
          role: "assistant",
          content: buildChatAnswer(detail),
          created_at: detail.finished_at,
        },
      ];
    });
  }

  function clearAsyncHandles() {
    if (pollHandle.current !== null) {
      window.clearInterval(pollHandle.current);
      pollHandle.current = null;
    }
    if (eventSourceHandle.current !== null) {
      eventSourceHandle.current.close();
      eventSourceHandle.current = null;
    }
  }

  function clearConversationState() {
    clearAsyncHandles();
    setActiveSessionId(null);
    setMessages([]);
    setQueryDetails({});
    setPendingQuery(null);
    setDraft("");
    setIsEvidenceOpen(false);
  }

  return (
    <main className="chat-app">
      <header className="topbar">
        <h1>Ассистент инженера</h1>
        <div className="topbar-actions">
          {pendingQuery ? (
            <div className="thinking-indicator" aria-live="polite">
              <span className="thinking-dot" />
              <span className="thinking-dot" />
              <span className="thinking-dot" />
              <span>Формирую ответ</span>
            </div>
          ) : null}
          <button
            type="button"
            className="secondary-button"
            onClick={() => setIsEvidenceOpen((current) => !current)}
            disabled={latestCompletedDetail === null}
          >
            {isEvidenceOpen ? "Скрыть доказательства" : "Показать доказательства"}
          </button>
          <button type="button" className="primary-button" onClick={() => void createFreshSession()} disabled={loading}>
            Новая сессия
          </button>
        </div>
      </header>

      <section className={`chat-layout${isEvidenceOpen ? " chat-layout-with-evidence" : ""}`}>
        <section className="chat-surface">
          <div className="messages-column">
            {loading ? <SystemCard text="Подготавливаю новую сессию..." /> : null}
            {error ? (
              <div className="error-card">
                <p>{error}</p>
                <button type="button" className="secondary-button" onClick={() => void bootstrap()}>
                  Повторить
                </button>
              </div>
            ) : null}
            {messages.map((message) => (
              <MessageBubble
                key={message.message_id}
                message={message}
                detail={message.query_id ? queryDetails[message.query_id] ?? null : null}
              />
            ))}

            {pendingQuery !== null &&
            !messages.some(
              (message) => message.role === "assistant" && message.query_id === pendingQuery.queryId,
            ) ? (
              <article className="message-row message-row-assistant">
                <div className="message-bubble assistant-bubble">
                  <p className="message-label">Ассистент</p>
                  <div className="message-copy">{pendingQuery.partialAnswer}</div>
                </div>
              </article>
            ) : null}

            <div ref={scrollAnchor} />
          </div>

          <div className="composer-shell">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Введите вопрос"
            />
            <div className="composer-footer">
              <button
                type="button"
                className="primary-button"
                onClick={() => void submitMessage()}
                disabled={sending || !draft.trim() || activeSessionId === null}
              >
                {sending ? "Отправка..." : "Отправить"}
              </button>
            </div>
          </div>
        </section>

        {isEvidenceOpen ? (
          <aside className="evidence-panel">
            <header className="evidence-panel-header">
              <div>
                <p className="panel-kicker">Доказательная база</p>
                <h2>Источники ответа</h2>
              </div>
              <button type="button" className="secondary-button" onClick={() => setIsEvidenceOpen(false)}>
                Свернуть
              </button>
            </header>

            {latestCompletedDetail?.answer ? (
              <div className="summary-tags">
                <span className="status-tag">
                  {COVERAGE_LABELS[latestCompletedDetail.answer.coverage_status] ??
                    latestCompletedDetail.answer.coverage_status}
                </span>
                {latestCompletedDetail.answer.has_stale_sources ? (
                  <span className="status-tag status-tag-warning">Есть риск устаревания</span>
                ) : null}
                {latestCompletedDetail.answer.has_external_sources ? (
                  <span className="status-tag status-tag-info">Есть внешние источники</span>
                ) : null}
              </div>
            ) : null}

            {latestCompletedDetail?.answer?.warnings?.length ? (
              <div className="warning-stack">
                {latestCompletedDetail.answer.warnings.map((warning) => (
                  <p key={warning} className="warning-banner">
                    {warning}
                  </p>
                ))}
              </div>
            ) : null}

            <div className="evidence-stack">
              {latestCompletedDetail?.evidence?.length ? (
                latestCompletedDetail.evidence.map((item) => <EvidenceCard key={item.id} evidence={item} />)
              ) : (
                <SystemCard text="После завершения ответа здесь появятся подтверждающие источники." />
              )}
            </div>
          </aside>
        ) : null}
      </section>
    </main>
  );
}

function MessageBubble({ message, detail }: { message: Message; detail: QueryDetail | null }) {
  const isAssistant = message.role === "assistant";

  return (
    <article className={`message-row ${isAssistant ? "message-row-assistant" : "message-row-user"}`}>
      <div className={`message-bubble ${isAssistant ? "assistant-bubble" : "user-bubble"}`}>
        <p className="message-label">{isAssistant ? "Ассистент" : "Вы"}</p>
        <div className="message-copy">
          {isAssistant ? <RenderedAssistantContent content={message.content} /> : message.content}
        </div>
      </div>
    </article>
  );
}

function RenderedAssistantContent({ content }: { content: string }) {
  const blocks = content.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);

  return (
    <>
      {blocks.map((block, index) => {
        if (block.startsWith("### ")) {
          return (
            <section key={`${index}-${block.slice(0, 12)}`} className="rendered-section">
              <h4>{block.slice(4).trim()}</h4>
            </section>
          );
        }
        if (block.startsWith("## ")) {
          return (
            <section key={`${index}-${block.slice(0, 12)}`} className="rendered-section">
              <h3>{block.slice(3).trim()}</h3>
            </section>
          );
        }

        const lines = block.split("\n").map((item) => item.trim()).filter(Boolean);
        if (lines.every((line) => line.startsWith("- "))) {
          return (
            <ul key={`${index}-${lines.length}`} className="rendered-list">
              {lines.map((line) => (
                <li key={line}>{line.slice(2).trim()}</li>
              ))}
            </ul>
          );
        }

        return (
          <p key={`${index}-${block.slice(0, 12)}`} className="rendered-paragraph">
            {block}
          </p>
        );
      })}
    </>
  );
}

function EvidenceCard({ evidence }: { evidence: Evidence }) {
  const title = formatEvidenceTitle(evidence);
  const locator =
    evidence.source_kind === "normative"
      ? formatLocatorRange(evidence.locator, evidence.locator_end)
      : formatLocator(evidence.locator_end ?? evidence.locator);
  const freshnessLabel = FRESHNESS_LABELS[evidence.freshness_status] ?? evidence.freshness_status;
  const fullQuote = pickReadableQuote(evidence);

  return (
    <article className="evidence-card">
      <div className="tag-row">
        <span className="source-tag">{SOURCE_KIND_LABELS[evidence.source_kind]}</span>
        <span className="freshness-tag">{freshnessLabel}</span>
      </div>
      <p className="evidence-title">{title}</p>
      <div className="evidence-meta">
        {evidence.edition_label ? <p>Редакция: {evidence.edition_label}</p> : null}
        {locator ? <p>Локатор: {locator}</p> : null}
        {evidence.source_kind !== "normative" ? (
          <p className="evidence-warning">
            Ненормативный источник. Использовать только как вспомогательный материал и перепроверять.
          </p>
        ) : null}
        {evidence.source_url ? (
          <p>
            Ссылка:{" "}
            <a href={evidence.source_url} target="_blank" rel="noreferrer">
              {evidence.source_domain || evidence.source_url}
            </a>
          </p>
        ) : null}
      </div>
      <blockquote className="evidence-quote">{fullQuote}</blockquote>
    </article>
  );
}

function SystemCard({ text }: { text: string }) {
  return (
    <div className="system-card">
      <span className="system-dot" />
      <p>{text}</p>
    </div>
  );
}

function getOrCreateBrowserClientId(): string {
  const existing = window.localStorage.getItem(WEB_CLIENT_ID_KEY);
  if (existing) {
    return existing;
  }

  const browserClientId =
    typeof window.crypto?.randomUUID === "function"
      ? window.crypto.randomUUID()
      : `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  window.localStorage.setItem(WEB_CLIENT_ID_KEY, browserClientId);
  return browserClientId;
}

function formatEvidenceTitle(evidence: Evidence): string {
  if (evidence.source_kind === "normative") {
    return evidence.document_title ?? "Нормативный документ";
  }
  if (evidence.source_title) {
    return evidence.source_title;
  }
  if (evidence.source_domain) {
    return evidence.source_domain;
  }
  if (evidence.source_url) {
    return evidence.source_url;
  }
  return "Внешний источник";
}

function formatLocatorRange(locator: string | null, locatorEnd: string | null): string | null {
  const start = formatLocator(locator);
  const end = formatLocator(locatorEnd);
  if (start && end && start !== end) {
    return `${start} -> ${end}`;
  }
  return start ?? end;
}

function formatLocator(locator: string | null): string | null {
  if (!locator) {
    return null;
  }

  const parts = locator
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [rawType, ...rawValueParts] = part.split(":");
      const value = rawValueParts.join(":").trim();
      const label = LOCATOR_LABELS[rawType] ?? rawType;
      return value ? `${label} ${value}` : label;
    });

  return parts.length ? parts.join(" / ") : locator;
}

function pickReadableQuote(evidence: Evidence): string {
  const primary = (evidence.chunk_text || "").trim();
  const fallback = (evidence.quote || "").trim();

  if (primary.length >= fallback.length && primary.length > 0) {
    return primary;
  }
  if (fallback.length > 0) {
    return fallback;
  }
  return "Цитата не подготовлена.";
}

function buildChatAnswer(detail: QueryDetail): string {
  const answer = detail.answer;
  if (!answer) {
    return "";
  }
  if (answer.sections.length) {
    return answer.sections
      .map((section) => `${section.heading}\n${section.body}`.trim())
      .filter(Boolean)
      .join("\n\n");
  }
  return answer.answer_text || answer.markdown;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Произошла непредвиденная ошибка интерфейса.";
}
