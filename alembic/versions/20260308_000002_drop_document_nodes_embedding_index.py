"""drop legacy document node embedding index"""

from __future__ import annotations

from alembic import op


revision = "20260308_000002"
down_revision = "e3fe7afe0b4a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the legacy node-level vector index.

    Dense retrieval is moving to chunk-level storage, so keeping the HNSW index on
    `document_nodes.embedding` only wastes disk space and slows maintenance work.
    """

    op.drop_index("ix_document_nodes_embedding", table_name="document_nodes", if_exists=True)


def downgrade() -> None:
    """Restore the legacy node-level vector index if the migration is rolled back."""

    op.create_index(
        "ix_document_nodes_embedding",
        "document_nodes",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
