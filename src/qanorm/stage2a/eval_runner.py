"""Eval runner for the Stage 2A MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from pydantic import BaseModel, Field

from qanorm.normalizers.codes import normalize_document_code
from qanorm.settings import PROJECT_ROOT, get_settings
from qanorm.stage2a.runtime import Stage2AQueryResult, Stage2ARuntime


class EvalQuestion(BaseModel):
    """One eval question with lightweight gold expectations."""

    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    expected_mode: str = Field(min_length=1)
    expected_documents: list[str] = Field(default_factory=list)
    expected_locators: list[str] = Field(default_factory=list)
    require_exact_edition: bool = False
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
    document_match_mode: str = "family"
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
    document_hit_at_3: float = Field(default=0.0, ge=0.0, le=1.0)
    locator_hit_at_5: float = Field(default=0.0, ge=0.0, le=1.0)
    grounded_answer_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    unsupported_claim_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    partial_answer_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_mode_match_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    wrong_document_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    question_results: list[EvalQuestionResult] = Field(default_factory=list)


@dataclass(slots=True)
class EvalWorkerRunResult:
    """Summary of one detached eval worker."""

    status: str
    processed_questions: int
    remaining_questions: int
    state_path: str
    report_path: str
    log_path: str


@dataclass(slots=True)
class ParallelEvalRunResult:
    """Summary of a detached parallel eval run."""

    status: str
    worker_count: int
    manifest_path: str
    workers: list[dict[str, Any]]


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
    normalized_actual_families = {_normalize_document_family(value) for value in actual_documents}
    normalized_actual_locators = {_normalize_text(value) for value in actual_locators}
    expected_documents = [_normalize_text(value) for value in question.expected_documents]
    expected_families = {_normalize_document_family(value) for value in question.expected_documents}
    expected_locators = [_normalize_text(value) for value in question.expected_locators]
    forbidden_documents = {_normalize_text(value) for value in question.must_not_use_documents}
    requires_exact = question.require_exact_edition
    if not expected_documents:
        document_hit = True
    elif requires_exact:
        document_hit = any(value in normalized_actual_documents for value in expected_documents)
    else:
        document_hit = any(value in normalized_actual_families for value in expected_families)
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
        document_match_mode="exact" if requires_exact else "family",
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
    scenario: str | None = None,
    runtime_factory: Callable[[], Stage2ARuntime] | None = None,
) -> EvalRunReport:
    """Run the Stage 2A runtime over one eval set and score the results."""

    questions = _select_eval_questions(questions_path=questions_path, limit=limit, scenario=scenario)
    runtime = (runtime_factory or Stage2ARuntime)()
    results = [score_eval_result(question, runtime.answer_query(question.query)) for question in questions]
    return build_eval_report(results)


def run_stage2a_eval_worker(
    *,
    questions_path: str | Path,
    limit: int | None = None,
    scenario: str | None = None,
    runtime_factory: Callable[[], Stage2ARuntime] | None = None,
    state_path: str | Path | None = None,
    report_path: str | Path | None = None,
    log_path: str | Path | None = None,
    shard_index: int = 0,
    shard_count: int = 1,
) -> EvalWorkerRunResult:
    """Run one detached eval shard with resumable checkpoint/report state."""

    shard_index, shard_count = _normalize_shard_params(shard_index=shard_index, shard_count=shard_count)
    resolved_state_path, resolved_report_path, resolved_log_path = _resolve_eval_paths(
        state_path=state_path,
        report_path=report_path,
        log_path=log_path,
    )
    logger = _build_eval_logger(resolved_log_path, logger_name=f"eval_runner_{shard_index}_{shard_count}")
    state = _read_state_file(resolved_state_path)
    report = _read_eval_report_file(resolved_report_path)
    processed_ids = {item.question_id for item in report.question_results}

    questions = _select_eval_questions(questions_path=questions_path, limit=limit, scenario=scenario)
    shard_questions = _slice_questions_for_shard(questions, shard_index=shard_index, shard_count=shard_count)
    pending_questions = [question for question in shard_questions if question.id not in processed_ids]
    started_at = state.get("started_at") or datetime.now(UTC).isoformat()
    _write_state_file(
        resolved_state_path,
        {
            **state,
            "status": "running",
            "pid": os.getpid(),
            "questions_path": str(Path(questions_path)),
            "limit": limit,
            "scenario": scenario,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "target_questions": len(shard_questions),
            "processed_questions": len(processed_ids),
            "remaining_questions": len(pending_questions),
            "started_at": started_at,
            "updated_at": datetime.now(UTC).isoformat(),
            "report_path": str(resolved_report_path),
            "log_path": str(resolved_log_path),
        },
    )

    logger.info(
        "Starting Stage 2A eval worker shard=%s/%s target=%s pending=%s",
        shard_index + 1,
        shard_count,
        len(shard_questions),
        len(pending_questions),
    )

    runtime = (runtime_factory or Stage2ARuntime)()
    try:
        for question in pending_questions:
            scored = score_eval_result(question, runtime.answer_query(question.query))
            report.question_results.append(scored)
            report = build_eval_report(report.question_results)
            processed_ids.add(question.id)
            remaining_questions = max(0, len(shard_questions) - len(processed_ids))
            _write_eval_report_file(resolved_report_path, report)
            _write_state_file(
                resolved_state_path,
                {
                    **state,
                    "status": "running",
                    "pid": os.getpid(),
                    "questions_path": str(Path(questions_path)),
                    "limit": limit,
                    "scenario": scenario,
                    "shard_index": shard_index,
                    "shard_count": shard_count,
                    "target_questions": len(shard_questions),
                    "processed_questions": len(processed_ids),
                    "remaining_questions": remaining_questions,
                    "last_question_id": question.id,
                    "started_at": started_at,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "report_path": str(resolved_report_path),
                    "log_path": str(resolved_log_path),
                },
            )
            logger.info(
                "Scored question %s; processed=%s remaining=%s",
                question.id,
                len(processed_ids),
                remaining_questions,
            )
    except Exception as exc:
        logger.exception("Stage 2A eval worker failed")
        _write_state_file(
            resolved_state_path,
            {
                **state,
                "status": "failed",
                "pid": os.getpid(),
                "questions_path": str(Path(questions_path)),
                "limit": limit,
                "scenario": scenario,
                "shard_index": shard_index,
                "shard_count": shard_count,
                "target_questions": len(shard_questions),
                "processed_questions": len(processed_ids),
                "remaining_questions": max(0, len(shard_questions) - len(processed_ids)),
                "started_at": started_at,
                "updated_at": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "report_path": str(resolved_report_path),
                "log_path": str(resolved_log_path),
            },
        )
        raise

    logger.info("Stage 2A eval worker completed")
    _write_state_file(
        resolved_state_path,
        {
            **state,
            "status": "completed",
            "pid": os.getpid(),
            "questions_path": str(Path(questions_path)),
            "limit": limit,
            "scenario": scenario,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "target_questions": len(shard_questions),
            "processed_questions": len(processed_ids),
            "remaining_questions": 0,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "report_path": str(resolved_report_path),
            "log_path": str(resolved_log_path),
        },
    )
    return EvalWorkerRunResult(
        status="completed",
        processed_questions=len(processed_ids),
        remaining_questions=0,
        state_path=str(resolved_state_path),
        report_path=str(resolved_report_path),
        log_path=str(resolved_log_path),
    )


def start_stage2a_eval_process(
    *,
    questions_path: str | Path,
    limit: int | None = None,
    scenario: str | None = None,
    state_path: str | Path | None = None,
    report_path: str | Path | None = None,
    log_path: str | Path | None = None,
    shard_index: int = 0,
    shard_count: int = 1,
) -> dict[str, Any]:
    """Spawn one detached eval worker."""

    shard_index, shard_count = _normalize_shard_params(shard_index=shard_index, shard_count=shard_count)
    resolved_state_path, resolved_report_path, resolved_log_path = _resolve_eval_paths(
        state_path=state_path,
        report_path=report_path,
        log_path=log_path,
    )
    existing_state = _read_state_file(resolved_state_path)
    command = [
        sys.executable,
        "-m",
        "qanorm.cli.main",
        "stage2a-eval-worker",
        "--questions-path",
        str(Path(questions_path)),
        "--state-path",
        str(resolved_state_path),
        "--report-path",
        str(resolved_report_path),
        "--log-path",
        str(resolved_log_path),
        "--shard-index",
        str(shard_index),
        "--shard-count",
        str(shard_count),
    ]
    if limit is not None:
        command.extend(["--limit", str(limit)])
    if scenario:
        command.extend(["--scenario", scenario])

    process = _spawn_detached_process(command)
    _write_state_file(
        resolved_state_path,
        {
            **existing_state,
            "status": "queued",
            "pid": process.pid,
            "questions_path": str(Path(questions_path)),
            "limit": limit,
            "scenario": scenario,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "command": command,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "report_path": str(resolved_report_path),
            "log_path": str(resolved_log_path),
        },
    )
    return {
        "status": "started",
        "pid": process.pid,
        "state_path": str(resolved_state_path),
        "report_path": str(resolved_report_path),
        "log_path": str(resolved_log_path),
    }


def start_parallel_stage2a_eval_processes(
    *,
    worker_count: int,
    questions_path: str | Path,
    limit: int | None = None,
    scenario: str | None = None,
    state_path: str | Path | None = None,
    report_path: str | Path | None = None,
    log_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> ParallelEvalRunResult:
    """Spawn multiple detached eval workers over eval shards."""

    if worker_count < 2:
        raise ValueError("worker_count must be at least 2 for parallel eval")

    base_state_path, base_report_path, base_log_path = _resolve_eval_paths(
        state_path=state_path,
        report_path=report_path,
        log_path=log_path,
    )
    resolved_manifest_path = _resolve_eval_manifest_path(manifest_path)
    questions = _select_eval_questions(questions_path=questions_path, limit=limit, scenario=scenario)
    workers: list[dict[str, Any]] = []

    for shard_index in range(worker_count):
        shard_state_path = _derive_shard_path(base_state_path, shard_index=shard_index, shard_count=worker_count)
        shard_report_path = _derive_shard_path(base_report_path, shard_index=shard_index, shard_count=worker_count)
        shard_log_path = _derive_shard_path(base_log_path, shard_index=shard_index, shard_count=worker_count)
        shard_questions = _slice_questions_for_shard(questions, shard_index=shard_index, shard_count=worker_count)
        _write_state_file(
            shard_state_path,
            {
                "status": "queued",
                "questions_path": str(Path(questions_path)),
                "limit": limit,
                "scenario": scenario,
                "shard_index": shard_index,
                "shard_count": worker_count,
                "target_questions": len(shard_questions),
                "processed_questions": 0,
                "remaining_questions": len(shard_questions),
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "report_path": str(shard_report_path),
                "log_path": str(shard_log_path),
            },
        )
        started = start_stage2a_eval_process(
            questions_path=questions_path,
            limit=limit,
            scenario=scenario,
            state_path=shard_state_path,
            report_path=shard_report_path,
            log_path=shard_log_path,
            shard_index=shard_index,
            shard_count=worker_count,
        )
        workers.append(
            {
                **started,
                "shard_index": shard_index,
                "shard_count": worker_count,
                "target_questions": len(shard_questions),
            }
        )

    manifest_payload = {
        "status": "running",
        "worker_count": worker_count,
        "questions_path": str(Path(questions_path)),
        "limit": limit,
        "scenario": scenario,
        "manifest_path": str(resolved_manifest_path),
        "workers": workers,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    _write_state_file(resolved_manifest_path, manifest_payload)
    return ParallelEvalRunResult(
        status="started",
        worker_count=worker_count,
        manifest_path=str(resolved_manifest_path),
        workers=workers,
    )


def read_stage2a_eval_state(
    *,
    state_path: str | Path | None = None,
    report_path: str | Path | None = None,
    log_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read persisted state for one eval worker or one parallel eval run."""

    if state_path is None:
        resolved_manifest_path = _resolve_eval_manifest_path(manifest_path)
        manifest = _read_state_file(resolved_manifest_path)
        if manifest.get("workers"):
            worker_states: list[dict[str, Any]] = []
            reports: list[EvalRunReport] = []
            for worker in manifest["workers"]:
                worker_state = _read_state_file(Path(worker["state_path"]))
                worker_states.append(worker_state or worker)
                worker_report_path = (worker_state or worker).get("report_path")
                if worker_report_path:
                    report = _read_eval_report_file(Path(worker_report_path))
                    if report.question_results:
                        reports.append(report)
            return _aggregate_parallel_eval_states(manifest, worker_states, reports)

    resolved_state_path, resolved_report_path, _ = _resolve_eval_paths(
        state_path=state_path,
        report_path=report_path,
        log_path=log_path,
    )
    state = _read_state_file(resolved_state_path)
    report = _read_eval_report_file(resolved_report_path)
    if report.total_questions:
        state["report"] = report.model_dump(mode="json")
    return state


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


