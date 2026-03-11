from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.contracts import AnswerClaimDTO, EvidenceItemDTO, Stage2AAnswerDTO
from qanorm.stage2a.runtime import Stage2ARuntime


class _FakeSession:
    def close(self) -> None:
        self.closed = True


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


def _build_evidence() -> list[EvidenceItemDTO]:
    document_id = uuid4()
    version_id = uuid4()
    return [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="document_node_locator",
            document_id=document_id,
            document_version_id=version_id,
            node_id=uuid4(),
            retrieval_unit_id=None,
            locator="5.1",
            heading_path="Раздел 5",
            score=1.0,
            text="Требование по расчету конструкции.",
        )
    ]


def test_runtime_skips_answer_modules_when_controller_has_no_evidence(monkeypatch) -> None:
    evidence = []

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="clarify",
                reasoning_summary="Нужно уточнить контекст.",
                selected_evidence_ids=[],
                evidence=evidence,
                trajectory={"step": "none"},
                policy_hint="discover first",
                iterations_used=1,
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("composer must not be called")),
        verifier_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("verifier must not be called")),
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: object())

    result = runtime.answer_query("Нужен ли температурный шов?")

    assert result.answer.mode == "clarify"
    assert result.answer.evidence == []
    assert "Контроллер не собрал" in result.answer.limitations[0]


def test_runtime_runs_full_answer_flow(monkeypatch) -> None:
    evidence = _build_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="Найден один подтвержденный фрагмент.",
                selected_evidence_ids=["ev-0001"],
                evidence=evidence,
                trajectory={"tool_name_0": "lookup_locator"},
                policy_hint="resolve then lookup",
                iterations_used=1,
            )

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Черновик ответа [ev-0001].",
                claims=[AnswerClaimDTO(text="Подтвержденный тезис.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Черновик."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="partial",
                answer_text="Проверенный ответ [ev-0001].",
                claims=[AnswerClaimDTO(text="Подтвержденный тезис.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Остался один подтвержденный фрагмент."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: object())

    result = runtime.answer_query("Что сказано в СП 63.13330.2018 пункт 5.1?")

    assert result.answer.mode == "partial"
    assert result.answer.answer_text == "Проверенный ответ [ev-0001]."
    assert result.answer.debug_trace[0].startswith("tool_name_0")
