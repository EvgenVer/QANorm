from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.stage2a.retrieval.engine import DocumentCandidate, RetrievalEngine, RetrievalHit
from qanorm.stage2a.retrieval.query_parser import ParsedQuery


def test_rerank_document_candidates_prefers_sp63_for_generic_reinforced_concrete_queries() -> None:
    retrieval = RetrievalEngine(MagicMock())
    active_version = type("Version", (), {"is_active": True, "is_outdated": False})()
    retrieval.document_versions.get = lambda version_id: active_version
    query = ParsedQuery(
        raw_text="\u0427\u0442\u043e \u043f\u043e \u0437\u0430\u0449\u0438\u0442\u043d\u043e\u043c\u0443 \u0441\u043b\u043e\u044e \u0430\u0440\u043c\u0430\u0442\u0443\u0440\u044b \u0432 \u0436\u0435\u043b\u0435\u0437\u043e\u0431\u0435\u0442\u043e\u043d\u043d\u044b\u0445 \u043a\u043e\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f\u0445?",
        normalized_text="\u0427\u0442\u043e \u043f\u043e \u0437\u0430\u0449\u0438\u0442\u043d\u043e\u043c\u0443 \u0441\u043b\u043e\u044e \u0430\u0440\u043c\u0430\u0442\u0443\u0440\u044b \u0432 \u0436\u0435\u043b\u0435\u0437\u043e\u0431\u0435\u0442\u043e\u043d\u043d\u044b\u0445 \u043a\u043e\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f\u0445?",
        explicit_document_codes=[],
        explicit_locator_values=[],
        lexical_query="\u0437\u0430\u0449\u0438\u0442\u043d\u044b\u0439 \u0441\u043b\u043e\u0439 \u0430\u0440\u043c\u0430\u0442\u0443\u0440\u044b \u0436\u0435\u043b\u0435\u0437\u043e\u0431\u0435\u0442\u043e\u043d",
        lexical_tokens=["\u0437\u0430\u0449\u0438\u0442", "\u0441\u043b\u043e\u0439", "\u0430\u0440\u043c\u0430\u0442\u0443\u0440", "\u0436\u0435\u043b\u0435\u0437\u043e\u0431\u0435\u0442\u043e\u043d"],
    )
    candidates = [
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=0.95,
            reason="lexical",
            matched_value="\u0421\u041f 351",
            display_code="\u0421\u041f 351.1325800.2017",
            title="Lightweight concrete structures",
        ),
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=0.93,
            reason="lexical",
            matched_value="\u0421\u041f 52",
            display_code="\u0421\u041f 52-101-2003",
            title="Concrete and reinforced concrete structures",
        ),
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=0.9,
            reason="lexical",
            matched_value="\u0421\u041f 63",
            display_code="\u0421\u041f 63.13330.2018",
            title="Concrete and reinforced concrete structures",
        ),
    ]

    reranked = retrieval._rerank_document_candidates(query, candidates)

    assert [item.display_code for item in reranked[:3]] == [
        "\u0421\u041f 63.13330.2018",
        "\u0421\u041f 351.1325800.2017",
        "\u0421\u041f 52-101-2003",
    ]


def test_merge_and_rerank_hits_prefers_contextual_retrieval_unit_over_weak_node_locator() -> None:
    retrieval = RetrievalEngine(MagicMock())
    document_id = uuid4()
    version_id = uuid4()

    locator_hit = RetrievalHit(
        source_kind="document_node_locator",
        score=0.5,
        document_id=document_id,
        document_version_id=version_id,
        node_id=uuid4(),
        retrieval_unit_id=None,
        order_index=2,
        locator="1.1",
        heading_path="Section 1",
        text="Requirement about loads.",
    )
    lexical_hit = RetrievalHit(
        source_kind="retrieval_unit_lexical",
        score=0.6,
        document_id=document_id,
        document_version_id=version_id,
        node_id=None,
        retrieval_unit_id=uuid4(),
        order_index=1,
        locator=None,
        heading_path="Section 1",
        text="Loads and actions.",
    )

    reranked = retrieval.merge_and_rerank_hits(
        locator_hits=[locator_hit],
        lexical_hits=[lexical_hit],
        dense_hits=[],
        explicit_locator_count=1,
    )

    assert reranked[0].source_kind == "retrieval_unit_lexical"


