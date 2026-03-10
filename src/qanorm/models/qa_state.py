"""Typed runtime state models for Stage 2 orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Iterable
from uuid import UUID

from qanorm.db.types import QueryStatus, SubtaskStatus
from qanorm.models import QAEvidence, QAMessage


def _stable_fingerprint(parts: Iterable[str]) -> str:
    """Build a deterministic fingerprint from ordered string parts."""

    digest = sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass(slots=True)
class EvidenceBundle:
    """Evidence grouped by provenance for prompt rendering and verification."""

    normative: list[QAEvidence] = field(default_factory=list)
    trusted_web: list[QAEvidence] = field(default_factory=list)
    open_web: list[QAEvidence] = field(default_factory=list)

    @property
    def all_items(self) -> list[QAEvidence]:
        """Return evidence in deterministic provenance order."""

        return [*self.normative, *self.trusted_web, *self.open_web]

    def fingerprint(self) -> str:
        """Build a stable fingerprint for the current evidence set."""

        parts = []
        for evidence in self.all_items:
            parts.append(
                "|".join(
                    [
                        str(evidence.id or ""),
                        str(evidence.document_id or ""),
                        str(evidence.node_id or ""),
                        evidence.quote or "",
                        evidence.source_url or "",
                    ]
                )
            )
        return _stable_fingerprint(parts)


@dataclass(slots=True)
class PromptRenderContext:
    """Prepared context passed into prompt templates."""

    session_id: UUID
    query_id: UUID | None
    query_text: str
    session_summary: str | None = None
    intent: str | None = None
    retrieval_mode: str | None = None
    clarification_required: bool = False
    clarification_question: str | None = None
    document_hints: list[str] = field(default_factory=list)
    locator_hints: list[str] = field(default_factory=list)
    subject: str | None = None
    engineering_aspects: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    recent_messages: list[QAMessage] = field(default_factory=list)
    evidence_bundle: EvidenceBundle = field(default_factory=EvidenceBundle)
    stale_warning_messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubtaskState:
    """In-memory representation of one orchestrator subtask."""

    subtask_id: UUID | None
    parent_subtask_id: UUID | None
    subtask_type: str
    description: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    priority: int = 100
    result_summary: str | None = None
    evidence_ids: list[UUID] = field(default_factory=list)


@dataclass(slots=True)
class QueryState:
    """Mutable runtime state for one user query orchestration run."""

    session_id: UUID
    query_id: UUID | None
    message_id: UUID | None
    query_text: str
    status: QueryStatus = QueryStatus.PENDING
    query_type: str | None = None
    session_summary: str | None = None
    intent: str | None = None
    retrieval_mode: str | None = None
    clarification_required: bool = False
    clarification_question: str | None = None
    document_hints: list[str] = field(default_factory=list)
    locator_hints: list[str] = field(default_factory=list)
    subject: str | None = None
    engineering_aspects: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    recent_messages: list[QAMessage] = field(default_factory=list)
    subtasks: list[SubtaskState] = field(default_factory=list)
    evidence_bundle: EvidenceBundle = field(default_factory=EvidenceBundle)
    verification_attempt_count: int = 0
    repair_attempt_count: int = 0
    tool_call_count: int = 0
    attempt_deadline: datetime | None = None
    evidence_fingerprint: str | None = None
    verification_fingerprint: str | None = None
    used_open_web: bool = False
    used_trusted_web: bool = False
    open_web_fallback_allowed: bool = False
    requires_freshness_check: bool = False

    def build_prompt_context(self) -> PromptRenderContext:
        """Create a prompt-ready snapshot of the current runtime state."""

        return PromptRenderContext(
            session_id=self.session_id,
            query_id=self.query_id,
            query_text=self.query_text,
            session_summary=self.session_summary,
            intent=self.intent,
            retrieval_mode=self.retrieval_mode,
            clarification_required=self.clarification_required,
            clarification_question=self.clarification_question,
            document_hints=list(self.document_hints),
            locator_hints=list(self.locator_hints),
            subject=self.subject,
            engineering_aspects=list(self.engineering_aspects),
            constraints=list(self.constraints),
            recent_messages=list(self.recent_messages),
            evidence_bundle=self.evidence_bundle,
        )

    def build_contextual_query_text(self, *, include_assistant_turns: bool = False, max_messages: int = 4) -> str:
        """Build a retrieval-friendly query that preserves recent session context.

        The raw user turn stays first. Recent turns are appended only as compact
        context so retrieval still anchors on the latest question.
        """

        recent = self.recent_messages[-max_messages:]
        context_lines: list[str] = []
        for message in recent:
            role_value = getattr(message.role, "value", str(message.role))
            if role_value == "assistant" and not include_assistant_turns:
                continue
            if not message.content.strip():
                continue
            prefix = "Пользователь" if role_value == "user" else "Ассистент"
            context_lines.append(f"{prefix}: {message.content.strip()}")
        if not context_lines:
            return self.query_text
        return "\n".join(
            [
                f"Текущий вопрос: {self.query_text}",
                "Контекст диалога:",
                *context_lines,
            ]
        )

    def refresh_evidence_fingerprint(self) -> str:
        """Recompute and persist the current evidence-set fingerprint."""

        self.evidence_fingerprint = self.evidence_bundle.fingerprint()
        return self.evidence_fingerprint

    def refresh_verification_fingerprint(self, findings: Iterable[str]) -> str:
        """Recompute and persist the current verification-findings fingerprint."""

        self.verification_fingerprint = _stable_fingerprint(findings)
        return self.verification_fingerprint
