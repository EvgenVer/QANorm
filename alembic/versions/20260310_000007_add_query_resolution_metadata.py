"""Add query resolution metadata and normative search scope."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260310_000007"
down_revision = "20260310_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qa_queries", sa.Column("document_resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute("ALTER TYPE search_scope_enum ADD VALUE IF NOT EXISTS 'normative'")


def downgrade() -> None:
    op.drop_column("qa_queries", "document_resolution")