def test_merge_and_rerank_hits_prioritizes_retrieval_unit_context_over_document_node() -> None:
    retrieval = RetrievalEngine(MagicMock())
    document_id = uuid4()
    version_id = uuid4()

    node_hit = RetrievalHit(
        source_kind="document_node_locator",
        score=0.9,
        document_id=document_id,
        document_version_id=version_id,
        node_id=uuid4(),
        retrieval_unit_id=None,
        order_index=2,
        locator="5.1",
        heading_path="Section 5",
        text="One short line from a point.",
    )
    unit_hit = RetrievalHit(
        source_kind="retrieval_unit_context",
        score=0.92,
        document_id=document_id,
        document_version_id=version_id,
        node_id=uuid4(),
        retrieval_unit_id=uuid4(),
        order_index=1,
        locator="5.1",
        heading_path="Section 5 > 5.1",
        text="A larger contextual semantic block around the same locator.",
    )

    reranked = retrieval.merge_and_rerank_hits(
        locator_hits=[node_hit],
        lexical_hits=[unit_hit],
        dense_hits=[],
        explicit_locator_count=1,
    )

    assert reranked[0].source_kind == "retrieval_unit_context"


def test_rerank_document_candidates_prefers_latest_edition_when_query_has_no_year() -> None:
    retrieval = RetrievalEngine(MagicMock())
    latest_version = uuid4()
    old_version = uuid4()
    retrieval.document_versions.get = lambda version_id: type(
        "Version",
        (),
        {
            "is_active": version_id == latest_version,
            "is_outdated": version_id == old_version,
        },
    )()
    query = ParsedQuery(
        raw_text="Что СП 50 говорит про конденсацию влаги?",
        normalized_text="Что СП 50 говорит про конденсацию влаги?",
        explicit_document_codes=["СП 50"],
        explicit_locator_values=[],
        lexical_query="сп 50 конденсация влаги",
        lexical_tokens=["сп", "50", "конденсац", "влаг"],
    )
    candidates = [
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=old_version,
            score=1.0,
            reason="prefix_alias",
            matched_value="СП 50.13330.2012",
            display_code="СП 50.13330.2012",
            title="Тепловая защита зданий",
        ),
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=latest_version,
            score=0.96,
            reason="prefix_alias",
            matched_value="СП 50.13330.2024",
            display_code="СП 50.13330.2024",
            title="Тепловая защита зданий",
        ),
    ]

    reranked = retrieval._rerank_document_candidates(query, candidates)

    assert reranked[0].display_code == "СП 50.13330.2024"


def test_explicit_code_match_bonus_does_not_confuse_short_sp_family_with_longer_code() -> None:
    retrieval = RetrievalEngine(MagicMock())
    query = ParsedQuery(
        raw_text="Что СП 1 говорит про эвакуационные выходы?",
        normalized_text="Что СП 1 говорит про эвакуационные выходы?",
        explicit_document_codes=["СП 1"],
        explicit_locator_values=[],
        lexical_query="сп 1 эвакуационные выходы",
        lexical_tokens=["сп", "1", "эвакуацион", "выход"],
    )

    sp1_bonus = retrieval._explicit_code_match_bonus(
        query,
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=1.0,
            reason="prefix_alias",
            matched_value="СП 1",
            display_code="СП 1.13130.2020",
            title="Системы противопожарной защиты. Эвакуационные пути и выходы",
        ),
    )
    sp107_bonus = retrieval._explicit_code_match_bonus(
        query,
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=1.0,
            reason="prefix_alias",
            matched_value="СП 107",
            display_code="СП 107.13330.2012",
            title="Теплицы и парники",
        ),
    )

    assert sp1_bonus > 0
    assert sp107_bonus == 0


