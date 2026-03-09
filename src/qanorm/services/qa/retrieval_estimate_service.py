"""Dry-run estimation helpers for chunk-based retrieval rollout."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import EMBEDDING_DIMENSIONS, StatusNormalized
from qanorm.models import Document, DocumentVersion
from qanorm.providers.base import create_role_bound_providers
from qanorm.providers import create_provider_registry
from qanorm.observability import set_retrieval_metric
from qanorm.settings import RuntimeConfig, get_settings
from qanorm.services.qa.chunking_service import ChunkingConfig, build_retrieval_chunk_drafts
from qanorm.repositories import DocumentNodeRepository


LOCAL_EMBEDDING_PROVIDERS = {"ollama", "lmstudio", "vllm"}


@dataclass(slots=True, frozen=True)
class RetrievalEstimate:
    """Dry-run estimate for the chunked retrieval corpus and embedding rollout."""

    active_document_count: int
    active_version_count: int
    estimated_chunk_count: int
    unique_chunk_count: int
    estimated_token_count: int
    estimated_char_count: int
    embedding_dimensions: int
    estimated_embedding_storage_bytes: int
    estimated_embedding_index_bytes: int
    estimated_embedding_cost: float | None
    embedding_provider: str
    embedding_model: str


def estimate_retrieval_rollout(
    session: Session,
    *,
    chunking_config: ChunkingConfig | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> RetrievalEstimate:
    """Estimate chunk counts, storage, and embedding cost without writing anything."""

    effective_config = chunking_config or ChunkingConfig()
    config = runtime_config or get_settings()
    bindings = create_role_bound_providers(registry=create_provider_registry(), runtime_config=config)
    embedding_provider_name = bindings.embeddings.provider_name
    embedding_model_name = bindings.embeddings.model

    stmt = (
        select(Document, DocumentVersion)
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(
            Document.status_normalized == StatusNormalized.ACTIVE,
            DocumentVersion.is_active.is_(True),
        )
        .order_by(Document.normalized_code.asc())
    )
    rows = list(session.execute(stmt).all())

    estimated_chunk_count = 0
    unique_hashes: set[str] = set()
    estimated_token_count = 0
    estimated_char_count = 0

    node_repository = DocumentNodeRepository(session)
    for _, version in rows:
        drafts = build_retrieval_chunk_drafts(
            node_repository.list_for_document_version(version.id),
            config=effective_config,
        )
        estimated_chunk_count += len(drafts)
        for draft in drafts:
            unique_hashes.add(draft.chunk_hash)
            estimated_token_count += draft.token_count
            estimated_char_count += draft.char_count

    embedding_dimensions = _resolve_embedding_dimensions(
        provider_name=embedding_provider_name,
        model_name=embedding_model_name,
        configured_dimensions=config.qa.providers.embedding_output_dimensions,
    )
    storage_bytes = unique_count_to_storage_bytes(unique_count=len(unique_hashes), dimensions=embedding_dimensions)
    # HNSW overhead is approximate; the estimate is meant for planning, not billing precision.
    index_bytes = ceil(storage_bytes * 0.35)

    estimate = RetrievalEstimate(
        active_document_count=len(rows),
        active_version_count=len(rows),
        estimated_chunk_count=estimated_chunk_count,
        unique_chunk_count=len(unique_hashes),
        estimated_token_count=estimated_token_count,
        estimated_char_count=estimated_char_count,
        embedding_dimensions=embedding_dimensions,
        estimated_embedding_storage_bytes=storage_bytes,
        estimated_embedding_index_bytes=index_bytes,
        estimated_embedding_cost=_estimate_embedding_cost(
            provider_name=embedding_provider_name,
            token_count=estimated_token_count,
        ),
        embedding_provider=embedding_provider_name,
        embedding_model=embedding_model_name,
    )
    set_retrieval_metric("estimated_chunk_count", float(estimate.estimated_chunk_count))
    set_retrieval_metric("unique_chunk_count", float(estimate.unique_chunk_count))
    set_retrieval_metric("estimated_token_count", float(estimate.estimated_token_count))
    set_retrieval_metric("estimated_embedding_storage_bytes", float(estimate.estimated_embedding_storage_bytes))
    set_retrieval_metric("estimated_embedding_index_bytes", float(estimate.estimated_embedding_index_bytes))
    return estimate


def render_retrieval_estimate_report(estimate: RetrievalEstimate) -> str:
    """Render a human-readable dry-run estimate report."""

    cost_text = "0.00 (local provider)" if estimate.estimated_embedding_cost == 0 else (
        "unknown" if estimate.estimated_embedding_cost is None else f"{estimate.estimated_embedding_cost:.2f}"
    )
    return "\n".join(
        [
            "# Retrieval Dry-Run Estimate",
            f"- Active documents: {estimate.active_document_count}",
            f"- Active versions: {estimate.active_version_count}",
            f"- Estimated retrieval chunks: {estimate.estimated_chunk_count}",
            f"- Unique chunk hashes: {estimate.unique_chunk_count}",
            f"- Estimated tokens: {estimate.estimated_token_count}",
            f"- Estimated characters: {estimate.estimated_char_count}",
            f"- Embedding provider/model: {estimate.embedding_provider} / {estimate.embedding_model}",
            f"- Embedding dimensions: {estimate.embedding_dimensions}",
            f"- Estimated embedding storage bytes: {estimate.estimated_embedding_storage_bytes}",
            f"- Estimated embedding index bytes: {estimate.estimated_embedding_index_bytes}",
            f"- Estimated embedding cost: {cost_text}",
        ]
    )


def unique_count_to_storage_bytes(*, unique_count: int, dimensions: int) -> int:
    """Estimate raw vector storage size using 4-byte float components."""

    return unique_count * dimensions * 4


def _resolve_embedding_dimensions(*, provider_name: str, model_name: str, configured_dimensions: int) -> int:
    """Infer a planning dimension for well-known models and fall back conservatively otherwise."""

    normalized_model = model_name.lower()
    if provider_name == "gemini" and "gemini-embedding-001" in normalized_model:
        return configured_dimensions
    if "bge-m3" in normalized_model:
        return 1024
    if "text-embedding-3-large" in normalized_model:
        return 3072
    if "text-embedding-3-small" in normalized_model:
        return 1536
    if provider_name in LOCAL_EMBEDDING_PROVIDERS:
        # Local defaults vary widely, so keep a conservative upper-bound when the model is unknown.
        return EMBEDDING_DIMENSIONS
    return configured_dimensions if configured_dimensions > 0 else EMBEDDING_DIMENSIONS


def _estimate_embedding_cost(*, provider_name: str, token_count: int) -> float | None:
    """Estimate embedding cost for the selected provider when it is known to be local."""

    if provider_name in LOCAL_EMBEDDING_PROVIDERS:
        return 0.0
    return None
