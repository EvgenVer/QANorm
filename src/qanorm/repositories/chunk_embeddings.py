"""Repositories for deduplicated retrieval chunk embeddings."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.models import ChunkEmbedding


class ChunkEmbeddingRepository:
    """Data access helpers for deduplicated chunk embedding rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_many(self, embeddings: Iterable[ChunkEmbedding]) -> list[ChunkEmbedding]:
        """Insert multiple embedding rows in one flush."""

        items = list(embeddings)
        self.session.add_all(items)
        self.session.flush()
        return items

    def list_for_hashes(
        self,
        chunk_hashes: list[str],
        *,
        model_provider: str,
        model_name: str,
        model_revision: str = "",
    ) -> list[ChunkEmbedding]:
        """Return embeddings for the requested hashes and model identity."""

        if not chunk_hashes:
            return []
        stmt = (
            select(ChunkEmbedding)
            .where(
                ChunkEmbedding.chunk_hash.in_(chunk_hashes),
                ChunkEmbedding.model_provider == model_provider,
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.model_revision == model_revision,
            )
            .order_by(ChunkEmbedding.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())
