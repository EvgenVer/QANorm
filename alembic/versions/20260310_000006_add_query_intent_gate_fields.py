"""Add intent-gate fields to qa_queries."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260310_000006"
down_revision = "20260310_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qa_queries", sa.Column("intent", sa.String(length=64), nullable=True))
    op.add_column(
        "qa_queries",
        sa.Column("clarification_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "qa_queries",
        sa.Column(
            "document_hints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "qa_queries",
        sa.Column(
            "locator_hints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("qa_queries", sa.Column("retrieval_mode", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("qa_queries", "retrieval_mode")
    op.drop_column("qa_queries", "locator_hints")
    op.drop_column("qa_queries", "document_hints")
    op.drop_column("qa_queries", "clarification_required")
    op.drop_column("qa_queries", "intent")
