"""Synchronization, indexing, and retrieval for allowlisted trusted sources."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from qanorm.db.types import EvidenceSourceKind, SearchScope, SearchStatus, TrustedSourceSyncStatus
from qanorm.fetchers.trusted_sources import TrustedSourcePage, discover_trusted_source_urls, fetch_trusted_source_page
from qanorm.indexing.fts import build_text_tsv
from qanorm.models import QAEvidence, SearchEvent, TrustedSourceChunk, TrustedSourceDocument, TrustedSourceSyncRun
from qanorm.repositories import SearchEventRepository, TrustedSourceRepository
from qanorm.settings import TrustedSourceAdapterConfig
from qanorm.utils.text import normalize_whitespace


@dataclass(slots=True, frozen=True)
class TrustedSourceSearchHit:
    """One normalized hit from the local trusted-source corpus."""

    chunk_id: UUID
    document_id: UUID
    source_domain: str
    source_url: str
    title: str | None
    locator: str | None
    text: str
    score: float


@dataclass(slots=True, frozen=True)
class TrustedSourceSyncResult:
    """Summary of one trusted-source synchronization run."""

    sync_run_id: UUID
    source_domain: str
    discovered_url_count: int
    indexed_document_count: int


def chunk_trusted_source_text(
    text: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> list[str]:
    """Split a trusted-source document into compact overlapping chunks."""

    normalized = normalize_whitespace(text)
    if not normalized:
        return []

    if chunk_overlap_chars >= chunk_size_chars:
        raise ValueError("chunk_overlap_chars must be smaller than chunk_size_chars")

    chunks: list[str] = []
    step = max(1, chunk_size_chars - chunk_overlap_chars)
    cursor = 0
    while cursor < len(normalized):
        window = normalized[cursor : cursor + chunk_size_chars].strip()
        if not window:
            break
        if len(window) == chunk_size_chars and cursor + chunk_size_chars < len(normalized):
            last_break = max(window.rfind(". "), window.rfind("; "), window.rfind(" "))
            if last_break > max(0, chunk_size_chars // 2):
                window = window[: last_break + 1].strip()
        chunks.append(window)
        cursor += max(1, len(window) - chunk_overlap_chars)
    return chunks


def sync_trusted_source(
    session: Session,
    *,
    adapter: TrustedSourceAdapterConfig,
) -> TrustedSourceSyncResult:
    """Synchronize one allowlisted trusted domain into the local searchable store."""

    repository = TrustedSourceRepository(session)
    sync_run = repository.save_sync_run(
        TrustedSourceSyncRun(
            source_domain=adapter.domain,
            status=TrustedSourceSyncStatus.RUNNING,
            details_json={
                "sitemap_urls": list(adapter.sitemap_urls),
                "seed_urls": list(adapter.seed_urls),
            },
        )
    )
    discovered_urls = discover_trusted_source_urls(
        domain=adapter.domain,
        sitemap_urls=adapter.sitemap_urls,
        seed_urls=adapter.seed_urls,
        allowed_prefixes=adapter.allowed_prefixes,
    )
    indexed_documents = 0
    for url in discovered_urls[: adapter.max_documents_per_sync]:
        page = fetch_trusted_source_page(url)
        if not page.text:
            continue
        document = repository.save_document(_build_trusted_document(sync_run_id=sync_run.id, page=page))
        chunks = [
            TrustedSourceChunk(
                document_id=document.id,
                chunk_index=index,
                locator=f"chunk:{index + 1}",
                text=chunk_text,
                text_tsv=build_text_tsv(chunk_text),
            )
            for index, chunk_text in enumerate(
                chunk_trusted_source_text(
                    page.text,
                    chunk_size_chars=adapter.chunk_size_chars,
                    chunk_overlap_chars=adapter.chunk_overlap_chars,
                )
            )
        ]
        repository.replace_chunks(document.id, chunks)
        indexed_documents += 1

    sync_run.status = TrustedSourceSyncStatus.COMPLETED
    sync_run.documents_discovered = len(discovered_urls)
    sync_run.documents_indexed = indexed_documents
    sync_run.details_json = {
        **(sync_run.details_json or {}),
        "indexed_document_urls": discovered_urls[:indexed_documents],
    }
    session.flush()
    return TrustedSourceSyncResult(
        sync_run_id=sync_run.id,
        source_domain=adapter.domain,
        discovered_url_count=len(discovered_urls),
        indexed_document_count=indexed_documents,
    )


def search_trusted_sources(
    session: Session,
    *,
    query_id: UUID | None,
    subtask_id: UUID | None,
    query_text: str,
    allowed_domains: Iterable[str] | None = None,
    limit: int = 5,
) -> list[TrustedSourceSearchHit]:
    """Run a lexical lookup against indexed trusted-source chunks."""

    ts_query = func.plainto_tsquery("simple", query_text)
    rank = func.ts_rank_cd(TrustedSourceChunk.text_tsv, ts_query)
    stmt = (
        select(TrustedSourceChunk, TrustedSourceDocument, rank.label("score"))
        .join(TrustedSourceDocument, TrustedSourceChunk.document_id == TrustedSourceDocument.id)
        .where(TrustedSourceChunk.text_tsv.op("@@")(ts_query))
        .order_by(rank.desc(), TrustedSourceChunk.chunk_index.asc())
        .limit(limit)
    )
    domains = [item.strip() for item in (allowed_domains or []) if item.strip()]
    if domains:
        stmt = stmt.where(TrustedSourceDocument.source_domain.in_(domains))

    rows = session.execute(stmt).all()
    hits = [
        TrustedSourceSearchHit(
            chunk_id=chunk.id,
            document_id=document.id,
            source_domain=document.source_domain,
            source_url=document.source_url,
            title=document.title,
            locator=chunk.locator,
            text=chunk.text,
            score=float(score or 0.0),
        )
        for chunk, document, score in rows
    ]
    SearchEventRepository(session).add(
        SearchEvent(
            query_id=query_id,
            subtask_id=subtask_id,
            provider_name="trusted_sources",
            search_scope=SearchScope.TRUSTED_WEB,
            query_text=query_text,
            allowed_domains=domains or None,
            result_count=len(hits),
            status=SearchStatus.COMPLETED,
        )
    )
    return hits


def normalize_trusted_hits_to_evidence(
    *,
    query_id: UUID,
    hits: Iterable[TrustedSourceSearchHit],
    subtask_id: UUID | None = None,
) -> list[QAEvidence]:
    """Convert trusted-source hits into external evidence rows."""

    evidence_rows: list[QAEvidence] = []
    for hit in hits:
        evidence_rows.append(
            QAEvidence(
                query_id=query_id,
                subtask_id=subtask_id,
                source_kind=EvidenceSourceKind.TRUSTED_WEB,
                source_url=hit.source_url,
                source_domain=hit.source_domain,
                locator=hit.locator,
                quote=hit.text[:500],
                chunk_text=hit.text,
                relevance_score=hit.score,
                is_normative=False,
                requires_verification=True,
            )
        )
    return evidence_rows


def _build_trusted_document(*, sync_run_id: UUID, page: TrustedSourcePage) -> TrustedSourceDocument:
    """Build one stored trusted-source document from a fetched page."""

    content_hash = sha256(page.text.encode("utf-8")).hexdigest()
    return TrustedSourceDocument(
        last_sync_run_id=sync_run_id,
        source_domain=page.source_domain,
        source_url=page.url,
        title=page.title,
        content_hash=content_hash,
        published_at=page.published_at,
        metadata_json=page.metadata,
    )
