from __future__ import annotations

from uuid import uuid4

from qanorm.normalizers.structure import StructuralNodeDraft
from qanorm.services.document_pipeline import _build_heading_path_for_draft


def test_build_heading_path_for_draft_uses_heading_ancestors_only() -> None:
    title = StructuralNodeDraft(
        node_type="title",
        text="СП 20.13330.2016",
        label=None,
        title="СП 20.13330.2016",
        order_index=1,
        parent_order_index=None,
        locator="title:1",
        page_from=None,
        page_to=None,
        char_start=0,
        char_end=10,
        parse_confidence=1.0,
    )
    section = StructuralNodeDraft(
        node_type="section",
        text="1 Общие положения",
        label="1",
        title="Общие положения",
        order_index=2,
        parent_order_index=1,
        locator="section:1",
        page_from=None,
        page_to=None,
        char_start=11,
        char_end=30,
        parse_confidence=1.0,
    )
    paragraph = StructuralNodeDraft(
        node_type="paragraph",
        text="Текст пункта",
        label=None,
        title=None,
        order_index=3,
        parent_order_index=2,
        locator="paragraph:3",
        page_from=None,
        page_to=None,
        char_start=31,
        char_end=42,
        parse_confidence=1.0,
    )

    heading_paths: dict[int, str | None] = {
        1: _build_heading_path_for_draft(title, [title], {}),
        2: _build_heading_path_for_draft(section, [title, section], {1: "СП 20.13330.2016"}),
    }

    paragraph_heading = _build_heading_path_for_draft(paragraph, [title, section, paragraph], heading_paths)

    assert paragraph_heading == "СП 20.13330.2016 > 1 Общие положения"
