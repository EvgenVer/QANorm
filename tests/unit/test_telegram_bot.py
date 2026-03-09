from __future__ import annotations

from qanorm.integrations.telegram import chunk_telegram_text, format_answer_for_telegram


def test_chunk_telegram_text_splits_long_payload_without_losing_content() -> None:
    text = ("Paragraph one.\n\n" * 40).strip()

    chunks = chunk_telegram_text(text, max_length=120)

    assert len(chunks) > 1
    assert "".join(chunk.replace("\n", "") for chunk in chunks).startswith("Paragraph one.")


def test_format_answer_for_telegram_returns_safe_chunks() -> None:
    chunks = format_answer_for_telegram("## Heading\n\n- item 1\n- item 2", max_length=500)

    assert chunks
    assert "<b>" in chunks[0]
    assert "• item 1" in chunks[0]
