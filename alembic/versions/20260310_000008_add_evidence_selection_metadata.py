"""Add evidence selection metadata for retrieval debugging.

Revision ID: 20260310_000008
Revises: 20260310_000007
Create Date: 2026-03-10 19:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260310_000008"
down_revision = "20260310_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist retrieval selection rationale directly on evidence rows."""

    op.add_column("qa_evidence", sa.Column("selection_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Remove retrieval selection metadata from evidence rows."""

    op.drop_column("qa_evidence", "selection_metadata")
