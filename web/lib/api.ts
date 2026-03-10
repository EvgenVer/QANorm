import type {
  Message,
  QueryCreated,
  QueryDetail,
  StreamEventEnvelope,
  Session,
} from "./types";

export class APIClientError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "APIClientError";
    this.status = status;
  }
}

export class APIClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async createWebSession(externalUserId: string): Promise<Session> {
    return this.request<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify({
        channel: "web",
        external_user_id: externalUserId,
        replace_existing: true,
      }),
    });
  }

  async listMessages(sessionId: string): Promise<Message[]> {
    return this.request<Message[]>(`/sessions/${sessionId}/messages`);
  }

  async createQuery(sessionId: string, content: string): Promise<QueryCreated> {
    return this.request<QueryCreated>(`/sessions/${sessionId}/queries`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
  }

  async getQuery(queryId: string): Promise<QueryDetail> {
    return this.request<QueryDetail>(`/queries/${queryId}`);
  }

  streamQuery(
    queryId: string,
    onEvent: (event: StreamEventEnvelope) => void,
    onError: (error: Event) => void,
  ): EventSource {
    const eventSource = new EventSource(`${this.baseUrl}/queries/${queryId}/events`);
    const forwardEvent = (rawEvent: MessageEvent<string>) => {
      try {
        onEvent(JSON.parse(rawEvent.data) as StreamEventEnvelope);
      } catch (_error) {
        onEvent({
          event: rawEvent.type,
          query_id: queryId,
          data: { raw: rawEvent.data },
          created_at: new Date().toISOString(),
        });
      }
    };

    eventSource.onmessage = forwardEvent;
    for (const eventName of [
      "stream_ready",
      "query_created",
      "analysis_started",
      "analysis_completed",
      "planning_completed",
      "partial_answer",
      "answer_completed",
      "query_failed",
    ]) {
      eventSource.addEventListener(eventName, forwardEvent);
    }
    eventSource.onerror = onError;
    return eventSource;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new APIClientError(await response.text(), response.status);
    }
    return (await response.json()) as T;
  }
}