def _normalize_document_family(value: str) -> str:
    normalized = normalize_document_code(str(value))
    normalized = re.sub(r"([.\-/])\d{4}$", "", normalized)
    return _normalize_text(normalized)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _select_eval_questions(
    *,
    questions_path: str | Path,
    limit: int | None = None,
    scenario: str | None = None,
) -> list[EvalQuestion]:
    questions = load_eval_questions(questions_path)
    if scenario:
        questions = [question for question in questions if question.scenario == scenario]
    if limit is not None:
        questions = questions[:limit]
    return questions


def _slice_questions_for_shard(
    questions: list[EvalQuestion],
    *,
    shard_index: int,
    shard_count: int,
) -> list[EvalQuestion]:
    if shard_count == 1:
        return list(questions)
    return [question for index, question in enumerate(questions) if index % shard_count == shard_index]


def _resolve_eval_paths(
    *,
    state_path: str | Path | None,
    report_path: str | Path | None,
    log_path: str | Path | None,
) -> tuple[Path, Path, Path]:
    base_dir = get_settings().env.raw_storage_path.parent / "stage2a"
    resolved_state_path = Path(state_path) if state_path is not None else base_dir / "eval_state.json"
    resolved_report_path = Path(report_path) if report_path is not None else base_dir / "eval_report.json"
    resolved_log_path = Path(log_path) if log_path is not None else base_dir / "eval.log"
    resolved_state_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_state_path, resolved_report_path, resolved_log_path


