from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from qanorm.cli.main import _build_alembic_config
from qanorm.db.types import StatusNormalized
from qanorm.models import Document, DocumentAlias, DocumentNode, DocumentVersion, RetrievalUnit
from qanorm.repositories import (
    DocumentAliasRepository,
    DocumentNodeRepository,
    DocumentRepository,
    DocumentVersionRepository,
    RetrievalUnitRepository,
)
from qanorm.settings import get_settings


@contextmanager
def _temporary_migrated_database(*, revision: str = "head") -> Iterator[str]:
    current_url = os.environ.get("QANORM_DB_URL") or str(get_settings().env.db_url)
    base_url = make_url(current_url)
    admin_url = base_url.set(database="postgres")
    database_name = f"qanorm_stage2a_{uuid4().hex}"
    test_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    previous_env_url = os.environ.get("QANORM_DB_URL")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))

        os.environ["QANORM_DB_URL"] = test_url
        get_settings.cache_clear()
        command.upgrade(_build_alembic_config(), revision)
        yield test_url
    finally:
        get_settings.cache_clear()
        if previous_env_url is None:
            os.environ.pop("QANORM_DB_URL", None)
        else:
            os.environ["QANORM_DB_URL"] = previous_env_url

        test_engine = create_engine(test_url, future=True)
        test_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


def test_401_integration_stage2a_migration_creates_retrieval_schema() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)
        inspector = inspect(engine)

        assert "document_aliases" in inspector.get_table_names()
        assert "retrieval_units" in inspector.get_table_names()

        document_node_columns = {column["name"] for column in inspector.get_columns("document_nodes")}
        assert {"locator_raw", "locator_normalized", "heading_path"} <= document_node_columns

        document_node_indexes = {index["name"] for index in inspector.get_indexes("document_nodes")}
        assert "ix_document_nodes_locator_normalized" in document_node_indexes

        alias_columns = {column["name"] for column in inspector.get_columns("document_aliases")}
        assert {"document_id", "alias_raw", "alias_normalized", "alias_type", "confidence"} <= alias_columns

        alias_uniques = {tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints("document_aliases")}
        assert ("document_id", "alias_normalized") in alias_uniques

        retrieval_columns = {column["name"] for column in inspector.get_columns("retrieval_units")}
        assert {
            "document_version_id",
            "unit_type",
            "anchor_node_id",
            "start_order_index",
            "end_order_index",
            "heading_path",
            "locator_primary",
            "text",
            "text_tsv",
            "embedding",
            "chunk_hash",
        } <= retrieval_columns

        engine.dispose()


def test_402_integration_stage2a_repositories_roundtrip_retrieval_entities() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)

        with Session(engine) as session:
            document = Document(
                normalized_code="SP-20.13330.2016",
                display_code="SP 20.13330.2016",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document = DocumentRepository(session).add(document)
            document_id = document.id

            version = DocumentVersion(
                document_id=document.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version = DocumentVersionRepository(session).add(version)
            version_id = version.id

            node = DocumentNode(
                document_version_id=version.id,
                node_type="point",
                label="1.1",
                title="Loads",
                text="Distributed loads shall be considered in design calculations.",
                locator_raw="cl. 1.1",
                locator_normalized="1.1",
                heading_path="Section 1 > Loads",
                order_index=1,
            )
            node = DocumentNodeRepository(session).add(node)
            node_id = node.id

            alias = DocumentAlias(
                document_id=document.id,
                alias_raw="SP 20",
                alias_normalized="sp 20",
                alias_type="short_code",
                confidence=0.9,
            )
            DocumentAliasRepository(session).add(alias)

            unit = RetrievalUnit(
                document_version_id=version.id,
                unit_type="semantic_block",
                anchor_node_id=node.id,
                start_order_index=1,
                end_order_index=1,
                heading_path=node.heading_path,
                locator_primary=node.locator_normalized,
                text=node.text,
                chunk_hash="1" * 64,
            )
            RetrievalUnitRepository(session).add(unit)
            session.commit()

        with Session(engine) as session:
            alias_repository = DocumentAliasRepository(session)
            unit_repository = RetrievalUnitRepository(session)
            node_repository = DocumentNodeRepository(session)

            resolved_aliases = alias_repository.list_by_alias_normalized("sp 20")
            assert len(resolved_aliases) == 1
            assert resolved_aliases[0].alias_type == "short_code"
            assert resolved_aliases[0].document_id == document_id

            units = unit_repository.list_for_document_version(version_id)
            assert len(units) == 1
            assert units[0].anchor_node_id == node_id
            assert units[0].locator_primary == "1.1"

            stored_node = node_repository.get(node_id)
            assert stored_node is not None
            assert stored_node.locator_raw == "cl. 1.1"
            assert stored_node.heading_path == "Section 1 > Loads"

        engine.dispose()


def test_403_integration_stage2a_cleanup_migration_drops_legacy_tables() -> None:
    legacy_tables = (
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

    with _temporary_migrated_database(revision="20260311_000002") as database_url:
        engine = create_engine(database_url, future=True)

        with engine.begin() as connection:
            for table_name in legacy_tables:
                connection.execute(text(f'CREATE TABLE "{table_name}" (id integer primary key)'))

        inspector = inspect(engine)
        assert set(legacy_tables) <= set(inspector.get_table_names())
        engine.dispose()

        previous_env_url = os.environ.get("QANORM_DB_URL")
        os.environ["QANORM_DB_URL"] = database_url
        get_settings.cache_clear()
        try:
            command.upgrade(_build_alembic_config(), "head")
        finally:
            get_settings.cache_clear()
            if previous_env_url is None:
                os.environ.pop("QANORM_DB_URL", None)
            else:
                os.environ["QANORM_DB_URL"] = previous_env_url

        engine = create_engine(database_url, future=True)
        inspector = inspect(engine)
        assert set(legacy_tables).isdisjoint(inspector.get_table_names())
        engine.dispose()
