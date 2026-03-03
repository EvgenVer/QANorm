"""HTML document parsing."""

from __future__ import annotations

from dataclasses import dataclass

from lxml import html


_REMOVABLE_XPATH = (
    "//script | //style | //noscript | //header | //footer | //nav | "
    "//*[contains(@class, 'crumbs')] | "
    "//*[contains(@class, 'header')] | "
    "//*[contains(@class, 'footer')] | "
    "//*[contains(@class, 'menu')]"
)


@dataclass(slots=True)
class HtmlTextExtractionResult:
    """Normalized text extracted from an HTML document."""

    text: str
    text_length: int


def extract_text_from_html_document(page_html: str) -> HtmlTextExtractionResult:
    """Extract normalized readable text from a full HTML document."""

    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(page_html.encode("utf-8"), parser=parser)

    for node in tree.xpath(_REMOVABLE_XPATH):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)

    preferred_roots = (
        tree.xpath("//div[contains(@class, 'contener_doc')]")
        or tree.xpath("//div[contains(@class, 'contener_cat_list')]")
        or tree.xpath("//body")
        or [tree]
    )
    text_chunks = []
    for chunk in preferred_roots[0].itertext():
        normalized = " ".join(chunk.split())
        if normalized:
            text_chunks.append(normalized)

    text = "\n".join(text_chunks)
    return HtmlTextExtractionResult(text=text, text_length=len(text))
