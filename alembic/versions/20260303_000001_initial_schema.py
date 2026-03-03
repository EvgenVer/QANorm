"""Initial schema."""

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


revision = "20260303_000001"
down_revision = None
branch_labels = None
depends_on = None


status_normalized_enum = sa.Enum("active", "inactive", "unknown", name="status_normalized_enum")
processing_status_enum = sa.Enum("pending", "downloaded", "extracted", "normalized", "indexed", "failed", name="processing_status_enum")
artifact_type_enum = sa.Enum("html_raw", "pdf_raw", "page_image", "ocr_raw", "parsed_text_snapshot", name="artifact_type_enum")
job_type_enum = sa.Enum(
    "crawl_seed",
    "parse_list_page",
    "process_document_card",
    "download_artifacts",
    "extract_text",
    "run_ocr",
    "normalize_document",
    "index_document",
    "refresh_document",
    name="job_type_enum",
)
job_status_enum = sa.Enum("pending", "running", "completed", "failed", name="job_status_enum")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    status_normalized_enum.create(bind, checkfirst=True)
    processing_status_enum.create(bind, checkfirst=True)
    artifact_type_enum.create(bind, checkfirst=True)
    job_type_enum.create(bind, checkfirst=True)
    job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_code", sa.String(length=255), nullable=False),
        sa.Column("display_code", sa.String(length=255), nullable=False),
        sa.Column("document_type", sa.String(length=100), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status_normalized", status_normalized_enum, nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("normalized_code", name="uq_documents_normalized_code"),
    )
    op.create_index("ix_documents_document_type", "documents", ["document_type"], unique=False)
    op.create_index("ix_documents_status_normalized", "documents", ["status_normalized"], unique=False)

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edition_label", sa.String(length=255), nullable=True),
        sa.Column("source_status_raw", sa.String(length=255), nullable=True),
        sa.Column("status_normalized", status_normalized_enum, nullable=False),
        sa.Column("text_actualized_at", sa.Date(), nullable=True),
        sa.Column("description_actualized_at", sa.Date(), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_outdated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("parse_confidence", sa.Float(), nullable=True),
        sa.Column("has_ocr", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("processing_status", processing_status_enum, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_document_versions_document_id_documents", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"], unique=False)
    op.create_index("ix_document_versions_is_active", "document_versions", ["is_active"], unique=False)
    op.create_index("ix_document_versions_status_normalized", "document_versions", ["status_normalized"], unique=False)
    op.create_index("ix_document_versions_content_hash", "document_versions", ["content_hash"], unique=False)
    op.create_foreign_key(
        "fk_documents_current_version_id_document_versions",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "document_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seed_url", sa.Text(), nullable=True),
        sa.Column("list_page_url", sa.Text(), nullable=True),
        sa.Column("card_url", sa.Text(), nullable=False),
        sa.Column("html_url", sa.Text(), nullable=True),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("print_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=100), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_document_sources_document_id_documents", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], name="fk_document_sources_document_version_id_document_versions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_sources"),
    )

    op.create_table(
        "raw_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", artifact_type_enum, nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], name="fk_raw_artifacts_document_version_id_document_versions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_raw_artifacts"),
    )

    op.create_table(
        "document_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_type", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("parse_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], name="fk_document_nodes_document_version_id_document_versions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_node_id"], ["document_nodes.id"], name="fk_document_nodes_parent_node_id_document_nodes", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_nodes"),
    )
    op.create_index("ix_document_nodes_document_version_id", "document_nodes", ["document_version_id"], unique=False)
    op.create_index("ix_document_nodes_parent_node_id", "document_nodes", ["parent_node_id"], unique=False)
    op.create_index("ix_document_nodes_text_tsv", "document_nodes", ["text_tsv"], unique=False, postgresql_using="gin")
    op.create_index("ix_document_nodes_embedding", "document_nodes", ["embedding"], unique=False, postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})

    op.create_table(
        "document_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_text", sa.Text(), nullable=False),
        sa.Column("referenced_code_normalized", sa.String(length=255), nullable=False),
        sa.Column("matched_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], name="fk_document_references_document_version_id_document_versions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_document_id"], ["documents.id"], name="fk_document_references_matched_document_id_documents", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_node_id"], ["document_nodes.id"], name="fk_document_references_source_node_id_document_nodes", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_references"),
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", job_type_enum, nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", job_status_enum, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
    )
    op.create_index("ix_ingestion_jobs_status", "ingestion_jobs", ["status"], unique=False)
    op.create_index("ix_ingestion_jobs_job_type", "ingestion_jobs", ["job_type"], unique=False)
    op.create_index("ix_ingestion_jobs_scheduled_at", "ingestion_jobs", ["scheduled_at"], unique=False)

    op.create_table(
        "update_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("old_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("update_reason", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=100), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_update_events_document_id_documents", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["new_version_id"], ["document_versions.id"], name="fk_update_events_new_version_id_document_versions", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["old_version_id"], ["document_versions.id"], name="fk_update_events_old_version_id_document_versions", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_update_events"),
    )


def downgrade() -> None:
    op.drop_table("update_events")
    op.drop_index("ix_ingestion_jobs_scheduled_at", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_job_type", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_status", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_table("document_references")
    op.drop_index("ix_document_nodes_embedding", table_name="document_nodes")
    op.drop_index("ix_document_nodes_text_tsv", table_name="document_nodes")
    op.drop_index("ix_document_nodes_parent_node_id", table_name="document_nodes")
    op.drop_index("ix_document_nodes_document_version_id", table_name="document_nodes")
    op.drop_table("document_nodes")
    op.drop_table("raw_artifacts")
    op.drop_table("document_sources")
    op.drop_constraint("fk_documents_current_version_id_document_versions", "documents", type_="foreignkey")
    op.drop_index("ix_document_versions_content_hash", table_name="document_versions")
    op.drop_index("ix_document_versions_status_normalized", table_name="document_versions")
    op.drop_index("ix_document_versions_is_active", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_status_normalized", table_name="documents")
    op.drop_index("ix_documents_document_type", table_name="documents")
    op.drop_table("documents")

    bind = op.get_bind()
    job_status_enum.drop(bind, checkfirst=True)
    job_type_enum.drop(bind, checkfirst=True)
    artifact_type_enum.drop(bind, checkfirst=True)
    processing_status_enum.drop(bind, checkfirst=True)
    status_normalized_enum.drop(bind, checkfirst=True)
