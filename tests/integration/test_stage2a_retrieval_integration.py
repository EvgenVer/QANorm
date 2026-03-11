from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qanorm.db.types import StatusNormalized
from qanorm.models import Document, DocumentNode, DocumentSource, DocumentVersion
from qanorm.repositories import DocumentNodeRepository, DocumentRepository, DocumentSourceRepository, DocumentVersionRepository
from qanorm.stage2a.indexing.backfill import rebuild_derived_retrieval_data
from qanorm.stage2a.retrieval import RetrievalEngine

from tests.integration.test_stage2a_schema_integration import _temporary_migrated_database


def test_501_integration_stage2a_retrieval_resolves_document_and_locator() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)

        with Session(engine) as session:
            document = Document(
                normalized_code="СП 20.13330.2016",
                display_code="СП 20.13330.2016",
                title="Нагрузки и воздействия",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document = DocumentRepository(session).add(document)

            version = DocumentVersion(
                document_id=document.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version = DocumentVersionRepository(session).add(version)
            document.current_version_id = version.id
            session.flush()

            DocumentSourceRepository(session).add(
                DocumentSource(
                    document_id=document.id,
                    document_version_id=version.id,
                    card_url="https://docs.example.test/cards/sp-20",
                )
            )
            title_node = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    node_type="title",
                    title=document.title,
                    text=document.title or "",
                    order_index=1,
                )
            )
            section_node = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=title_node.id,
                    node_type="section",
                    label="1",
                    title="Общие положения",
                    text="1 Общие положения",
                    order_index=2,
                )
            )
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=section_node.id,
                    node_type="point",
                    label="1.1",
                    title="Нагрузки",
                    text="Постоянные и временные нагрузки следует учитывать в расчетах.",
                    order_index=3,
                )
            )

            rebuild_derived_retrieval_data(session)
            session.commit()

        with Session(engine) as session:
            retrieval = RetrievalEngine(session)
            parsed = retrieval.parse_query("Что требует СП 20.13330.2016 по п. 1.1 для нагрузок?")
            resolved = retrieval.resolve_document(parsed)
            locator_hits = retrieval.lookup_locator(
                document_version_id=resolved[0].document_version_id,
                locator=parsed.explicit_locator_values[0],
            )

            assert resolved
            assert resolved[0].display_code == "СП 20.13330.2016"
            assert locator_hits
            assert locator_hits[0].locator == "1.1"
            assert "нагрузки" in locator_hits[0].text.lower()

        engine.dispose()


def test_502_integration_stage2a_retrieval_builds_evidence_pack_without_explicit_document() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)

        with Session(engine) as session:
            document = Document(
                normalized_code="СП 63.13330.2018",
                display_code="СП 63.13330.2018",
                title="Бетонные и железобетонные конструкции",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document = DocumentRepository(session).add(document)

            version = DocumentVersion(
                document_id=document.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version = DocumentVersionRepository(session).add(version)
            document.current_version_id = version.id
            session.flush()

            DocumentSourceRepository(session).add(
                DocumentSource(
                    document_id=document.id,
                    document_version_id=version.id,
                    card_url="https://docs.example.test/cards/sp-63",
                )
            )
            title_node = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    node_type="title",
                    title=document.title,
                    text=document.title or "",
                    order_index=1,
                )
            )
            section_node = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=title_node.id,
                    node_type="section",
                    label="8",
                    title="Армирование",
                    text="8 Армирование",
                    order_index=2,
                )
            )
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=section_node.id,
                    node_type="paragraph",
                    text="Минимальное армирование следует назначать по требованиям настоящего свода правил.",
                    order_index=3,
                )
            )

            rebuild_derived_retrieval_data(session)
            session.commit()

        with Session(engine) as session:
            retrieval = RetrievalEngine(session)
            evidence = retrieval.build_evidence_pack("Какое минимальное армирование требуется для железобетонных конструкций?")

            assert evidence
            assert any("армирование" in hit.text.lower() for hit in evidence)

        engine.dispose()
