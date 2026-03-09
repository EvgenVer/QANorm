"""Add retrieval chunk layer and deduplicated chunk embeddings."""

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

        def __init__(self, dimensions: int | None = None) -> None:
            self.dimensions = dimensions

        def get_col_spec(self, **_: object) -> str:
            return "VECTOR" if self.dimensions is None else f"VECTOR({self.dimensions})"


revision = "20260309_000004"
down_revision = "20260308_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("end_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_type", sa.String(length=100), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("locator", sa.Text(), nullable=True),
        sa.Column("locator_end", sa.Text(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("chunk_text_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["start_node_id"], ["document_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["end_node_id"], ["document_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", "chunk_index", name="uq_retrieval_chunks_version_chunk_index"),
    )
    op.create_index("ix_retrieval_chunks_document_id", "retrieval_chunks", ["document_id"], unique=False)
    op.create_index("ix_retrieval_chunks_document_version_id", "retrieval_chunks", ["document_version_id"], unique=False)
    op.create_index("ix_retrieval_chunks_start_node_id", "retrieval_chunks", ["start_node_id"], unique=False)
    op.create_index("ix_retrieval_chunks_end_node_id", "retrieval_chunks", ["end_node_id"], unique=False)
    op.create_index("ix_retrieval_chunks_chunk_hash", "retrieval_chunks", ["chunk_hash"], unique=False)
    op.create_index("ix_retrieval_chunks_is_active", "retrieval_chunks", ["is_active"], unique=False)
    op.create_index(
        "ix_retrieval_chunks_chunk_text_tsv",
        "retrieval_chunks",
        ["chunk_text_tsv"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("model_revision", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("chunk_text_sample", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chunk_hash",
            "model_provider",
            "model_name",
            "model_revision",
            name="uq_chunk_embeddings_hash_model",
        ),
    )
    op.create_index("ix_chunk_embeddings_chunk_hash", "chunk_embeddings", ["chunk_hash"], unique=False)
    op.create_index("ix_chunk_embeddings_model_provider", "chunk_embeddings", ["model_provider"], unique=False)
    op.create_index("ix_chunk_embeddings_model_name", "chunk_embeddings", ["model_name"], unique=False)
    op.create_index(
        "ix_chunk_embeddings_embedding",
        "chunk_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.add_column("qa_evidence", sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("qa_evidence", sa.Column("start_node_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("qa_evidence", sa.Column("end_node_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("qa_evidence", sa.Column("locator_end", sa.Text(), nullable=True))
    op.add_column("qa_evidence", sa.Column("chunk_text", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_qa_evidence_chunk_id_retrieval_chunks"), "qa_evidence", "retrieval_chunks", ["chunk_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_qa_evidence_start_node_id_document_nodes"), "qa_evidence", "document_nodes", ["start_node_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_qa_evidence_end_node_id_document_nodes"), "qa_evidence", "document_nodes", ["end_node_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_qa_evidence_chunk_id", "qa_evidence", ["chunk_id"], unique=False)
    op.create_index("ix_qa_evidence_start_node_id", "qa_evidence", ["start_node_id"], unique=False)
    op.create_index("ix_qa_evidence_end_node_id", "qa_evidence", ["end_node_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_qa_evidence_end_node_id", table_name="qa_evidence")
    op.drop_index("ix_qa_evidence_start_node_id", table_name="qa_evidence")
    op.drop_index("ix_qa_evidence_chunk_id", table_name="qa_evidence")
    op.drop_constraint(op.f("fk_qa_evidence_end_node_id_document_nodes"), "qa_evidence", type_="foreignkey")
    op.drop_constraint(op.f("fk_qa_evidence_start_node_id_document_nodes"), "qa_evidence", type_="foreignkey")
    op.drop_constraint(op.f("fk_qa_evidence_chunk_id_retrieval_chunks"), "qa_evidence", type_="foreignkey")
    op.drop_column("qa_evidence", "chunk_text")
    op.drop_column("qa_evidence", "locator_end")
    op.drop_column("qa_evidence", "end_node_id")
    op.drop_column("qa_evidence", "start_node_id")
    op.drop_column("qa_evidence", "chunk_id")

    op.drop_index("ix_chunk_embeddings_embedding", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_model_name", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_model_provider", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_chunk_hash", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")

    op.drop_index("ix_retrieval_chunks_chunk_text_tsv", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_is_active", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_chunk_hash", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_end_node_id", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_start_node_id", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_document_version_id", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_document_id", table_name="retrieval_chunks")
    op.drop_table("retrieval_chunks")
