"""Stage 2 agent exports with lazy imports to avoid package cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "AnswerSection",
    "AnswerSynthesizer",
    "PlannedSubtask",
    "QueryAnalysis",
    "QueryAnalyzer",
    "QueryComplexity",
    "QueryOrchestrator",
    "QueryPlanningResult",
    "QueryTaskDecomposer",
    "StructuredAnswer",
]

_MODULE_EXPORTS = {
    "AnswerSection": ("qanorm.agents.answer_synthesizer", "AnswerSection"),
    "AnswerSynthesizer": ("qanorm.agents.answer_synthesizer", "AnswerSynthesizer"),
    "StructuredAnswer": ("qanorm.agents.answer_synthesizer", "StructuredAnswer"),
    "PlannedSubtask": ("qanorm.agents.planner", "PlannedSubtask"),
    "QueryAnalysis": ("qanorm.agents.planner", "QueryAnalysis"),
    "QueryAnalyzer": ("qanorm.agents.planner", "QueryAnalyzer"),
    "QueryComplexity": ("qanorm.agents.planner", "QueryComplexity"),
    "QueryTaskDecomposer": ("qanorm.agents.planner", "QueryTaskDecomposer"),
    "QueryOrchestrator": ("qanorm.agents.orchestrator", "QueryOrchestrator"),
    "QueryPlanningResult": ("qanorm.agents.orchestrator", "QueryPlanningResult"),
}


def __getattr__(name: str) -> Any:
    """Resolve agent exports lazily so planner imports do not pull orchestrator eagerly."""

    target = _MODULE_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
