"""Normative retrieval services built on chunked Stage 1 data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import Session

from qanorm.audit import AuditWriter
from qanorm.db.types import EvidenceSourceKind, FreshnessStatus, StatusNormalized
from qanorm.models import (
    ChunkEmbedding,
    Document,
    DocumentNode,
    DocumentReference,
    DocumentVersion,
    QAEvidence,
    QAQuery,
    RetrievalChunk,
)
from qanorm.services.qa.document_resolver import DocumentResolutionResult, DocumentResolutionStatus
from qanorm.normalizers.codes import clean_document_code, normalize_document_code
from qanorm.observability import increment_event, set_backfill_metric, set_retrieval_metric
from qanorm.providers import create_provider_registry
from qanorm.providers.base import EmbeddingProvider, EmbeddingRequest, create_role_bound_providers
from qanorm.repositories import ChunkEmbeddingRepository, QAEvidenceRepository, RetrievalChunkRepository
from qanorm.services.qa.chunking_service import build_chunk_hash, build_node_locator_map
from qanorm.settings import RuntimeConfig, get_settings
from qanorm.utils.text import normalize_whitespace


LOCATOR_QUERY_RE = re.compile(r"(?P<code>.+?)\s+(?P<locator>\d+(?:[./]\d+)*[a-zа-я]?)$", re.IGNORECASE)
RRF_K = 60


@dataclass(slots=True, frozen=True)
class RetrievalRequest:
    """Normalized retrieval request with optional metadata filters."""

    query_text: str
    limit: int = 10
    offset: int = 0
    document_type: str | None = None
    document_ids: list[UUID] = field(default_factory=list)
    locator_hint: str | None = None
    retrieval_scope: str = "global"
    active_only: bool = True
    include_related_documents: bool = True
    enable_vector_search: bool = True
    model_revision: str = ""


@dataclass(slots=True, frozen=True)
class RetrievalHit:
    """One normalized normative retrieval hit."""

    chunk_id: UUID
    chunk_hash: str
    document_id: UUID
    document_version_id: UUID
    document_code: str
    document_title: str | None
    document_type: str | None
    edition_label: str | None
    start_node_id: UUID
    end_node_id: UUID
    locator: str | None
    locator_end: str | None
    chunk_text: str
    quote: str
    score: float
    score_source: str
    freshness_status: FreshnessStatus


@dataclass(slots=True, frozen=True)
class RetrievalResult:
    """Combined retrieval result including primary and secondary evidence."""

    primary_hits: list[RetrievalHit]
    secondary_hits: list[RetrievalHit]

    @property
    def all_hits(self) -> list[RetrievalHit]:
        """Return all hits in user-facing precedence order."""

        return [*self.primary_hits, *self.secondary_hits]


async def retrieve_normative_evidence(
    session: Session,
    *,
    request: RetrievalRequest,
    embedding_provider: EmbeddingProvider | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> RetrievalResult:
    """Run exact, FTS, and optional vector retrieval over active normative chunks."""

    exact_hits = _run_exact_match_lookup(session, request=request)
    fts_hits = _run_fts_search(session, request=request)
    vector_hits: list[RetrievalHit] = []
    if request.enable_vector_search:
        vector_hits = await _run_vector_search(
            session,
            request=request,
            embedding_provider=embedding_provider,
            runtime_config=runtime_config,
        )

    primary_hits = _merge_ranked_hits(
        exact_hits=exact_hits,
        fts_hits=fts_hits,
        vector_hits=vector_hits,
        offset=request.offset,
        limit=request.limit,
    )
    secondary_hits = _load_secondary_hits(session, primary_hits=primary_hits, limit=max(3, request.limit // 2)) if request.include_related_documents else []
    set_retrieval_metric("primary_hit_count", float(len(primary_hits)))
    set_retrieval_metric("secondary_hit_count", float(len(secondary_hits)))
    increment_event("retrieval_request", status="ok")
    return RetrievalResult(primary_hits=primary_hits, secondary_hits=secondary_hits)


async def retrieve_normative_evidence_with_resolution(
    session: Session,
    *,
    request: RetrievalRequest,
    resolution: DocumentResolutionResult | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> tuple[RetrievalResult, dict[str, Any]]:
    """Prefer document-scoped retrieval and fall back to global retrieval only when needed."""

    metadata = {
        "resolution_status": resolution.status.value if resolution is not None else "unresolved",
        "initial_scope": "global",
        "fallback_used": False,
    }
    if resolution is None or resolution.status is not DocumentResolutionStatus.RESOLVED:
        result = await retrieve_normative_evidence(
            session,
            request=request,
            embedding_provider=embedding_provider,
            runtime_config=runtime_config,
        )
        metadata["final_scope"] = "global"
        metadata["result_count"] = len(result.all_hits)
        return result, metadata

    scoped_request = replace(
        request,
        document_ids=resolution.resolved_document_ids,
        locator_hint=resolution.locator_hint,
        retrieval_scope=resolution.retrieval_scope,
    )
    metadata["initial_scope"] = resolution.retrieval_scope
    scoped_result = await retrieve_normative_evidence(
        session,
        request=scoped_request,
        embedding_provider=embedding_provider,
        runtime_config=runtime_config,
    )
    if _is_scoped_result_sufficient(scoped_result, locator_hint=resolution.locator_hint):
        metadata["final_scope"] = resolution.retrieval_scope
        metadata["result_count"] = len(scoped_result.all_hits)
        return scoped_result, metadata

    global_result = await retrieve_normative_evidence(
        session,
        request=replace(request, locator_hint=resolution.locator_hint, retrieval_scope="global"),
        embedding_provider=embedding_provider,
        runtime_config=runtime_config,
    )
    metadata["fallback_used"] = True
    metadata["final_scope"] = "global"
    metadata["result_count"] = len(global_result.all_hits)
    return global_result, metadata


def normalize_hits_to_evidence(
    *,
    query_id: UUID,
    hits: list[RetrievalHit],
    subtask_id: UUID | None = None,
) -> list[QAEvidence]:
    """Convert retrieval hits into normalized normative evidence rows with deduplication."""

    seen_chunk_ids: set[UUID] = set()
    seen_chunk_hashes: set[str] = set()
    evidence_rows: list[QAEvidence] = []
    for hit in hits:
        if hit.chunk_id in seen_chunk_ids or hit.chunk_hash in seen_chunk_hashes:
            continue
        seen_chunk_ids.add(hit.chunk_id)
        seen_chunk_hashes.add(hit.chunk_hash)
        evidence_rows.append(
            QAEvidence(
                query_id=query_id,
                subtask_id=subtask_id,
                chunk_id=hit.chunk_id,
                source_kind=EvidenceSourceKind.NORMATIVE,
                document_id=hit.document_id,
                document_version_id=hit.document_version_id,
                node_id=hit.start_node_id,
                start_node_id=hit.start_node_id,
                end_node_id=hit.end_node_id,
                edition_label=hit.edition_label,
                locator=hit.locator,
                locator_end=hit.locator_end,
                quote=hit.quote,
                chunk_text=hit.chunk_text,
                relevance_score=hit.score,
                freshness_status=hit.freshness_status,
                is_normative=True,
                requires_verification=False,
            )
        )
    return evidence_rows


def persist_normative_evidence(
    session: Session,
    *,
    query_id: UUID,
    hits: list[RetrievalHit],
    subtask_id: UUID | None = None,
) -> list[QAEvidence]:
    """Persist retrieval hits as evidence rows and return the stored records."""

    evidence = normalize_hits_to_evidence(query_id=query_id, hits=hits, subtask_id=subtask_id)
    if not evidence:
        return []
    stored = QAEvidenceRepository(session).add_many(evidence)
    query = session.get(QAQuery, query_id) if stored else None
    AuditWriter(session).write(
        session_id=query.session_id if query is not None else None,
        query_id=query_id,
        subtask_id=subtask_id,
        event_type="normative_retrieval_persisted",
        actor_kind="retrieval_service",
        payload_json={"evidence_count": len(stored), "hit_count": len(hits)},
    )
    return stored


async def backfill_chunk_embeddings(
    session: Session,
    *,
    runtime_config: RuntimeConfig | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    batch_size: int = 32,
    existing_lookup_batch_size: int = 5000,
    checkpoint_every_batches: int | None = None,
    max_generation_batches: int | None = None,
    model_revision: str = "",
) -> dict[str, Any]:
    """Generate deduplicated embeddings for all active retrieval chunks."""

    config = runtime_config or get_settings()
    provider = embedding_provider or create_role_bound_providers(
        registry=create_provider_registry(),
        runtime_config=config,
    ).embeddings
    expected_dimensions = config.qa.providers.embedding_output_dimensions

    chunk_repository = RetrievalChunkRepository(session)
    embedding_repository = ChunkEmbeddingRepository(session)
    active_chunks = chunk_repository.list_active()
    if not active_chunks:
        return {"processed_chunk_count": 0, "generated_embedding_count": 0, "reused_embedding_count": 0}

    chunk_texts_by_hash: dict[str, str] = {}
    for chunk in active_chunks:
        chunk_texts_by_hash.setdefault(chunk.chunk_hash, chunk.chunk_text)

    existing_hashes: set[str] = set()
    all_chunk_hashes = list(chunk_texts_by_hash)
    for batch_start in range(0, len(all_chunk_hashes), existing_lookup_batch_size):
        hash_batch = all_chunk_hashes[batch_start : batch_start + existing_lookup_batch_size]
        existing_embeddings = embedding_repository.list_for_hashes(
            hash_batch,
            model_provider=provider.provider_name,
            model_name=provider.model,
            model_revision=model_revision,
        )
        existing_hashes.update(item.chunk_hash for item in existing_embeddings)
    missing_hashes = [item for item in all_chunk_hashes if item not in existing_hashes]

    generated_embeddings = 0
    processed_batches = 0
    for batch_start in range(0, len(missing_hashes), batch_size):
        if max_generation_batches is not None and processed_batches >= max_generation_batches:
            break
        batch_hashes = missing_hashes[batch_start : batch_start + batch_size]
        batch_texts = [chunk_texts_by_hash[item] for item in batch_hashes]
        response = await provider.embed(EmbeddingRequest(model=provider.model, texts=batch_texts))
        if response.dimensions != expected_dimensions:
            raise ValueError(
                f"Embedding provider returned {response.dimensions} dimensions, "
                f"expected {expected_dimensions}."
            )
        embedding_repository.add_many(
            [
                ChunkEmbedding(
                    chunk_hash=chunk_hash,
                    model_provider=provider.provider_name,
                    model_name=provider.model,
                    model_revision=model_revision,
                    dimensions=response.dimensions,
                    chunk_text_sample=chunk_texts_by_hash[chunk_hash][:500],
                    embedding=vector,
                )
                for chunk_hash, vector in zip(batch_hashes, response.vectors, strict=True)
            ]
        )
        generated_embeddings += len(batch_hashes)
        processed_batches += 1
        if checkpoint_every_batches and processed_batches % checkpoint_every_batches == 0:
            # Periodic commits make the long-running backfill resumable after network failures.
            session.commit()

    result = {
        "processed_chunk_count": len(active_chunks),
        "missing_hash_count": len(missing_hashes),
        "generated_embedding_count": generated_embeddings,
        "reused_embedding_count": len(existing_hashes),
        "processed_batches": processed_batches,
    }
    set_backfill_metric("processed_chunk_count", float(result["processed_chunk_count"]))
    set_backfill_metric("generated_embedding_count", float(result["generated_embedding_count"]))
    set_backfill_metric("reused_embedding_count", float(result["reused_embedding_count"]))
    set_backfill_metric("missing_hash_count", float(result["missing_hash_count"]))
    increment_event("embedding_backfill", status="ok")
    return result


def _run_exact_match_lookup(session: Session, *, request: RetrievalRequest) -> list[RetrievalHit]:
    """Run code-oriented and locator-oriented retrieval without dense search."""

    normalized_code, locator_hint = _split_query_for_locator(request.query_text)
    effective_locator_hint = request.locator_hint or locator_hint
    predicates = [
        Document.normalized_code == normalized_code,
        Document.display_code.ilike(f"%{clean_document_code(request.query_text)}%"),
    ]
    if request.document_ids:
        predicates.extend(
            [
                RetrievalChunk.locator.ilike(f"%{effective_locator_hint}%") if effective_locator_hint else literal(False),
                RetrievalChunk.locator_end.ilike(f"%{effective_locator_hint}%") if effective_locator_hint else literal(False),
                RetrievalChunk.chunk_text.ilike(f"%{clean_document_code(request.query_text)}%"),
            ]
        )
    elif effective_locator_hint:
        predicates.append(RetrievalChunk.locator.ilike(f"%{effective_locator_hint}%"))
        predicates.append(RetrievalChunk.locator_end.ilike(f"%{effective_locator_hint}%"))
    stmt = (
        _build_chunk_query(session, request=request)
        .where(or_(*predicates))
        .order_by(Document.normalized_code.asc(), RetrievalChunk.chunk_index.asc())
        .limit(max(request.limit * 2, 10))
    )
    return [_row_to_hit(session, row, score_source="exact", score=float(max(1, request.limit * 2 - index))) for index, row in enumerate(session.execute(stmt).all(), start=1)]


def _run_fts_search(session: Session, *, request: RetrievalRequest) -> list[RetrievalHit]:
    """Run lexical retrieval against persisted chunk TSV payloads."""

    ts_query = func.plainto_tsquery("simple", request.query_text)
    rank = func.ts_rank_cd(RetrievalChunk.chunk_text_tsv, ts_query)
    stmt = (
        _build_chunk_query(session, request=request)
        .where(RetrievalChunk.chunk_text_tsv.op("@@")(ts_query))
        .order_by(rank.desc(), RetrievalChunk.chunk_index.asc())
        .limit(max(request.limit * 4, 20))
        .add_columns(rank.label("score"))
    )
    hits: list[RetrievalHit] = []
    for row in session.execute(stmt).all():
        score = float(row[-1] or 0.0)
        hits.append(_row_to_hit(session, row[:-1], score_source="fts", score=score))
    return hits


async def _run_vector_search(
    session: Session,
    *,
    request: RetrievalRequest,
    embedding_provider: EmbeddingProvider | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> list[RetrievalHit]:
    """Run dense retrieval against deduplicated chunk embeddings."""

    config = runtime_config or get_settings()
    provider = embedding_provider or create_role_bound_providers(
        registry=create_provider_registry(),
        runtime_config=config,
    ).embeddings
    response = await provider.embed(EmbeddingRequest(model=provider.model, texts=[request.query_text]))
    if not response.vectors:
        return []

    query_vector = response.vectors[0]
    distance = ChunkEmbedding.embedding.cosine_distance(query_vector)
    stmt = (
        _build_chunk_query(session, request=request)
        .join(
            ChunkEmbedding,
            and_(
                RetrievalChunk.chunk_hash == ChunkEmbedding.chunk_hash,
                ChunkEmbedding.model_provider == provider.provider_name,
                ChunkEmbedding.model_name == provider.model,
                ChunkEmbedding.model_revision == request.model_revision,
            ),
        )
        .order_by(distance.asc(), RetrievalChunk.chunk_index.asc())
        .limit(max(request.limit * 4, 20))
        .add_columns(distance.label("distance"))
    )
    hits: list[RetrievalHit] = []
    for row in session.execute(stmt).all():
        score = max(0.0, 1.0 - float(row[-1] or 1.0))
        hits.append(_row_to_hit(session, row[:-1], score_source="vector", score=score))
    return hits


def _merge_ranked_hits(
    *,
    exact_hits: list[RetrievalHit],
    fts_hits: list[RetrievalHit],
    vector_hits: list[RetrievalHit],
    offset: int,
    limit: int,
) -> list[RetrievalHit]:
    """Combine exact, lexical, and vector rankings with reciprocal-rank fusion."""

    combined: dict[UUID, dict[str, Any]] = {}
    for source_name, hits in (("exact", exact_hits), ("fts", fts_hits), ("vector", vector_hits)):
        for rank, hit in enumerate(hits, start=1):
            item = combined.setdefault(hit.chunk_id, {"hit": hit, "score": 0.0, "sources": set()})
            item["score"] += 1.0 / (RRF_K + rank)
            item["sources"].add(source_name)
            if source_name == "exact":
                item["score"] += 0.5

    ranked = sorted(
        combined.values(),
        key=lambda item: (
            -item["score"],
            "exact" not in item["sources"],
            item["hit"].locator or "",
            str(item["hit"].chunk_id),
        ),
    )
    sliced = ranked[offset : offset + limit]
    return [
        replace(item["hit"], score=item["score"], score_source="+".join(sorted(item["sources"])))
        for item in sliced
    ]


def _is_scoped_result_sufficient(result: RetrievalResult, *, locator_hint: str | None) -> bool:
    """Keep global fallback rare by accepting good scoped hits when they satisfy the explicit locator."""

    if not result.primary_hits:
        return False
    if locator_hint:
        return any(locator_hint.lower() in (hit.locator or "").lower() or locator_hint.lower() in (hit.locator_end or "").lower() for hit in result.primary_hits)
    return len(result.primary_hits) >= 1


def _load_secondary_hits(session: Session, *, primary_hits: list[RetrievalHit], limit: int) -> list[RetrievalHit]:
    """Use document references to bring in related normative documents as secondary evidence."""

    referenced_document_ids: set[UUID] = set()
    for hit in primary_hits:
        chunk_nodes = _fetch_chunk_nodes(session, hit)
        if not chunk_nodes:
            continue
        node_ids = [node.id for node in chunk_nodes]
        stmt = select(DocumentReference).where(
            DocumentReference.document_version_id == hit.document_version_id,
            DocumentReference.source_node_id.in_(node_ids),
            DocumentReference.matched_document_id.is_not(None),
        )
        referenced_document_ids.update(
            item.matched_document_id for item in session.execute(stmt).scalars().all() if item.matched_document_id is not None
        )

    referenced_document_ids.difference_update(hit.document_id for hit in primary_hits)
    if not referenced_document_ids:
        return []

    stmt = (
        _build_chunk_query(
            session,
            request=RetrievalRequest(query_text="", limit=limit, document_ids=list(referenced_document_ids)),
        )
        .order_by(Document.normalized_code.asc(), RetrievalChunk.chunk_index.asc())
        .limit(limit)
    )
    return [_row_to_hit(session, row, score_source="reference", score=0.25) for row in session.execute(stmt).all()]


def _build_chunk_query(session: Session, *, request: RetrievalRequest):
    """Build the common chunk query with document/version metadata filters."""

    stmt = (
        select(RetrievalChunk, Document, DocumentVersion)
        .join(Document, RetrievalChunk.document_id == Document.id)
        .join(DocumentVersion, RetrievalChunk.document_version_id == DocumentVersion.id)
    )
    if request.active_only:
        stmt = stmt.where(
            RetrievalChunk.is_active.is_(True),
            Document.status_normalized == StatusNormalized.ACTIVE,
            DocumentVersion.is_active.is_(True),
        )
    if request.document_type:
        stmt = stmt.where(Document.document_type == request.document_type)
    if request.document_ids:
        stmt = stmt.where(RetrievalChunk.document_id.in_(request.document_ids))
    return stmt


def _row_to_hit(session: Session, row: tuple[Any, ...], *, score_source: str, score: float) -> RetrievalHit:
    """Convert one SQL row into a normalized hit with reconstructed quote text."""

    chunk, document, version = row
    quote = _build_chunk_quote(session, chunk)
    freshness_status = FreshnessStatus.FRESH if version.is_active and document.current_version_id == version.id else FreshnessStatus.STALE
    return RetrievalHit(
        chunk_id=chunk.id,
        chunk_hash=chunk.chunk_hash,
        document_id=document.id,
        document_version_id=version.id,
        document_code=document.normalized_code,
        document_title=document.title,
        document_type=document.document_type,
        edition_label=version.edition_label,
        start_node_id=chunk.start_node_id,
        end_node_id=chunk.end_node_id,
        locator=chunk.locator,
        locator_end=chunk.locator_end,
        chunk_text=chunk.chunk_text,
        quote=quote,
        score=score,
        score_source=score_source,
        freshness_status=freshness_status,
    )


def _build_chunk_quote(session: Session, chunk: RetrievalChunk) -> str:
    """Reconstruct a precise quote from persisted document nodes for one chunk."""

    nodes = _fetch_chunk_nodes(
        session,
        RetrievalHit(
            chunk_id=chunk.id,
            chunk_hash=chunk.chunk_hash,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            document_code="",
            document_title=None,
            document_type=None,
            edition_label=None,
            start_node_id=chunk.start_node_id,
            end_node_id=chunk.end_node_id,
            locator=chunk.locator,
            locator_end=chunk.locator_end,
            chunk_text=chunk.chunk_text,
            quote="",
            score=0.0,
            score_source="internal",
            freshness_status=FreshnessStatus.UNKNOWN,
        ),
    )
    text = "\n".join(normalize_whitespace(" ".join(part for part in (node.label, node.title, node.text) if part)) for node in nodes)
    return text[:1000].strip()


def _fetch_chunk_nodes(session: Session, hit: RetrievalHit) -> list[DocumentNode]:
    """Load the node span covered by a chunk in document order."""

    start_node = session.get(DocumentNode, hit.start_node_id)
    end_node = session.get(DocumentNode, hit.end_node_id)
    if start_node is None or end_node is None:
        return []

    stmt = (
        select(DocumentNode)
        .where(
            DocumentNode.document_version_id == hit.document_version_id,
            DocumentNode.order_index >= start_node.order_index,
            DocumentNode.order_index <= end_node.order_index,
        )
        .order_by(DocumentNode.order_index.asc())
    )
    return list(session.execute(stmt).scalars().all())


def _split_query_for_locator(query_text: str) -> tuple[str, str | None]:
    """Split a code-like query into canonical code and optional locator suffix."""

    cleaned = clean_document_code(query_text)
    match = LOCATOR_QUERY_RE.match(cleaned)
    if not match:
        return normalize_document_code(cleaned), None
    return normalize_document_code(match.group("code")), match.group("locator")
