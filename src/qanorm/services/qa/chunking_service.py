"""Chunk building and backfill helpers for normative retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.types import StatusNormalized
from qanorm.indexing.fts import build_text_tsv, tokenize_for_fts
from qanorm.models import DocumentNode, DocumentVersion, RetrievalChunk
from qanorm.normalizers.locators import build_node_locator
from qanorm.repositories import DocumentNodeRepository, RetrievalChunkRepository
from qanorm.utils.text import normalize_whitespace


HEADING_NODE_TYPES = {"title", "section", "subsection", "appendix"}
STANDALONE_NODE_TYPES = {"table", "note"}
# Retrieval chunks should be anchored at the main normative clause level.
# Subpoints stay attached to their parent point unless a later pass needs to split
# an oversized chunk, which keeps embedding volume lower and preserves context.
ANCHOR_NODE_TYPES = {"point", *STANDALONE_NODE_TYPES}


@dataclass(slots=True, frozen=True)
class ChunkingConfig:
    """Configurable chunk-size thresholds for deterministic chunk building."""

    min_tokens: int = 40
    max_tokens: int = 220


@dataclass(slots=True, frozen=True)
class RetrievalChunkDraft:
    """In-memory retrieval chunk built from one contiguous node group."""

    chunk_index: int
    start_node_id: UUID
    end_node_id: UUID
    chunk_type: str
    heading_path: str | None
    locator: str | None
    locator_end: str | None
    chunk_text: str
    chunk_hash: str
    char_count: int
    token_count: int


@dataclass(slots=True, frozen=True)
class ChunkBackfillResult:
    """Summary of chunk backfill for one or more active versions."""

    processed_version_count: int
    chunk_count: int


def build_retrieval_chunk_drafts(
    nodes: Iterable[DocumentNode],
    *,
    config: ChunkingConfig | None = None,
) -> list[RetrievalChunkDraft]:
    """Group normalized nodes into retrieval-oriented chunks with minimal overlap."""

    effective_config = config or ChunkingConfig()
    ordered_nodes = sorted(nodes, key=lambda item: item.order_index)
    if not ordered_nodes:
        return []

    locator_map = build_node_locator_map(ordered_nodes)
    heading_context: list[DocumentNode] = []
    buffered_nodes: list[DocumentNode] = []
    chunk_groups: list[tuple[list[DocumentNode], list[DocumentNode]]] = []

    def flush_buffer() -> None:
        if buffered_nodes:
            # Copy the current heading snapshot so later heading changes do not mutate old groups.
            chunk_groups.append((list(heading_context), list(buffered_nodes)))
            buffered_nodes.clear()

    for node in ordered_nodes:
        if node.node_type in HEADING_NODE_TYPES:
            flush_buffer()
            heading_context = _update_heading_context(heading_context, node)
            continue

        if node.node_type in ANCHOR_NODE_TYPES:
            flush_buffer()
            buffered_nodes.append(node)
            if node.node_type in STANDALONE_NODE_TYPES:
                flush_buffer()
            continue

        buffered_nodes.append(node)
        if _count_tokens(buffered_nodes) >= effective_config.max_tokens:
            flush_buffer()

    flush_buffer()
    merged_groups = _merge_short_groups(chunk_groups, config=effective_config)

    drafts: list[RetrievalChunkDraft] = []
    for chunk_index, (headings, group_nodes) in enumerate(merged_groups, start=1):
        chunk_text = _compose_chunk_text(group_nodes)
        drafts.append(
            RetrievalChunkDraft(
                chunk_index=chunk_index,
                start_node_id=group_nodes[0].id,
                end_node_id=group_nodes[-1].id,
                chunk_type=group_nodes[0].node_type,
                heading_path=_build_heading_path(headings),
                locator=locator_map.get(group_nodes[0].id),
                locator_end=locator_map.get(group_nodes[-1].id),
                chunk_text=chunk_text,
                chunk_hash=build_chunk_hash(chunk_text),
                char_count=len(chunk_text),
                token_count=len(tokenize_for_fts(chunk_text)),
            )
        )
    return drafts


def build_chunk_hash(chunk_text: str) -> str:
    """Create a stable deduplication key from normalized chunk text."""

    normalized_text = normalize_whitespace(chunk_text)
    return sha256(normalized_text.encode("utf-8")).hexdigest()


def build_node_locator_map(nodes: Iterable[DocumentNode]) -> dict[UUID, str]:
    """Reconstruct node locators from the persisted tree structure."""

    ordered_nodes = sorted(nodes, key=lambda item: item.order_index)
    locator_by_id: dict[UUID, str] = {}
    for node in ordered_nodes:
        parent_locator = None
        if node.parent_node_id is not None:
            parent_locator = locator_by_id.get(node.parent_node_id)
        locator_by_id[node.id] = build_node_locator(
            node_type=node.node_type,
            label=node.label,
            order_index=node.order_index,
            parent_locator=parent_locator,
        )
    return locator_by_id


def sync_retrieval_chunks_for_version(
    session: Session,
    *,
    document_version_id: UUID,
    config: ChunkingConfig | None = None,
) -> list[RetrievalChunk]:
    """Rebuild and persist retrieval chunks for one document version."""

    version = session.get(DocumentVersion, document_version_id)
    if version is None:
        raise ValueError(f"Document version not found: {document_version_id}")

    nodes = DocumentNodeRepository(session).list_for_document_version(document_version_id)
    drafts = build_retrieval_chunk_drafts(nodes, config=config)
    repository = RetrievalChunkRepository(session)
    repository.delete_for_document_version(document_version_id)
    chunk_models = [
        RetrievalChunk(
            document_id=version.document_id,
            document_version_id=document_version_id,
            start_node_id=draft.start_node_id,
            end_node_id=draft.end_node_id,
            chunk_index=draft.chunk_index,
            chunk_type=draft.chunk_type,
            heading_path=draft.heading_path,
            locator=draft.locator,
            locator_end=draft.locator_end,
            chunk_text=draft.chunk_text,
            chunk_text_tsv=build_text_tsv(draft.chunk_text),
            chunk_hash=draft.chunk_hash,
            char_count=draft.char_count,
            token_count=draft.token_count,
            is_active=version.is_active,
        )
        for draft in drafts
    ]
    if not chunk_models:
        return []
    return repository.add_many(chunk_models)


def backfill_active_retrieval_chunks(
    session: Session,
    *,
    config: ChunkingConfig | None = None,
) -> ChunkBackfillResult:
    """Build retrieval chunks for all active document versions in the normative corpus."""

    stmt = (
        select(DocumentVersion)
        .join(DocumentVersion.document)
        .where(
            DocumentVersion.is_active.is_(True),
            DocumentVersion.document.has(status_normalized=StatusNormalized.ACTIVE),
        )
        .order_by(DocumentVersion.created_at.asc())
    )
    versions = list(session.execute(stmt).scalars().all())

    chunk_count = 0
    for version in versions:
        chunk_count += len(sync_retrieval_chunks_for_version(session, document_version_id=version.id, config=config))
    return ChunkBackfillResult(processed_version_count=len(versions), chunk_count=chunk_count)


def _update_heading_context(current: list[DocumentNode], node: DocumentNode) -> list[DocumentNode]:
    """Maintain a compact heading stack while iterating the document in order."""

    if node.node_type == "title":
        return [node]
    if node.node_type == "section":
        return [item for item in current if item.node_type == "title"] + [node]
    if node.node_type == "subsection":
        preserved = [item for item in current if item.node_type in {"title", "section"}]
        return preserved + [node]
    if node.node_type == "appendix":
        return [item for item in current if item.node_type == "title"] + [node]
    return current + [node]


def _merge_short_groups(
    groups: list[tuple[list[DocumentNode], list[DocumentNode]]],
    *,
    config: ChunkingConfig,
) -> list[tuple[list[DocumentNode], list[DocumentNode]]]:
    """Merge short neighboring groups when doing so preserves the local context."""

    merged: list[tuple[list[DocumentNode], list[DocumentNode]]] = []
    for headings, nodes in groups:
        token_count = _count_tokens(nodes)
        if (
            merged
            and token_count < config.min_tokens
            and _build_heading_path(merged[-1][0]) == _build_heading_path(headings)
            and (_count_tokens(merged[-1][1]) + token_count) <= config.max_tokens
        ):
            merged[-1][1].extend(nodes)
            continue
        merged.append((headings, list(nodes)))
    return merged


def _compose_chunk_text(nodes: list[DocumentNode]) -> str:
    """Build stable chunk text from ordered document nodes."""

    parts = []
    for node in nodes:
        segment = " ".join(part for part in (node.label, node.title, node.text) if part)
        parts.append(normalize_whitespace(segment))
    return "\n".join(part for part in parts if part)


def _build_heading_path(headings: list[DocumentNode]) -> str | None:
    """Render the heading stack into a stable breadcrumb string."""

    parts = []
    for node in headings:
        rendered = " ".join(part for part in (node.label, node.title, node.text if node.node_type == "title" else None) if part)
        if rendered:
            parts.append(normalize_whitespace(rendered))
    return " > ".join(parts) if parts else None


def _count_tokens(nodes: list[DocumentNode]) -> int:
    """Estimate chunk size using the same tokenizer as the FTS layer."""

    return len(tokenize_for_fts(_compose_chunk_text(nodes)))
