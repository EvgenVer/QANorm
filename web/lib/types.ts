export type SessionStatus = "active" | "expired" | "closed";

export type Session = {
  id: string;
  channel: "web" | "telegram";
  status: SessionStatus;
  session_summary: string | null;
  created_at: string | null;
  updated_at: string | null;
  expires_at: string | null;
};

export type Message = {
  message_id: string;
  session_id: string;
  query_id: string | null;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string | null;
};

export type Citation = {
  title: string;
  edition_label: string | null;
  locator: string | null;
  quote: string | null;
  is_normative: boolean;
  requires_verification: boolean;
};

export type AnswerSection = {
  heading: string;
  body: string;
  source_kind: "normative" | "trusted_web" | "open_web";
  citations: Citation[];
};

export type Answer = {
  answer_text: string;
  markdown: string;
  answer_format: string;
  coverage_status: string;
  has_stale_sources: boolean;
  has_external_sources: boolean;
  assumptions: string[];
  limitations: string[];
  warnings: string[];
  sections: AnswerSection[];
  model_name: string | null;
};

export type Evidence = {
  id: string;
  source_kind: "normative" | "trusted_web" | "open_web";
  source_title: string | null;
  source_url: string | null;
  source_domain: string | null;
  document_id: string | null;
  document_title: string | null;
  document_version_id: string | null;
  chunk_id: string | null;
  locator: string | null;
  locator_end: string | null;
  edition_label: string | null;
  quote: string | null;
  chunk_text: string | null;
  freshness_status: string;
  is_normative: boolean;
  requires_verification: boolean;
  relevance_score: number | null;
};

export type QueryDetail = {
  id: string;
  session_id: string;
  message_id: string;
  status: string;
  query_type: string | null;
  query_text: string;
  requires_freshness_check: boolean;
  used_open_web: boolean;
  used_trusted_web: boolean;
  created_at: string | null;
  finished_at: string | null;
  answer: Answer | null;
  evidence: Evidence[];
};

export type QueryCreated = {
  message_id: string;
  session_id: string;
  query_id: string | null;
  role: string;
  content: string;
  created_at: string | null;
};

export type StreamEventEnvelope = {
  event: string;
  query_id: string;
  data: Record<string, unknown>;
  created_at: string;
};
