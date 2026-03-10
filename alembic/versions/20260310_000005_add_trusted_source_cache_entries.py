"""Add trusted source cache entries table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260310_000005"
down_revision = "20260309_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_source_cache_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cache_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("source_domain", sa.String(length=255), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trusted_source_cache_entries")),
        sa.UniqueConstraint("cache_kind", "cache_key", name="uq_trusted_source_cache_entries_kind_key"),
    )
    op.create_index(
        "ix_trusted_source_cache_entries_source_id",
        "trusted_source_cache_entries",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        "ix_trusted_source_cache_entries_source_domain",
        "trusted_source_cache_entries",
        ["source_domain"],
        unique=False,
    )
    op.create_index(
        "ix_trusted_source_cache_entries_expires_at",
        "trusted_source_cache_entries",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_trusted_source_cache_entries_last_accessed_at",
        "trusted_source_cache_entries",
        ["last_accessed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_trusted_source_cache_entries_last_accessed_at", table_name="trusted_source_cache_entries")
    op.drop_index("ix_trusted_source_cache_entries_expires_at", table_name="trusted_source_cache_entries")
    op.drop_index("ix_trusted_source_cache_entries_source_domain", table_name="trusted_source_cache_entries")
    op.drop_index("ix_trusted_source_cache_entries_source_id", table_name="trusted_source_cache_entries")
    op.drop_table("trusted_source_cache_entries")
