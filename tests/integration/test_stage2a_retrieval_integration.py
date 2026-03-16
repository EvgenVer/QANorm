from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qanorm.db.types import EMBEDDING_DIMENSIONS, StatusNormalized
from qanorm.models import Document, DocumentNode, DocumentSource, DocumentVersion, RetrievalUnit
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
            version_id = version.id
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
            version_id = version.id
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


def test_503_integration_stage2a_retrieval_uses_dense_hits_when_embeddings_ready() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)

        with Session(engine) as session:
            document = Document(
                normalized_code="СП 50.13330.2012",
                display_code="СП 50.13330.2012",
                title="Тепловая защита зданий",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document = DocumentRepository(session).add(document)

            version = DocumentVersion(
                document_id=document.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version = DocumentVersionRepository(session).add(version)
            version_id = version.id
            document.current_version_id = version.id
            session.flush()

            DocumentSourceRepository(session).add(
                DocumentSource(
                    document_id=document.id,
                    document_version_id=version.id,
                    card_url="https://docs.example.test/cards/sp-50",
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
                    label="5",
                    title="Теплоизоляция",
                    text="5 Теплоизоляция",
                    order_index=2,
                )
            )
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=section_node.id,
                    node_type="paragraph",
                    text="Толщина теплоизоляции наружных стен должна обеспечивать требуемое сопротивление теплопередаче.",
                    order_index=3,
                )
            )

            rebuild_derived_retrieval_data(session)
            session.commit()

        with Session(engine) as session:
            semantic_unit = (
                session.query(RetrievalUnit)
                .filter(
                    RetrievalUnit.document_version_id == version_id,
                    RetrievalUnit.unit_type == "semantic_block",
                )
                .one()
            )
            semantic_unit.embedding = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
            session.commit()

        with Session(engine) as session:
            retrieval = RetrievalEngine(
                session,
                query_embedding_fn=lambda _: [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
            )
            hits = retrieval.search_semantic(
                "Какая толщина теплоизоляции нужна для наружных стен?",
                document_version_ids=[version_id],
                unit_types=["semantic_block"],
            )

            assert hits
            assert hits[0].source_kind == "retrieval_unit_dense"
            assert "теплоизоляции" in hits[0].text.lower()

        engine.dispose()


def test_504_integration_stage2a_resolve_document_handles_compact_gost_alias() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)

        with Session(engine) as session:
            document = Document(
                normalized_code="ГОСТ 27751-2014",
                display_code="ГОСТ 27751-2014",
                title="Надежность строительных конструкций и оснований",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document = DocumentRepository(session).add(document)

            version = DocumentVersion(
                document_id=document.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version = DocumentVersionRepository(session).add(version)
            version_id = version.id
            document.current_version_id = version.id
            session.flush()

            DocumentSourceRepository(session).add(
                DocumentSource(
                    document_id=document.id,
                    document_version_id=version.id,
                    card_url="https://docs.example.test/cards/gost-27751",
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
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=title_node.id,
                    node_type="paragraph",
                    text="Требования по надежности и сочетаниям воздействий устанавливаются настоящим стандартом.",
                    order_index=2,
                )
            )
            rebuild_derived_retrieval_data(session)
            session.commit()

        with Session(engine) as session:
            retrieval = RetrievalEngine(session)
            parsed = retrieval.parse_query("Что ГОСТ27751 говорит про надежность?")
            resolved = retrieval.resolve_document(parsed)

            assert resolved
            assert resolved[0].display_code == "ГОСТ 27751-2014"

        engine.dispose()


def test_505_integration_stage2a_lookup_locator_matches_prefix_and_prefers_contextual_unit() -> None:
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
            version_id = version.id
            document.current_version_id = version.id
            session.flush()

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
                    label="10",
                    title="Конструирование элементов",
                    text="10 Конструирование элементов",
                    order_index=2,
                )
            )
            subsection_node = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=section_node.id,
                    node_type="subsection",
                    label="10.3",
                    title="Плиты",
                    text="10.3 Плиты",
                    order_index=3,
                )
            )
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version.id,
                    parent_node_id=subsection_node.id,
                    node_type="point",
                    label="10.3.8",
                    title="Шаг арматуры",
                    text="В плитах высотой до 150 мм шаг арматуры должен быть не более 200 мм.",
                    order_index=4,
                )
            )
            rebuild_derived_retrieval_data(session)
            session.commit()

        with Session(engine) as session:
            retrieval = RetrievalEngine(session)
            hits = retrieval.lookup_locator(document_version_id=version_id, locator="10.3")

            assert hits
            assert any(hit.source_kind.startswith("retrieval_unit_locator") for hit in hits)
            assert any(hit.locator == "10.3.8" for hit in hits)

        engine.dispose()


def test_506_integration_stage2a_explicit_document_query_scopes_to_top_resolved_version() -> None:
    with _temporary_migrated_database() as database_url:
        engine = create_engine(database_url, future=True)

        with Session(engine) as session:
            document_2012 = Document(
                normalized_code="СП 50.13330.2012",
                display_code="СП 50.13330.2012",
                title="Тепловая защита зданий",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document_2012 = DocumentRepository(session).add(document_2012)
            version_2012 = DocumentVersion(
                document_id=document_2012.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version_2012 = DocumentVersionRepository(session).add(version_2012)
            document_2012.current_version_id = version_2012.id
            session.flush()

            title_2012 = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version_2012.id,
                    node_type="title",
                    title=document_2012.title,
                    text=document_2012.title or "",
                    order_index=1,
                )
            )
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version_2012.id,
                    parent_node_id=title_2012.id,
                    node_type="paragraph",
                    text="Сопротивление теплопередаче наружных стен определяется расчетом по настоящему своду правил.",
                    order_index=2,
                )
            )

            document_2024 = Document(
                normalized_code="СП 50.13330.2024",
                display_code="СП 50.13330.2024",
                title="Тепловая защита зданий",
                status_normalized=StatusNormalized.ACTIVE,
            )
            document_2024 = DocumentRepository(session).add(document_2024)
            version_2024 = DocumentVersion(
                document_id=document_2024.id,
                status_normalized=StatusNormalized.ACTIVE,
                is_active=True,
            )
            version_2024 = DocumentVersionRepository(session).add(version_2024)
            document_2024.current_version_id = version_2024.id
            session.flush()

            title_2024 = DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version_2024.id,
                    node_type="title",
                    title=document_2024.title,
                    text=document_2024.title or "",
                    order_index=1,
                )
            )
            DocumentNodeRepository(session).add(
                DocumentNode(
                    document_version_id=version_2024.id,
                    parent_node_id=title_2024.id,
                    node_type="paragraph",
                    text="Наружные стены следует рассчитывать с учетом новых теплотехнических показателей.",
                    order_index=2,
                )
            )

            rebuild_derived_retrieval_data(session)
            session.commit()

        with Session(engine) as session:
            retrieval = RetrievalEngine(session)
            evidence = retrieval.build_evidence_pack(
                "Что в СП 50.13330.2012 сказано про сопротивление теплопередаче наружных стен?"
            )

            assert evidence
            assert all(item.document_display_code == "СП 50.13330.2012" for item in evidence)

        engine.dispose()
