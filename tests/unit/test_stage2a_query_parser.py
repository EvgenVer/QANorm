from __future__ import annotations

from qanorm.stage2a.retrieval import QueryParser


def test_query_parser_extracts_explicit_document_codes_and_locators() -> None:
    parser = QueryParser()

    parsed = parser.parse("Что требует СП 20.13330.2016 по п. 1.1 для нагрузок?")

    assert parsed.explicit_document_codes == ["СП 20.13330.2016"]
    assert parsed.explicit_locator_values == ["1.1"]
    assert "нагруз" in parsed.lexical_query


def test_query_parser_extracts_appendix_locator() -> None:
    parser = QueryParser()

    parsed = parser.parse("Посмотри приложение А ГОСТ 27751-2014")

    assert parsed.explicit_document_codes == ["ГОСТ 27751-2014"]
    assert parsed.explicit_locator_values == ["а"]


def test_query_parser_expands_compact_document_prefixes() -> None:
    parser = QueryParser()

    parsed = parser.parse("Что требует СП63 по п. 5.1?")

    assert parsed.explicit_document_codes == ["СП 63"]
    assert parsed.explicit_locator_values == ["5.1"]
