from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from qanorm.stage2a.contracts import AnswerClaimDTO, EvidenceItemDTO, Stage2AAnswerDTO
from qanorm.stage2a.eval_runner import (
    EvalQuestion,
    EvalRunReport,
    build_eval_report,
    load_eval_questions,
    read_stage2a_eval_state,
    run_stage2a_eval,
    score_eval_result,
)
from qanorm.stage2a.runtime import Stage2AQueryResult
from qanorm.stage2a.agents import ControllerAgentResult

EVAL_PATH = Path(__file__).resolve().parents[2] / "eval" / "stage2a" / "questions.jsonl"


def _build_query_result(
    *,
    mode: str = "direct",
    document_code: str = "СП 63.13330.2018",
    locator: str | None = None,
    supported: bool = True,
) -> Stage2AQueryResult:
    evidence = [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="retrieval_unit_lexical",
            document_id=uuid4(),
            document_version_id=uuid4(),
            document_display_code=document_code,
            document_title="Документ",
            retrieval_unit_id=uuid4(),
            locator=locator,
            heading_path="Раздел 1",
            score=1.0,
            text="Подтвержденный фрагмент нормы.",
        )
    ]
    claims = [AnswerClaimDTO(text="Краткий вывод", evidence_ids=["ev-0001"], supported=supported)]
    return Stage2AQueryResult(
        controller=ControllerAgentResult(
            query_text="test",
            answer_mode=mode,
            reasoning_summary="ok",
            selected_evidence_ids=["ev-0001"],
            evidence=evidence,
            trajectory={},
            policy_hint="hint",
            iterations_used=1,
        ),
        answer=Stage2AAnswerDTO(
            mode=mode,
            answer_text="Краткий вывод",
            claims=claims,
            evidence=evidence,
            limitations=[],
            debug_trace=[],
        ),
    )


def test_load_eval_questions_reads_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "eval-1",
                        "query": "Что по СП 63 про плиты?",
                        "scenario": "explicit_document_without_locator",
                        "expected_mode": "direct",
                        "expected_documents": ["СП 63.13330.2018"],
                        "expected_locators": [],
                        "must_include_terms": ["плит"],
                        "must_not_use_documents": [],
                        "notes": "",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "id": "eval-2",
                        "query": "Какие нагрузки учитывать?",
                        "scenario": "no_explicit_norm_engineering",
                        "expected_mode": "direct",
                        "expected_documents": ["СП 20.13330.2016"],
                        "expected_locators": [],
                        "must_include_terms": ["нагруз"],
                        "must_not_use_documents": [],
                        "notes": "",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    questions = load_eval_questions(path)

    assert len(questions) == 2
    assert questions[0].id == "eval-1"
    assert questions[1].scenario == "no_explicit_norm_engineering"


def test_score_eval_result_tracks_document_and_locator_hits() -> None:
    question = load_eval_questions(EVAL_PATH)[0]
    result = _build_query_result(mode="direct", document_code="СП 63.13330.2018")

    scored = score_eval_result(question, result)

    assert scored.document_hit is False
    assert scored.grounded_answer is True
    assert scored.partial_answer is False


def test_build_eval_report_aggregates_expected_metrics() -> None:
    questions = load_eval_questions(EVAL_PATH)
    q1 = questions[30]
    q2 = questions[47]
    q3 = questions[135]

    r1 = score_eval_result(q1, _build_query_result(mode="direct", document_code="СП 63.13330.2018"))
    r2 = score_eval_result(q2, _build_query_result(mode="clarify", document_code="СП 63.13330.2018"))
    r3 = score_eval_result(q3, _build_query_result(mode="direct", document_code="СП 63.13330.2018", locator="10.3.8"))

    report = build_eval_report([r1, r2, r3])

    assert report.total_questions == 3
    assert report.document_hit_at_3 == 1.0
    assert report.locator_hit_at_5 == 1.0
    assert report.partial_answer_rate == 0.0
    assert report.expected_mode_match_rate == 1.0


def test_score_eval_result_counts_latest_edition_as_family_hit_by_default() -> None:
    question = load_eval_questions(EVAL_PATH)[24]
    result = _build_query_result(mode="direct", document_code="СП 50.13330.2024")

    scored = score_eval_result(question, result)

    assert scored.document_hit is True
    assert scored.document_match_mode == "family"


