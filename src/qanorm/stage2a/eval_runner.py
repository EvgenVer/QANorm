"""Eval runner for the Stage 2A MVP."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from qanorm.stage2a.runtime import Stage2AQueryResult, Stage2ARuntime


class EvalQuestion(BaseModel):
    """One eval question with lightweight gold expectations."""

    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    expected_mode: str = Field(min_length=1)
    expected_documents: list[str] = Field(default_factory=list)
    expected_locators: list[str] = Field(default_factory=list)
    must_include_terms: list[str] = Field(default_factory=list)
    must_not_use_documents: list[str] = Field(default_factory=list)
    notes: str = ""


class EvalQuestionResult(BaseModel):
    """One scored eval result."""

    question_id: str
    scenario: str
    query: str
    expected_mode: str
    actual_mode: str
    mode_match: bool
    expected_documents: list[str] = Field(default_factory=list)
    actual_documents: list[str] = Field(default_factory=list)
    document_hit: bool = False
    expected_locators: list[str] = Field(default_factory=list)
    actual_locators: list[str] = Field(default_factory=list)
    locator_hit: bool | None = None
    grounded_answer: bool = False
    unsupported_claim: bool = False
    partial_answer: bool = False
    forbidden_document_used: bool = False
    limitation_count: int = 0


class EvalRunReport(BaseModel):
    """Aggregate Stage 2A eval report."""

    total_questions: int = Field(ge=0)
    scenario_counts: dict[str, int] = Field(default_factory=dict)
    document_hit_at_3: float = Field(ge=0.0, le=1.0)
    locator_hit_at_5: float = Field(ge=0.0, le=1.0)
    grounded_answer_rate: float = Field(ge=0.0, le=1.0)
    unsupported_claim_rate: float = Field(ge=0.0, le=1.0)
    partial_answer_rate: float = Field(ge=0.0, le=1.0)
    expected_mode_match_rate: float = Field(ge=0.0, le=1.0)
    wrong_document_rate: float = Field(ge=0.0, le=1.0)
    question_results: list[EvalQuestionResult] = Field(default_factory=list)


def load_eval_questions(path: str | Path) -> list[EvalQuestion]:
    """Load eval questions from a JSONL file."""

    source = Path(path)
    records = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [EvalQuestion.model_validate(json.loads(line)) for line in records]


def score_eval_result(question: EvalQuestion, result: Stage2AQueryResult) -> EvalQuestionResult:
    """Score one runtime result against lightweight gold expectations."""

    answer = result.answer
    actual_documents = _top_unique_values(
        [item.document_display_code for item in answer.evidence if item.document_display_code],
        limit=3,
    )
    actual_locators = _top_unique_values(
        [item.locator for item in answer.evidence if item.locator],
        limit=5,
    )
    normalized_actual_documents = {_normalize_text(value) for value in actual_documents}
    normalized_actual_locators = {_normalize_text(value) for value in actual_locators}
    expected_documents = [_normalize_text(value) for value in question.expected_documents]
    expected_locators = [_normalize_text(value) for value in question.expected_locators]
    forbidden_documents = {_normalize_text(value) for value in question.must_not_use_documents}

    document_hit = not expected_documents or any(value in normalized_actual_documents for value in expected_documents)
    locator_hit = None
    if expected_locators:
        locator_hit = any(value in normalized_actual_locators for value in expected_locators)

    unsupported_claim = any((not claim.supported) or (not claim.evidence_ids) for claim in answer.claims)
    grounded_answer = answer.mode in {"direct", "partial"} and bool(answer.evidence) and not unsupported_claim
    actual_mode = answer.mode
    return EvalQuestionResult(
        question_id=question.id,
        scenario=question.scenario,
        query=question.query,
        expected_mode=question.expected_mode,
        actual_mode=actual_mode,
        mode_match=_normalize_text(question.expected_mode) == _normalize_text(actual_mode),
        expected_documents=question.expected_documents,
        actual_documents=actual_documents,
        document_hit=document_hit,
        expected_locators=question.expected_locators,
        actual_locators=actual_locators,
        locator_hit=locator_hit,
        grounded_answer=grounded_answer,
        unsupported_claim=unsupported_claim,
        partial_answer=actual_mode == "partial",
        forbidden_document_used=any(value in normalized_actual_documents for value in forbidden_documents),
        limitation_count=len(answer.limitations),
    )


def build_eval_report(results: list[EvalQuestionResult]) -> EvalRunReport:
    """Aggregate metrics across all eval questions."""

    total = len(results)
    if total == 0:
        return EvalRunReport(total_questions=0)

    locator_results = [item for item in results if item.locator_hit is not None]
    return EvalRunReport(
        total_questions=total,
        scenario_counts=dict(Counter(item.scenario for item in results)),
        document_hit_at_3=_ratio(sum(1 for item in results if item.document_hit), total),
        locator_hit_at_5=_ratio(sum(1 for item in locator_results if item.locator_hit), len(locator_results)),
        grounded_answer_rate=_ratio(sum(1 for item in results if item.grounded_answer), total),
        unsupported_claim_rate=_ratio(sum(1 for item in results if item.unsupported_claim), total),
        partial_answer_rate=_ratio(sum(1 for item in results if item.partial_answer), total),
        expected_mode_match_rate=_ratio(sum(1 for item in results if item.mode_match), total),
        wrong_document_rate=_ratio(sum(1 for item in results if item.forbidden_document_used), total),
        question_results=results,
    )


def run_stage2a_eval(
    *,
    questions_path: str | Path,
    limit: int | None = None,
    runtime_factory: Callable[[], Stage2ARuntime] | None = None,
) -> EvalRunReport:
    """Run the Stage 2A runtime over one eval set and score the results."""

    questions = load_eval_questions(questions_path)
    if limit is not None:
        questions = questions[:limit]
    runtime = (runtime_factory or Stage2ARuntime)()
    results = [score_eval_result(question, runtime.answer_query(question.query)) for question in questions]
    return build_eval_report(results)


def _top_unique_values(values: list[str], *, limit: int) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(value)
        if len(ordered) >= limit:
            break
    return ordered


def _normalize_text(value: str) -> str:
    return " ".join(str(value).lower().split())


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
