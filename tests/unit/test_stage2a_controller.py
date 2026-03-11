from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.agents.controller import ControllerAgent
from qanorm.stage2a.providers import Stage2ADspyModelBundle
from qanorm.stage2a.retrieval.engine import DocumentCandidate, RetrievalHit
from qanorm.stage2a.retrieval.query_parser import ParsedQuery


DOCUMENT_ID = uuid4()
VERSION_ID = uuid4()
NODE_ID = uuid4()
NEIGHBOR_NODE_ID = uuid4()


class _FakeRetrievalEngine:
    def parse_query(self, text: str) -> ParsedQuery:
        return ParsedQuery(
            raw_text=text,
            normalized_text=text,
            explicit_document_codes=["сп 63.13330.2018"] if "СП" in text else [],
            explicit_locator_values=["5.1"] if "5.1" in text else [],
            lexical_query=text,
            lexical_tokens=["нагрузка", "кровля"],
        )

    def resolve_document(self, _: ParsedQuery) -> list[DocumentCandidate]:
        return [
            DocumentCandidate(
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_ID,
                score=1.0,
                reason="explicit_code",
                matched_value="сп 63.13330.2018",
                display_code="СП 63.13330.2018",
                title="Бетонные конструкции",
            )
        ]

    def discover_documents(self, _: ParsedQuery) -> list[DocumentCandidate]:
        return self.resolve_document(_)

    def lookup_locator(self, *, document_version_id, locator: str) -> list[RetrievalHit]:
        assert document_version_id == VERSION_ID
        assert locator == "5.1"
        return [
            RetrievalHit(
                source_kind="document_node_locator",
                score=1.0,
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_ID,
                node_id=NODE_ID,
                retrieval_unit_id=None,
                order_index=10,
                locator="5.1",
                heading_path="Раздел 5",
                text="Требование к расчету конструкции.",
            ),
            RetrievalHit(
                source_kind="document_node_locator",
                score=0.9,
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_ID,
                node_id=NEIGHBOR_NODE_ID,
                retrieval_unit_id=None,
                order_index=11,
                locator="5.1",
                heading_path="Раздел 5",
                text="Дополнительное требование к расчету конструкции.",
            ),
        ]

    def search_lexical(self, query_text: str, *, document_version_ids) -> list[RetrievalHit]:
        assert query_text
        assert document_version_ids
        return [
            RetrievalHit(
                source_kind="retrieval_unit_lexical",
                score=0.85,
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_ID,
                node_id=NODE_ID,
                retrieval_unit_id=uuid4(),
                order_index=10,
                locator="5.1",
                heading_path="Раздел 5",
                text="Лексический фрагмент по расчету конструкции.",
            )
        ]

    def read_node(self, node_id):
        if node_id != NODE_ID:
            return None
        return RetrievalHit(
            source_kind="document_node",
            score=1.0,
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_ID,
            node_id=NODE_ID,
            retrieval_unit_id=None,
            order_index=10,
            locator="5.1",
            heading_path="Раздел 5",
            text="Точное чтение узла.",
        )

    def expand_neighbors(self, *, document_version_id, node_id):
        assert document_version_id == VERSION_ID
        assert node_id == NODE_ID
        return self.lookup_locator(document_version_id=document_version_id, locator="5.1")


def _build_bundle() -> Stage2ADspyModelBundle:
    placeholder = object()
    return Stage2ADspyModelBundle(
        provider_name="gemini",
        controller=placeholder,
        composer=placeholder,
        verifier=placeholder,
        reranker=placeholder,
    )


def test_controller_agent_returns_direct_when_enough_evidence() -> None:
    retrieval = _FakeRetrievalEngine()

    def fake_factory(tools):
        tool_map = {tool.__name__: tool for tool in tools}

        class _Program:
            def __call__(self, **kwargs):
                resolve_observation = tool_map["resolve_document"](kwargs["query_text"])
                locator_observation = tool_map["lookup_locator"](str(VERSION_ID), "5.1")
                return SimpleNamespace(
                    answer_mode="direct",
                    reasoning_summary="Нашел прямую норму и локатор.",
                    selected_evidence_ids="ev-0001, ev-0002",
                    trajectory={
                        "observation_0": resolve_observation,
                        "observation_1": locator_observation,
                    },
                )

        return _Program()

    agent = ControllerAgent(
        retrieval_engine=retrieval,
        model_bundle=_build_bundle(),
        react_factory=fake_factory,
    )

    result = agent.run("Что сказано в СП 63.13330.2018 пункт 5.1?")

    assert result.answer_mode == "direct"
    assert len(result.evidence) == 2
    assert result.selected_evidence_ids == ["ev-0001", "ev-0002"]
    assert "explicit document code and locator" in result.policy_hint


def test_controller_agent_retries_and_downgrades_to_partial() -> None:
    retrieval = _FakeRetrievalEngine()
    feedback_values: list[str] = []
    invocation_index = {"value": 0}

    def fake_factory(tools):
        tool_map = {tool.__name__: tool for tool in tools}

        class _Program:
            def __call__(self, **kwargs):
                invocation_index["value"] += 1
                feedback_values.append(kwargs["retrieval_feedback"])
                lexical_observation = tool_map["search_lexical"](kwargs["query_text"], [str(VERSION_ID)])
                if invocation_index["value"] == 1:
                    return SimpleNamespace(
                        answer_mode="direct",
                        reasoning_summary="Пока мало подтверждений.",
                        selected_evidence_ids="ev-9999",
                        trajectory={"observation_0": lexical_observation},
                    )
                return SimpleNamespace(
                    answer_mode="partial",
                    reasoning_summary="Есть один подтвержденный фрагмент.",
                    selected_evidence_ids="ev-0001",
                    trajectory={"observation_0": lexical_observation},
                )

        return _Program()

    agent = ControllerAgent(
        retrieval_engine=retrieval,
        model_bundle=_build_bundle(),
        react_factory=fake_factory,
    )

    result = agent.run("Какая нагрузка на кровлю?")

    assert result.answer_mode == "partial"
    assert result.iterations_used == 2
    assert len(result.evidence) == 1
    assert feedback_values[0] == ""
    assert "No valid evidence ids were selected" in feedback_values[1]
