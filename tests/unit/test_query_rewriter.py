from __future__ import annotations

from qanorm.services.qa.query_rewriter import QueryRewriter


def test_query_rewriter_builds_exact_lexical_and_semantic_queries() -> None:
    rewrite = QueryRewriter().rewrite(
        "Что требует п. 8.3 СП 63 по армированию колонн?",
        engineering_aspects=["армирование колонн"],
        constraints=["без сейсмики"],
    )

    assert rewrite.document_hint == "СП 63"
    assert rewrite.locator_hint == "п. 8.3"
    assert rewrite.exact_query == "СП 63 п. 8.3"
    assert "армированию" in rewrite.lexical_query
    assert "без сейсмики" in rewrite.semantic_query