def _resolve_eval_manifest_path(manifest_path: str | Path | None) -> Path:
    base_dir = get_settings().env.raw_storage_path.parent / "stage2a"
    resolved_path = Path(manifest_path) if manifest_path is not None else base_dir / "eval_manifest.json"
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_path


def _build_eval_logger(log_path: Path, *, logger_name: str) -> logging.Logger:
    logger = logging.getLogger(f"qanorm.stage2a.{logger_name}.{log_path}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def _spawn_detached_process(command: list[str]) -> subprocess.Popen[Any]:
    popen_kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(command, **popen_kwargs)


def _read_state_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state_file(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_eval_report_file(path: Path) -> EvalRunReport:
    if not path.exists():
        return build_eval_report([])
    return EvalRunReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _write_eval_report_file(path: Path, report: EvalRunReport) -> None:
    path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_shard_params(*, shard_index: int, shard_count: int) -> tuple[int, int]:
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be within [0, shard_count)")
    return shard_index, shard_count


def _derive_shard_path(path: Path, *, shard_index: int, shard_count: int) -> Path:
    suffix = f".shard-{shard_index + 1:02d}-of-{shard_count:02d}"
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def _aggregate_parallel_eval_states(
    manifest: dict[str, Any],
    worker_states: list[dict[str, Any]],
    reports: list[EvalRunReport],
) -> dict[str, Any]:
    statuses = [state.get("status", "unknown") for state in worker_states]
    if any(status == "failed" for status in statuses):
        aggregate_status = "failed"
    elif any(status in {"running", "queued"} for status in statuses):
        aggregate_status = "running"
    elif worker_states and all(status == "completed" for status in statuses):
        aggregate_status = "completed"
    else:
        aggregate_status = "unknown"

    processed_questions = sum(int(state.get("processed_questions", 0)) for state in worker_states)
    remaining_questions = sum(int(state.get("remaining_questions", 0)) for state in worker_states)
    target_questions = sum(int(state.get("target_questions", 0)) for state in worker_states)
    aggregate_results: list[EvalQuestionResult] = []
    for report in reports:
        aggregate_results.extend(report.question_results)
    aggregate_report = build_eval_report(aggregate_results)

    return {
        "status": aggregate_status,
        "worker_count": manifest.get("worker_count", len(worker_states)),
        "manifest_path": manifest.get("manifest_path"),
        "questions_path": manifest.get("questions_path"),
        "limit": manifest.get("limit"),
        "scenario": manifest.get("scenario"),
        "created_at": manifest.get("created_at"),
        "updated_at": datetime.now(UTC).isoformat(),
        "target_questions": target_questions,
        "processed_questions": processed_questions,
        "remaining_questions": remaining_questions,
        "workers": worker_states,
        "report": aggregate_report.model_dump(mode="json"),
    }
