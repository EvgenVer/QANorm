from __future__ import annotations

from qanorm.stage2a.retrieval.query_parser import QueryParser


def test_query_parser_expands_compact_alias_into_family_variants() -> None:
    parser = QueryParser()

    parsed = parser.parse("Что в СП63 по плитам?")

    assert "СП 63" in parsed.explicit_document_codes
    assert "СП63" in parsed.explicit_document_codes


def test_query_parser_keeps_yearless_variant_for_gost_with_year_suffix() -> None:
    parser = QueryParser()

    parsed = parser.parse("Что ГОСТ27751-2014 говорит про надежность?")

    assert "ГОСТ 27751-2014" in parsed.explicit_document_codes
    assert "ГОСТ 27751" in parsed.explicit_document_codes
