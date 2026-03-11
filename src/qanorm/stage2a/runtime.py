"""High-level orchestration for one Stage 2A query."""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qanorm.db.session import create_session_factory
from qanorm.stage2a.agents import Composer, ControllerAgent, ControllerAgentResult, GroundingVerifier
from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import Stage2AAnswerDTO
from qanorm.stage2a.providers import Stage2ADspyModelBundle, build_stage2a_dspy_models
from qanorm.stage2a.retrieval.engine import RetrievalEngine


class Stage2AQueryResult(BaseModel):
    """Full answer payload returned by the Stage 2A runtime."""

    controller: ControllerAgentResult
    answer: Stage2AAnswerDTO


class Stage2ARuntime:
    """Compose controller, composer, and verifier into one query workflow."""

    def __init__(
        self,
        *,
        config: Stage2AConfig | None = None,
        session_factory: sessionmaker[Session] | None = None,
        model_bundle: Stage2ADspyModelBundle | None = None,
        controller_factory: Callable[..., ControllerAgent] | None = None,
        composer_factory: Callable[..., Composer] | None = None,
        verifier_factory: Callable[..., GroundingVerifier] | None = None,
    ) -> None:
        self.config = config or get_stage2a_config()
        self.session_factory = session_factory or create_session_factory()
        self.model_bundle = model_bundle or build_stage2a_dspy_models(self.config)
        self._controller_factory = controller_factory or ControllerAgent
        self._composer_factory = composer_factory or Composer
        self._verifier_factory = verifier_factory or GroundingVerifier

    def answer_query(self, query_text: str) -> Stage2AQueryResult:
        """Run the full Stage 2A answer flow for one question."""

        session = self.session_factory()
        try:
            retrieval = RetrievalEngine(session)
            controller = self._controller_factory(
                retrieval_engine=retrieval,
                config=self.config,
                model_bundle=self.model_bundle,
            )
            controller_result = _coerce_controller_result(controller.run(query_text))

            if not controller_result.evidence:
                answer = Stage2AAnswerDTO(
                    mode=controller_result.answer_mode,
                    answer_text=controller_result.reasoning_summary,
                    claims=[],
                    evidence=[],
                    limitations=["Контроллер не собрал подтвержденные evidence."],
                    debug_trace=_build_debug_trace(controller_result, enabled=self.config.runtime.enable_debug_trace),
                )
                return Stage2AQueryResult(controller=controller_result, answer=answer)

            composer = self._composer_factory(
                config=self.config,
                model_bundle=self.model_bundle,
            )
            verifier = self._verifier_factory(
                config=self.config,
                model_bundle=self.model_bundle,
            )
            draft = composer.compose(
                query_text=query_text,
                answer_mode=controller_result.answer_mode,
                evidence=controller_result.evidence,
            )
            answer = verifier.verify(query_text=query_text, draft=draft)
            answer = answer.model_copy(
                update={"debug_trace": _build_debug_trace(controller_result, enabled=self.config.runtime.enable_debug_trace)}
            )
            return Stage2AQueryResult(controller=controller_result, answer=answer)
        finally:
            session.close()


def _build_debug_trace(result: ControllerAgentResult, *, enabled: bool) -> list[str]:
    if not enabled:
        return []
    ordered_keys = sorted(result.trajectory.keys())
    return [f"{key}: {result.trajectory[key]}" for key in ordered_keys]


def _coerce_controller_result(value: ControllerAgentResult | object) -> ControllerAgentResult:
    if isinstance(value, ControllerAgentResult):
        return value
    if hasattr(value, "__dict__"):
        return ControllerAgentResult.model_validate(value.__dict__)
    return ControllerAgentResult.model_validate(value)