def test_score_eval_result_can_require_exact_edition() -> None:
    question = load_eval_questions(EVAL_PATH)[24].model_copy(update={"require_exact_edition": True})
    result = _build_query_result(mode="direct", document_code="СП 50.13330.2024")

    scored = score_eval_result(question, result)

    assert scored.document_hit is False
    assert scored.document_match_mode == "exact"


def test_score_eval_result_treats_clarify_as_neutral_for_grounded_metric() -> None:
    question = load_eval_questions(EVAL_PATH)[47]
    result = _build_query_result(mode="clarify", document_code="СП 63.13330.2018")

    scored = score_eval_result(question, result)

    assert scored.grounded_answer is True


def test_run_stage2a_eval_uses_runtime_factory(tmp_path: Path) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "eval-1",
                "query": "Что СП 63 говорит про шаг арматуры в плитах?",
                "scenario": "explicit_document_without_locator",
                "expected_mode": "direct",
                "expected_documents": ["СП 63.13330.2018"],
                "expected_locators": [],
                "must_include_terms": ["шаг", "арматур", "плит"],
                "must_not_use_documents": [],
                "notes": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _FakeRuntime:
        def answer_query(self, query_text: str) -> Stage2AQueryResult:
            assert "СП 63" in query_text
            return _build_query_result(mode="direct", document_code="СП 63.13330.2018")

    report = run_stage2a_eval(questions_path=path, runtime_factory=_FakeRuntime)

    assert report.total_questions == 1
    assert report.document_hit_at_3 == 1.0
    assert report.grounded_answer_rate == 1.0


def test_run_stage2a_eval_filters_by_scenario() -> None:
    class _FakeRuntime:
        def answer_query(self, query_text: str) -> Stage2AQueryResult:
            return _build_query_result(mode="direct", document_code="СП 63.13330.2018")

    report = run_stage2a_eval(
        questions_path=EVAL_PATH,
        scenario="compact_alias_dirty_input",
        runtime_factory=_FakeRuntime,
    )

    assert report.total_questions == 15
    assert report.scenario_counts == {"compact_alias_dirty_input": 15}


def test_read_stage2a_eval_state_aggregates_parallel_reports(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    state_one = tmp_path / "eval_state.shard-01.json"
    state_two = tmp_path / "eval_state.shard-02.json"
    report_one = tmp_path / "eval_report.shard-01.json"
    report_two = tmp_path / "eval_report.shard-02.json"

    state_one.write_text(
        json.dumps(
            {
                "status": "completed",
                "processed_questions": 1,
                "remaining_questions": 0,
                "target_questions": 1,
                "report_path": str(report_one),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_two.write_text(
        json.dumps(
            {
                "status": "running",
                "processed_questions": 1,
                "remaining_questions": 1,
                "target_questions": 2,
                "report_path": str(report_two),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    q1 = load_eval_questions(EVAL_PATH)[30]
    q2 = load_eval_questions(EVAL_PATH)[47]
    report_one.write_text(
        json.dumps(
            build_eval_report([score_eval_result(q1, _build_query_result(mode="direct", document_code="СП 63.13330.2018"))]).model_dump(mode="json"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_two.write_text(
        json.dumps(
            build_eval_report([score_eval_result(q2, _build_query_result(mode="clarify", document_code="СП 63.13330.2018"))]).model_dump(mode="json"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "status": "running",
                "worker_count": 2,
                "manifest_path": str(manifest_path),
                "workers": [
                    {"state_path": str(state_one)},
                    {"state_path": str(state_two)},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status = read_stage2a_eval_state(manifest_path=manifest_path)

    assert status["status"] == "running"
    assert status["processed_questions"] == 2
    assert status["remaining_questions"] == 1
    assert status["report"]["total_questions"] == 2
    assert status["report"]["document_hit_at_3"] == 1.0


def test_eval_shard_selection_is_stable() -> None:
    questions = [
        EvalQuestion(
            id=f"eval-{index:04d}",
            query=f"q{index}",
            scenario="s",
            expected_mode="direct",
        )
        for index in range(6)
    ]

    from qanorm.stage2a import eval_runner as module

    shard_zero = module._slice_questions_for_shard(questions, shard_index=0, shard_count=3)
    shard_one = module._slice_questions_for_shard(questions, shard_index=1, shard_count=3)
    shard_two = module._slice_questions_for_shard(questions, shard_index=2, shard_count=3)

    assert [item.id for item in shard_zero] == ["eval-0000", "eval-0003"]
    assert [item.id for item in shard_one] == ["eval-0001", "eval-0004"]
    assert [item.id for item in shard_two] == ["eval-0002", "eval-0005"]
