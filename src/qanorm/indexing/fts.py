"""Full-text indexing helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence

from qanorm.models import DocumentNode
from qanorm.utils.text import normalize_whitespace


_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)


def tokenize_for_fts(text: str) -> list[str]:
    """Normalize text into a compact list of FTS tokens."""

    normalized_text = normalize_whitespace(text).lower()
    tokens = [match.group(0) for match in _TOKEN_RE.finditer(normalized_text)]
    return [token for token in tokens if len(token) > 1 or token.isdigit()]


def build_text_tsv(text: str) -> str:
    """Build a deterministic tsv-like lexical payload for one text."""

    tokens = tokenize_for_fts(text)
    seen: set[str] = set()
    ordered_tokens: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered_tokens.append(token)
    return " ".join(ordered_tokens)


def update_nodes_full_text_index(nodes: Sequence[DocumentNode]) -> int:
    """Update the full-text payload for a collection of nodes."""

    for node in nodes:
        node.text_tsv = build_text_tsv(_compose_node_text(node))
    return len(nodes)


def search_nodes_by_fts(
    nodes: Sequence[DocumentNode],
    query: str,
    *,
    limit: int = 10,
) -> list[DocumentNode]:
    """Rank nodes by simple lexical overlap against a query."""

    query_tokens = set(tokenize_for_fts(query))
    if not query_tokens:
        return []

    ranked: list[tuple[int, int, DocumentNode]] = []
    for node in nodes:
        haystack = node.text_tsv or build_text_tsv(_compose_node_text(node))
        node_tokens = set(haystack.split())
        score = len(query_tokens & node_tokens)
        if score <= 0:
            continue
        ranked.append((score, node.order_index, node))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:limit]]


def _compose_node_text(node: DocumentNode) -> str:
    return " ".join(part for part in (node.label, node.title, node.text) if part)
