"""Rendering helpers for the Stage 2B Streamlit chat UI."""

from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from qanorm.stage2a.contracts import RuntimeEventDTO


def iter_markdown_chunks(text: str, *, chunk_size: int = 120) -> Iterable[str]:
    """Yield fixed-size text chunks without destroying line breaks."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


def format_runtime_event(event: RuntimeEventDTO) -> str:
    """Convert one runtime event into a short markdown line for the debug trace."""

    marker = "warning" if event.level == "warning" else "step"
    details = _event_details(event)
    suffix = f" {details}" if details else ""
    return f"- `{marker}:{event.event_type}` {event.message}{suffix}"


def format_panel_value(value: Any) -> str:
    """Render nested payload values into one readable string for UI panels."""

    if isinstance(value, dict):
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return ", ".join(format_panel_value(item) for item in value)
    return str(value)


def _event_details(event: RuntimeEventDTO) -> str:
    if event.event_type == "query_rewritten":
        effective_query = str(event.payload.get("effective_query", "")).strip()
        if effective_query:
            return f"`effective_query={_compact_text(effective_query, limit=120)}`"
    if event.event_type == "controller_reasoning":
        summary = str(event.payload.get("summary", "")).strip()
        if summary:
            return f"`{_compact_text(summary, limit=160)}`"
    if event.event_type in {"tool_started", "tool_finished"}:
        tool_name = str(event.payload.get("tool_name", "")).strip()
        if event.event_type == "tool_finished":
            observation = str(event.payload.get("observation", "")).strip()
            if tool_name and observation:
                return f"`{tool_name}` -> `{_compact_text(observation, limit=120)}`"
        if tool_name:
            return f"`{tool_name}`"
    if event.event_type == "evidence_updated":
        evidence_count = event.payload.get("evidence_count")
        answer_mode = event.payload.get("answer_mode")
        if evidence_count is not None and answer_mode:
            return f"`mode={answer_mode}` `evidence={evidence_count}`"
    if event.event_type in {"warning", "verifier_started"}:
        limitations = event.payload.get("limitations")
        if isinstance(limitations, list) and limitations:
            return f"`{_compact_text(str(limitations[0]), limit=120)}`"
    return ""


def _compact_text(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."
