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
            document_display_code="SP 63.13330.2018",
            node_id=uuid4(),
            retrieval_unit_id=None,
            locator="5.1",
            heading_path="Section 5",
            score=1.0,
            text="Structural requirement text.",
        )
    ]


def test_runtime_skips_answer_modules_when_controller_has_no_evidence(monkeypatch) -> None:
    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="clarify",
                reasoning_summary="Need more context.",
                selected_evidence_ids=[],
                evidence=[],
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

    result = runtime.answer_query("Need clarification")

    assert result.answer.mode == "clarify"
    assert result.answer.evidence == []
    assert "evidence" in result.answer.limitations[0]


def test_runtime_runs_full_answer_flow(monkeypatch) -> None:
    evidence = _build_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="partial",
                reasoning_summary="One supported fragment was found.",
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
                answer_text="Draft answer [ev-0001].",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["Draft limitation."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            return Stage2AAnswerDTO(
                mode="partial",
                answer_text="Verified answer [ev-0001].",
                claims=[AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"])],
                evidence=evidence,
                limitations=["One supported fragment remained."],
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: object())

    result = runtime.answer_query("What does SP 63 say in 5.1?")

    assert result.answer.mode == "partial"
    assert result.answer.answer_text == "Verified answer [ev-0001]."
    assert result.answer.debug_trace[0].startswith("tool_name_0")


def test_runtime_uses_fallback_evidence_pack_when_controller_selected_none(monkeypatch) -> None:
    evidence = _build_evidence()

    class _FakeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, query_text: str):
            return SimpleNamespace(
                query_text=query_text,
                answer_mode="no_answer",
                reasoning_summary="Controller failed to name explicit evidence ids.",
                selected_evidence_ids=[],
                evidence=[],
                trajectory={"step": "fallback"},
                policy_hint="discover first",
                iterations_used=1,
            )

    class _FakeRetrieval:
        def build_evidence_pack(self, query_text: str):
            from qanorm.stage2a.retrieval.engine import RetrievalHit

            item = evidence[0]
            return [
                RetrievalHit(
                    source_kind=item.source_kind,
                    score=item.score,
                    document_id=item.document_id,
                    document_version_id=item.document_version_id,
                    node_id=item.node_id,
                    retrieval_unit_id=item.retrieval_unit_id,
                    order_index=1,
                    locator=item.locator,
                    heading_path=item.heading_path,
                    text=item.text,
                    document_display_code=item.document_display_code,
                )
            ]

    class _FakeComposer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def compose(self, **kwargs):
            assert kwargs["evidence"][0].evidence_id.startswith("ev-fallback-")
            return SimpleNamespace(
                answer_mode="partial",
                answer_text="Draft answer from fallback evidence.",
                claims=[AnswerClaimDTO(text="Fallback claim.", evidence_ids=[kwargs["evidence"][0].evidence_id])],
                evidence=kwargs["evidence"],
                limitations=["Fallback path used."],
            )

    class _FakeVerifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def verify(self, **kwargs):
            draft = kwargs["draft"]
            return Stage2AAnswerDTO(
                mode="partial",
                answer_text=draft.answer_text,
                claims=draft.claims,
                evidence=draft.evidence,
                limitations=draft.limitations,
            )

    runtime = Stage2ARuntime(
        session_factory=_FakeSessionFactory(),
        model_bundle=SimpleNamespace(controller=object(), composer=object(), verifier=object(), reranker=object(), provider_name="gemini"),
        controller_factory=_FakeController,
        composer_factory=_FakeComposer,
        verifier_factory=_FakeVerifier,
    )

    monkeypatch.setattr("qanorm.stage2a.runtime.RetrievalEngine", lambda session: _FakeRetrieval())

    result = runtime.answer_query("What does SP63 say?")

    assert result.answer.mode == "partial"
    assert result.answer.evidence[0].evidence_id.startswith("ev-fallback-")
    assert "Runtime fallback used the deterministic evidence pack." in result.controller.reasoning_summary
