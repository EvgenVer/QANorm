"""Prepare Stage 1 schema for Stage 2A retrieval."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

try:
    from pgvector.sqlalchemy import Vector
except ModuleNotFoundError:
    class Vector(UserDefinedType):
        """Fallback VECTOR type for environments without pgvector installed."""

        cache_ok = True

        def __init__(self, dimensions: int) -> None:
            self.dimensions = dimensions

        def get_col_spec(self, **_: object) -> str:
            return f"VECTOR({self.dimensions})"


revision = "20260311_000002"
down_revision = "20260303_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias_raw", sa.Text(), nullable=False),
        sa.Column("alias_normalized", sa.String(length=255), nullable=False),
        sa.Column("alias_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_document_aliases_document_id_documents", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_aliases"),
        sa.UniqueConstraint("document_id", "alias_normalized", name="uq_document_aliases_document_id_alias_normalized"),
    )
    op.create_index("ix_document_aliases_document_id", "document_aliases", ["document_id"], unique=False)
    op.create_index("ix_document_aliases_alias_normalized", "document_aliases", ["alias_normalized"], unique=False)
    op.create_index("ix_document_aliases_alias_type", "document_aliases", ["alias_type"], unique=False)

    op.add_column("document_nodes", sa.Column("locator_raw", sa.Text(), nullable=True))
    op.add_column("document_nodes", sa.Column("locator_normalized", sa.Text(), nullable=True))
    op.add_column("document_nodes", sa.Column("heading_path", sa.Text(), nullable=True))
    op.create_index("ix_document_nodes_locator_normalized", "document_nodes", ["locator_normalized"], unique=False)

    op.create_table(
        "retrieval_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit_type", sa.String(length=50), nullable=False),
        sa.Column("anchor_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("start_order_index", sa.Integer(), nullable=True),
        sa.Column("end_order_index", sa.Integer(), nullable=True),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("locator_primary", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["anchor_node_id"],
            ["document_nodes.id"],
            name="fk_retrieval_units_anchor_node_id_document_nodes",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_retrieval_units_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_retrieval_units"),
    )
    op.create_index("ix_retrieval_units_document_version_id", "retrieval_units", ["document_version_id"], unique=False)
    op.create_index("ix_retrieval_units_unit_type", "retrieval_units", ["unit_type"], unique=False)
    op.create_index("ix_retrieval_units_anchor_node_id", "retrieval_units", ["anchor_node_id"], unique=False)
    op.create_index("ix_retrieval_units_chunk_hash", "retrieval_units", ["chunk_hash"], unique=False)
    op.create_index("ix_retrieval_units_text_tsv", "retrieval_units", ["text_tsv"], unique=False, postgresql_using="gin")
    op.create_index(
        "ix_retrieval_units_embedding",
        "retrieval_units",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_units_embedding", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_text_tsv", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_chunk_hash", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_anchor_node_id", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_unit_type", table_name="retrieval_units")
    op.drop_index("ix_retrieval_units_document_version_id", table_name="retrieval_units")
    op.drop_table("retrieval_units")

    op.drop_index("ix_document_nodes_locator_normalized", table_name="document_nodes")
    op.drop_column("document_nodes", "heading_path")
    op.drop_column("document_nodes", "locator_normalized")
    op.drop_column("document_nodes", "locator_raw")

    op.drop_index("ix_document_aliases_alias_type", table_name="document_aliases")
    op.drop_index("ix_document_aliases_alias_normalized", table_name="document_aliases")
    op.drop_index("ix_document_aliases_document_id", table_name="document_aliases")
    op.drop_table("document_aliases")