def test_resolve_document_uses_family_fallback_for_explicit_gost_query() -> None:
    retrieval = RetrievalEngine(MagicMock())
    candidate_document = type(
        "Document",
        (),
        {
            "id": uuid4(),
            "display_code": "ГОСТ 27751-2014",
            "title": "Надежность строительных конструкций и оснований",
            "current_version": type("Version", (), {"id": uuid4()})(),
        },
    )()
    retrieval.documents.get_by_normalized_code = lambda code: None
    retrieval.documents.list_all = lambda: [candidate_document]
    retrieval.documents.get = lambda document_id: candidate_document if document_id == candidate_document.id else None
    retrieval.document_aliases.list_by_alias_normalized = lambda value: []
    retrieval.document_aliases.list_by_alias_prefix = lambda value: []
    query = ParsedQuery(
        raw_text="Что ГОСТ 27751 говорит про предельные состояния?",
        normalized_text="Что ГОСТ 27751 говорит про предельные состояния?",
        explicit_document_codes=["ГОСТ 27751"],
        explicit_locator_values=[],
        lexical_query="гост 27751 предельные состояния",
        lexical_tokens=["гост", "27751", "предельн", "состоян"],
    )

    resolved = retrieval.resolve_document(query)

    assert resolved
    assert resolved[0].display_code == "ГОСТ 27751-2014"
    assert resolved[0].reason in {"explicit_family_fallback", "prefix_alias", "exact_alias"}


def test_resolve_document_does_not_match_short_prefix_into_longer_numeric_family() -> None:
    retrieval = RetrievalEngine(MagicMock())
    sp107_document = type(
        "Document",
        (),
        {
            "id": uuid4(),
            "display_code": "СП 107.13330.2012",
            "title": "Теплицы и парники",
            "current_version": type("Version", (), {"id": uuid4()})(),
        },
    )()
    alias = type(
        "Alias",
        (),
        {
            "document_id": sp107_document.id,
            "alias_raw": "СП 107",
            "alias_normalized": "сп 107",
            "confidence": 1.0,
        },
    )()
    retrieval.documents.get_by_normalized_code = lambda code: None
    retrieval.documents.get = lambda document_id: sp107_document if document_id == sp107_document.id else None
    retrieval.documents.list_all = lambda: [sp107_document]
    retrieval.document_aliases.list_by_alias_normalized = lambda value: []
    retrieval.document_aliases.list_by_alias_prefix = lambda value: [alias]
    query = ParsedQuery(
        raw_text="Что СП 1 требует по эвакуации?",
        normalized_text="Что СП 1 требует по эвакуации?",
        explicit_document_codes=["СП 1"],
        explicit_locator_values=[],
        lexical_query="сп 1 эвакуац",
        lexical_tokens=["сп", "1", "эвакуац"],
    )

    resolved = retrieval.resolve_document(query)

    assert resolved == []


def test_rerank_document_candidates_filters_placeholder_documents() -> None:
    retrieval = RetrievalEngine(MagicMock())
    query = ParsedQuery(
        raw_text="Что СП 1 говорит про эвакуационные выходы?",
        normalized_text="Что СП 1 говорит про эвакуационные выходы?",
        explicit_document_codes=["СП 1"],
        explicit_locator_values=[],
        lexical_query="сп 1 эвакуационные выходы",
        lexical_tokens=["сп", "1", "эвакуацион", "выход"],
    )
    candidates = [
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=1.0,
            reason="exact_alias",
            matched_value="SP 1",
            display_code="SP 1.0",
            title="SP 1.0",
        ),
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            score=0.9,
            reason="prefix_alias",
            matched_value="СП 4",
            display_code="СП 4.13130.2013",
            title="Системы противопожарной защиты",
        ),
    ]

    reranked = retrieval._rerank_document_candidates(query, candidates)

    assert [item.display_code for item in reranked] == ["СП 4.13130.2013"]
