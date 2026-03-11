"""Drop legacy Stage 2 tables no longer used by Stage 2A."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260311_000003"
down_revision = "20260311_000002"
branch_labels = None
depends_on = None


LEGACY_TABLES = (
    "retrieval_chunks",
    "chunk_embeddings",
    "qa_sessions",
    "qa_queries",
    "qa_messages",
    "qa_subtasks",
    "qa_evidence",
    "qa_answers",
    "verification_reports",
    "tool_invocations",
    "search_events",
    "trusted_source_documents",
    "trusted_source_chunks",
    "trusted_source_sync_runs",
    "trusted_source_cache_entries",
    "freshness_checks",
    "audit_events",
    "security_events",
)


def upgrade() -> None:
    for table_name in LEGACY_TABLES:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))


def downgrade() -> None:
    # The dropped tables belong to a removed legacy runtime and are intentionally
    # not recreated as part of Stage 2A.
    return None
