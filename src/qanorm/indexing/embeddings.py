"""Embedding helpers."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from qanorm.db.types import EMBEDDING_DIMENSIONS
from qanorm.indexing.fts import tokenize_for_fts


def get_text_embedding(
    text: str,
    *,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Build a deterministic local embedding vector for text."""

    if dimensions <= 0:
        raise ValueError("Embedding dimensions must be greater than zero")

    vector = [0.0] * dimensions
    tokens = tokenize_for_fts(text)
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (digest[5] / 255.0)
        vector[index] += sign * weight

    return _normalize_vector(vector)


def batch_get_text_embeddings(
    texts: Sequence[str],
    *,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[list[float]]:
    """Vectorize a batch of texts."""

    return [get_text_embedding(text, dimensions=dimensions) for text in texts]


def update_nodes_embeddings(nodes: Sequence[object], texts: Sequence[str] | None = None) -> int:
    """Assign embeddings to nodes in batch order."""

    node_list = list(nodes)
    text_list = list(texts) if texts is not None else [_compose_node_text(node) for node in node_list]
    embeddings = batch_get_text_embeddings(text_list)
    for node, embedding in zip(node_list, embeddings, strict=False):
        node.embedding = embedding
    return len(node_list)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute cosine similarity for two vectors."""

    if len(left) != len(right):
        raise ValueError("Vectors must have the same dimensionality")

    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def search_nodes_by_vector_similarity(
    nodes: Sequence[object],
    query: str,
    *,
    limit: int = 10,
) -> list[object]:
    """Rank nodes by cosine similarity to the query embedding."""

    query_embedding = get_text_embedding(query)
    ranked: list[tuple[float, int, object]] = []
    for node in nodes:
        embedding = getattr(node, "embedding", None) or get_text_embedding(_compose_node_text(node))
        score = cosine_similarity(query_embedding, embedding)
        if score <= 0:
            continue
        ranked.append((score, getattr(node, "order_index", 0), node))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:limit]]


def _normalize_vector(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0.0:
        return vector
    return [value / magnitude for value in vector]


def _compose_node_text(node: object) -> str:
    parts = [getattr(node, "label", None), getattr(node, "title", None), getattr(node, "text", None)]
    return " ".join(str(part) for part in parts if part)
