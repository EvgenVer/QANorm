"""Builders for Stage 2A retrieval units."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
from itertools import takewhile

from qanorm.indexing.fts import build_text_tsv
from qanorm.models import Document, DocumentAlias, DocumentNode, DocumentVersion, RetrievalUnit
from qanorm.normalizers.locators import normalize_locator_value
from qanorm.stage2a.config import Stage2AIndexingConfig
from qanorm.utils.text import normalize_whitespace


_HEADING_NODE_TYPES = {"title", "section", "subsection", "point", "subpoint", "appendix", "table", "note"}
_MAJOR_BREAK_NODE_TYPES = {"section", "appendix", "table"}


@dataclass(frozen=True, slots=True)
class RetrievalUnitBuildResult:
    """Result of deterministic retrieval-unit building for one document version."""

    document_card: RetrievalUnit
    semantic_blocks: list[RetrievalUnit]


def enrich_document_nodes(nodes: Sequence[DocumentNode]) -> int:
    """Populate locator and heading metadata for existing node rows."""

    node_list = sorted(nodes, key=lambda item: item.order_index)
    nodes_by_id = {node.id: node for node in node_list}
    updated_count = 0

    for node in node_list:
        locator_raw = node.locator_raw or node.label
        locator_normalized = normalize_locator_value(locator_raw)
        heading_path = _build_heading_path(node, nodes_by_id)

        if node.locator_raw != locator_raw:
            node.locator_raw = locator_raw
            updated_count += 1
        if node.locator_normalized != locator_normalized:
            node.locator_normalized = locator_normalized
            updated_count += 1
        if node.heading_path != heading_path:
            node.heading_path = heading_path
            updated_count += 1

    return updated_count


def build_retrieval_units(
    document: Document,
    version: DocumentVersion,
    *,
    nodes: Sequence[DocumentNode],
    aliases: Sequence[DocumentAlias],
    config: Stage2AIndexingConfig,
) -> RetrievalUnitBuildResult:
    """Build document-card and semantic-block retrieval units."""

    document_card = build_document_card_unit(document, version, nodes=nodes, aliases=aliases, config=config)
    semantic_blocks = build_semantic_block_units(version, nodes=nodes, config=config)
    return RetrievalUnitBuildResult(document_card=document_card, semantic_blocks=semantic_blocks)


def build_document_card_unit(
    document: Document,
    version: DocumentVersion,
    *,
    nodes: Sequence[DocumentNode],
    aliases: Sequence[DocumentAlias],
    config: Stage2AIndexingConfig,
) -> RetrievalUnit:
    """Build one document-card retrieval unit for document discovery."""

    heading_candidates = [
        _build_heading_label(node)
        for node in sorted(nodes, key=lambda item: item.order_index)
        if node.node_type in _HEADING_NODE_TYPES and node.node_type != "title"
    ]
    headings = [item for item in heading_candidates if item][: config.document_card_max_headings]
    scope_fragments = [
        normalize_whitespace(node.text)
        for node in sorted(nodes, key=lambda item: item.order_index)
        if node.node_type not in {"title", "section"} and normalize_whitespace(node.text)
    ][:3]
    alias_values = [alias.alias_raw for alias in aliases if alias.alias_type not in {"card_url", "html_url", "pdf_url", "print_url"}][:12]

    text_sections = [
        f"Код документа: {document.display_code}",
        f"Канонический код: {document.normalized_code}",
    ]
    if document.title:
        text_sections.append(f"Название: {normalize_whitespace(document.title)}")
    if alias_values:
        text_sections.append(f"Алиасы: {', '.join(alias_values)}")
    if headings:
        text_sections.append(f"Ключевые заголовки: {' | '.join(headings)}")
    if scope_fragments:
        text_sections.append(f"Фрагменты содержания: {' '.join(scope_fragments)}")

    text = "\n".join(text_sections)
    return RetrievalUnit(
        document_version_id=version.id,
        unit_type="document_card",
        anchor_node_id=nodes[0].id if nodes else None,
        start_order_index=nodes[0].order_index if nodes else None,
        end_order_index=nodes[-1].order_index if nodes else None,
        heading_path=document.title or document.display_code,
        locator_primary=document.normalized_code,
        text=text,
        text_tsv=build_text_tsv(text),
        chunk_hash=_build_chunk_hash(version.id, "document_card", text, nodes[:1]),
    )


def build_semantic_block_units(
    version: DocumentVersion,
    *,
    nodes: Sequence[DocumentNode],
    config: Stage2AIndexingConfig,
) -> list[RetrievalUnit]:
    """Build semantic blocks from neighboring document nodes."""

    ordered_nodes = [node for node in sorted(nodes, key=lambda item: item.order_index) if normalize_whitespace(node.text)]
    if not ordered_nodes:
        return []

    blocks: list[list[DocumentNode]] = []
    current: list[DocumentNode] = []

    for node in ordered_nodes:
        if current and _should_flush_block(current, node=node, config=config):
            blocks.append(current)
            current = []
        current.append(node)
        if _block_char_length(current) >= config.semantic_block_target_chars or len(current) >= config.semantic_block_max_nodes:
            blocks.append(current)
            current = []

    if current:
        blocks.append(current)

    return [_build_semantic_block_unit(version.id, block) for block in blocks if block]


def _should_flush_block(current: Sequence[DocumentNode], *, node: DocumentNode, config: Stage2AIndexingConfig) -> bool:
    current_chars = _block_char_length(current)
    if current_chars >= config.semantic_block_min_chars:
        if len(current) >= config.semantic_block_max_nodes:
            return True
        if current_chars >= config.semantic_block_max_chars:
            return True
        if _major_context_key(current[-1]) != _major_context_key(node):
            return True
        if node.node_type in _MAJOR_BREAK_NODE_TYPES:
            return True

    projected_chars = current_chars + len(_render_node_text(node)) + 1
    return projected_chars > config.semantic_block_max_chars


def _build_semantic_block_unit(document_version_id, nodes: Sequence[DocumentNode]) -> RetrievalUnit:
    text = "\n".join(_render_node_text(node) for node in nodes)
    common_heading_path = _common_heading_path(nodes)
    locator_node = next((node for node in reversed(nodes) if node.locator_normalized), None)
    locator_primary = locator_node.locator_normalized if locator_node is not None else None
    anchor_node = locator_node or next((node for node in nodes if node.node_type in _HEADING_NODE_TYPES), nodes[0])
    heading_path = anchor_node.heading_path or common_heading_path

    return RetrievalUnit(
        document_version_id=document_version_id,
        unit_type="semantic_block",
        anchor_node_id=anchor_node.id,
        start_order_index=nodes[0].order_index,
        end_order_index=nodes[-1].order_index,
        heading_path=heading_path,
        locator_primary=locator_primary,
        text=text,
        text_tsv=build_text_tsv(text),
        chunk_hash=_build_chunk_hash(document_version_id, "semantic_block", text, nodes),
    )


def _build_heading_path(node: DocumentNode, nodes_by_id: dict) -> str | None:
    parts: list[str] = []
    current = node
    while current is not None:
        label = _build_heading_label(current)
        if label:
            parts.append(label)
        current = nodes_by_id.get(current.parent_node_id)
    if not parts:
        return None
    parts.reverse()
    return " > ".join(parts)


def _build_heading_label(node: DocumentNode) -> str | None:
    if node.node_type == "paragraph":
        return None
    if node.title and node.label:
        return normalize_whitespace(f"{node.label} {node.title}")
    if node.title:
        return normalize_whitespace(node.title)
    if node.label:
        return normalize_whitespace(node.label)
    if node.node_type == "title":
        return normalize_whitespace(node.text)
    return None


def _render_node_text(node: DocumentNode) -> str:
    prefix_parts = [part for part in (_build_heading_label(node), node.locator_normalized) if part]
    prefix = " | ".join(dict.fromkeys(prefix_parts))
    body = normalize_whitespace(node.text)
    if not prefix:
        return body
    if body.casefold() == prefix.casefold():
        return body
    return f"{prefix}\n{body}"


def _common_heading_path(nodes: Sequence[DocumentNode]) -> str | None:
    parts_lists = [tuple((node.heading_path or "").split(" > ")) for node in nodes if node.heading_path]
    if not parts_lists:
        return None
    common_parts = list(takewhile(lambda pair: len(set(pair)) == 1, zip(*parts_lists)))
    if not common_parts:
        return nodes[0].heading_path
    return " > ".join(part[0] for part in common_parts)


def _major_context_key(node: DocumentNode) -> tuple[str, ...]:
    if not node.heading_path:
        return ()
    parts = tuple(part for part in node.heading_path.split(" > ") if part)
    return parts[:2]


def _build_chunk_hash(document_version_id, unit_type: str, text: str, nodes: Iterable[DocumentNode]) -> str:
    payload = "|".join(
        [
            str(document_version_id),
            unit_type,
            ",".join(str(node.order_index) for node in nodes),
            normalize_whitespace(text),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _block_char_length(nodes: Sequence[DocumentNode]) -> int:
    return sum(len(_render_node_text(node)) for node in nodes)
