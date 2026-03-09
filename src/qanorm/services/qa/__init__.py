"""Session and query services for Stage 2."""

from qanorm.services.qa.chunking_service import (
    ChunkBackfillResult,
    ChunkingConfig,
    RetrievalChunkDraft,
    backfill_active_retrieval_chunks,
    build_retrieval_chunk_drafts,
    sync_retrieval_chunks_for_version,
)
from qanorm.services.qa.context_service import ContextService
from qanorm.services.qa.freshness_service import (
    FreshnessEvaluationResult,
    LocalDocumentFreshnessState,
    evaluate_freshness_check,
    load_local_document_freshness_state,
    queue_refresh_for_freshness_check,
    schedule_freshness_checks,
    should_run_freshness_check,
)
from qanorm.services.qa.open_web_service import (
    OpenWebDocument,
    fetch_open_web_document,
    normalize_open_web_results_to_evidence,
    sanitize_html_to_text,
    search_open_web,
)
from qanorm.services.qa.query_service import QueryService
from qanorm.services.qa.retrieval_estimate_service import (
    RetrievalEstimate,
    estimate_retrieval_rollout,
    render_retrieval_estimate_report,
)
from qanorm.services.qa.retrieval_service import (
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    backfill_chunk_embeddings,
    normalize_hits_to_evidence,
    persist_normative_evidence,
    retrieve_normative_evidence,
)
from qanorm.services.qa.session_service import SessionService
from qanorm.services.qa.trusted_sources_service import (
    TrustedSourceSearchHit,
    TrustedSourceSyncResult,
    chunk_trusted_source_text,
    normalize_trusted_hits_to_evidence,
    search_trusted_sources,
    sync_trusted_source,
)
from qanorm.services.qa.verification_service import VerificationFinding, VerificationOutcome, VerificationService

__all__ = [
    "ChunkBackfillResult",
    "ChunkingConfig",
    "ContextService",
    "FreshnessEvaluationResult",
    "LocalDocumentFreshnessState",
    "OpenWebDocument",
    "QueryService",
    "RetrievalChunkDraft",
    "RetrievalEstimate",
    "RetrievalHit",
    "RetrievalRequest",
    "RetrievalResult",
    "SessionService",
    "TrustedSourceSearchHit",
    "TrustedSourceSyncResult",
    "VerificationFinding",
    "VerificationOutcome",
    "VerificationService",
    "backfill_active_retrieval_chunks",
    "backfill_chunk_embeddings",
    "build_retrieval_chunk_drafts",
    "chunk_trusted_source_text",
    "estimate_retrieval_rollout",
    "evaluate_freshness_check",
    "fetch_open_web_document",
    "load_local_document_freshness_state",
    "normalize_open_web_results_to_evidence",
    "normalize_hits_to_evidence",
    "normalize_trusted_hits_to_evidence",
    "persist_normative_evidence",
    "queue_refresh_for_freshness_check",
    "render_retrieval_estimate_report",
    "retrieve_normative_evidence",
    "schedule_freshness_checks",
    "sanitize_html_to_text",
    "search_open_web",
    "search_trusted_sources",
    "should_run_freshness_check",
    "sync_trusted_source",
    "sync_retrieval_chunks_for_version",
]
