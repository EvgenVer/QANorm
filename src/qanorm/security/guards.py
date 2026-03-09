"""Security guards, sanitation, and session-isolation helpers for Stage 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.db.types import SecuritySeverity
from qanorm.models import SecurityEvent
from qanorm.models.qa_state import QueryState
from qanorm.repositories import SecurityEventRepository
from qanorm.utils.text import normalize_whitespace, strip_html_text


PROMPT_INJECTION_PATTERNS = (
    re.compile(r"\b(ignore|disregard|override)\b.{0,40}\b(previous|system|instructions?)\b", re.IGNORECASE),
    re.compile(r"\b(reveal|print|show)\b.{0,40}\b(prompt|chain[- ]of[- ]thought|hidden)\b", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
)
SUSPICIOUS_HTML_PATTERNS = (
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"<script\b.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<style\b.*?</style>", re.IGNORECASE | re.DOTALL),
)


@dataclass(slots=True, frozen=True)
class SecurityFinding:
    """One classified security observation collected from text or runtime state."""

    event_type: str
    severity: SecuritySeverity
    message: str
    source_kind: str
    details: dict[str, str] = field(default_factory=dict)

    @property
    def blocks_execution(self) -> bool:
        """Return whether this finding requires blocking the current step."""

        return self.severity in {SecuritySeverity.ERROR, SecuritySeverity.CRITICAL}


@dataclass(slots=True, frozen=True)
class SecurityDecision:
    """Normalized outcome of one safety check or policy enforcement step."""

    findings: list[SecurityFinding]
    sanitized_text: str

    @property
    def should_block(self) -> bool:
        """Return whether the caller must stop execution."""

        return any(item.blocks_execution for item in self.findings)

    @property
    def warnings(self) -> list[str]:
        """Return user-facing warning messages derived from the findings."""

        return [item.message for item in self.findings]


def resolve_security_violation(decision: SecurityDecision) -> str:
    """Map a security decision into a caller-facing action strategy."""

    if decision.should_block:
        return "block"
    if decision.findings:
        return "warn"
    return "allow"


def sanitize_external_text(value: str) -> str:
    """Strip active HTML artifacts and normalize whitespace before prompting."""

    cleaned = value
    for pattern in SUSPICIOUS_HTML_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return strip_html_text(cleaned)


def inspect_user_input(value: str) -> SecurityDecision:
    """Inspect direct user input for prompt-injection and policy bypass attempts."""

    return _inspect_text(value, source_kind="user_input", sanitize=False)


def inspect_retrieved_content(value: str, *, source_kind: str) -> SecurityDecision:
    """Inspect retrieved or external text and sanitize it before prompt usage."""

    return _inspect_text(value, source_kind=source_kind, sanitize=True)


def enforce_tool_call_budget(state: QueryState, *, max_tool_calls: int) -> SecurityDecision:
    """Enforce the global tool-call ceiling for one query run."""

    if state.tool_call_count <= max_tool_calls:
        return SecurityDecision(findings=[], sanitized_text="")
    return SecurityDecision(
        findings=[
            SecurityFinding(
                event_type="tool_budget_exceeded",
                severity=SecuritySeverity.ERROR,
                message=f"Tool call budget exceeded: {state.tool_call_count}/{max_tool_calls}.",
                source_kind="runtime",
                details={"tool_call_count": str(state.tool_call_count), "max_tool_calls": str(max_tool_calls)},
            )
        ],
        sanitized_text="",
    )


class SessionIsolationGuard:
    """Validate that cache keys, worker payloads, and temp paths remain session-scoped."""

    def build_cache_key(self, session_id: UUID, *parts: str) -> str:
        """Build a deterministic session-scoped cache key."""

        suffix = ":".join(part.strip(":") for part in parts if part)
        return f"qanorm:session:{session_id}:{suffix}" if suffix else f"qanorm:session:{session_id}"

    def assert_cache_key(self, *, session_id: UUID, cache_key: str) -> None:
        """Fail when a cache key is not namespaced under the current session."""

        expected_prefix = f"qanorm:session:{session_id}:"
        if cache_key != f"qanorm:session:{session_id}" and not cache_key.startswith(expected_prefix):
            raise ValueError("Cache key is not scoped to the current session.")

    def assert_worker_payload(self, *, session_id: UUID, payload: dict[str, object]) -> None:
        """Require background worker payloads to carry the same session identity."""

        payload_session_id = payload.get("session_id")
        if str(payload_session_id) != str(session_id):
            raise ValueError("Worker payload session_id does not match the active session.")

    def assert_temp_artifact_path(self, *, session_id: UUID, path: str | Path) -> None:
        """Require temp artifacts to live under a session-specific directory."""

        normalized = Path(path).as_posix()
        session_marker = f"/{session_id}/"
        if session_marker not in f"/{normalized}/":
            raise ValueError("Temporary artifact path is not isolated per session.")


def record_security_findings(
    session: Session,
    *,
    query_id: UUID | None,
    session_id: UUID | None,
    findings: list[SecurityFinding],
) -> list[SecurityEvent]:
    """Persist collected security findings as durable security events."""

    repository = SecurityEventRepository(session)
    events: list[SecurityEvent] = []
    for finding in findings:
        events.append(
            repository.add(
                SecurityEvent(
                    query_id=query_id,
                    session_id=session_id,
                    event_type=finding.event_type,
                    severity=finding.severity,
                    source_kind=finding.source_kind,
                    details_json=finding.details | {"message": finding.message},
                )
            )
        )
    return events


def _inspect_text(value: str, *, source_kind: str, sanitize: bool) -> SecurityDecision:
    """Run prompt-injection heuristics against one text fragment."""

    sanitized = sanitize_external_text(value) if sanitize else normalize_whitespace(value)
    findings: list[SecurityFinding] = []
    haystack = value
    for pattern in PROMPT_INJECTION_PATTERNS:
        if not pattern.search(haystack):
            continue
        severity = SecuritySeverity.ERROR if source_kind == "user_input" else SecuritySeverity.WARNING
        findings.append(
            SecurityFinding(
                event_type="prompt_injection_suspected",
                severity=severity,
                message="Potential prompt-injection or hidden-instruction pattern detected.",
                source_kind=source_kind,
                details={"pattern": pattern.pattern},
            )
        )
    return SecurityDecision(findings=findings, sanitized_text=sanitized)
