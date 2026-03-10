"""Shared database enums and constants."""

from __future__ import annotations

from enum import StrEnum


EMBEDDING_DIMENSIONS = 1536


class StatusNormalized(StrEnum):
    """Normalized document status values."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class ProcessingStatus(StrEnum):
    """Processing lifecycle states for document versions."""

    PENDING = "pending"
    DOWNLOADED = "downloaded"
    EXTRACTED = "extracted"
    NORMALIZED = "normalized"
    INDEXED = "indexed"
    FAILED = "failed"


class ArtifactType(StrEnum):
    """Supported raw artifact kinds."""

    HTML_RAW = "html_raw"
    PDF_RAW = "pdf_raw"
    PAGE_IMAGE = "page_image"
    OCR_RAW = "ocr_raw"
    PARSED_TEXT_SNAPSHOT = "parsed_text_snapshot"


class JobType(StrEnum):
    """Supported ingestion job types."""

    CRAWL_SEED = "crawl_seed"
    PARSE_LIST_PAGE = "parse_list_page"
    PROCESS_DOCUMENT_CARD = "process_document_card"
    DOWNLOAD_ARTIFACTS = "download_artifacts"
    EXTRACT_TEXT = "extract_text"
    RUN_OCR = "run_ocr"
    NORMALIZE_DOCUMENT = "normalize_document"
    INDEX_DOCUMENT = "index_document"
    REFRESH_DOCUMENT = "refresh_document"


class JobStatus(StrEnum):
    """Ingestion job states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionChannel(StrEnum):
    """Supported user access channels."""

    WEB = "web"
    TELEGRAM = "telegram"


class SessionStatus(StrEnum):
    """Lifecycle states for a chat session."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"


class MessageRole(StrEnum):
    """Supported message roles stored in session history."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class QueryStatus(StrEnum):
    """Execution stages for a user query."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    RETRIEVING = "retrieving"
    SYNTHESIZING = "synthesizing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubtaskStatus(StrEnum):
    """Execution stages for a decomposed subtask."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EvidenceSourceKind(StrEnum):
    """Supported evidence source groups."""

    NORMATIVE = "normative"
    TRUSTED_WEB = "trusted_web"
    OPEN_WEB = "open_web"


class FreshnessStatus(StrEnum):
    """Observed freshness state for evidence and answers."""

    FRESH = "fresh"
    STALE = "stale"
    REFRESH_IN_PROGRESS = "refresh_in_progress"
    REFRESH_FAILED = "refresh_failed"
    UNKNOWN = "unknown"


class AnswerStatus(StrEnum):
    """Persistence status for answer records."""

    DRAFT = "draft"
    COMPLETED = "completed"
    FAILED = "failed"


class CoverageStatus(StrEnum):
    """Coverage evaluation results for an answer."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class AnswerMode(StrEnum):
    """User-facing answer modes selected after retrieval and verification."""

    DIRECT_ANSWER = "direct_answer"
    PARTIAL_ANSWER = "partial_answer"
    CLARIFY = "clarify"
    DECLINE = "decline"


class VerificationResult(StrEnum):
    """Possible outcomes for verification checks."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class ToolInvocationStatus(StrEnum):
    """Execution status for a tool invocation."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FreshnessCheckStatus(StrEnum):
    """Status values for a freshness check run."""

    PENDING = "pending"
    FRESH = "fresh"
    STALE = "stale"
    REFRESH_IN_PROGRESS = "refresh_in_progress"
    REFRESH_FAILED = "refresh_failed"
    FAILED = "failed"


class SecuritySeverity(StrEnum):
    """Severity levels for security events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SearchScope(StrEnum):
    """Search scopes supported by the orchestration layer."""

    NORMATIVE = "normative"
    TRUSTED_WEB = "trusted_web"
    OPEN_WEB = "open_web"


class SearchStatus(StrEnum):
    """Result states for a search provider call."""

    COMPLETED = "completed"
    FAILED = "failed"


class TrustedSourceSyncStatus(StrEnum):
    """Execution status for a trusted source synchronization run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
