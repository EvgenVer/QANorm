"""drop legacy document node embedding column"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import UserDefinedType


revision = "20260308_000003"
down_revision = "20260308_000002"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    """Drop the legacy node-level embedding column."""

    op.drop_column("document_nodes", "embedding")


def downgrade() -> None:
    """Restore the legacy node-level embedding column."""

    op.add_column("document_nodes", sa.Column("embedding", Vector(1536), nullable=True))
