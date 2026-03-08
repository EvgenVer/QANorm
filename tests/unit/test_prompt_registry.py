from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from qanorm.db.types import MessageRole
from qanorm.models import QAMessage
from qanorm.models.qa_state import PromptRenderContext
from qanorm.prompts.base import PromptTemplateDefinition
from qanorm.prompts.registry import PromptRegistry


def test_prompt_registry_renders_default_fragments_into_role_templates(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "prompts"
    role_path = catalog_dir / "common" / "roles" / "orchestrator.v1.md"
    source_fragment_path = catalog_dir / "common" / "fragments" / "source_policy.v1.md"
    safety_fragment_path = catalog_dir / "common" / "fragments" / "safety_policy.v1.md"
    role_path.parent.mkdir(parents=True, exist_ok=True)
    source_fragment_path.parent.mkdir(parents=True, exist_ok=True)

    role_path.write_text("Q: {query_text}\n{source_policy_text}\n{safety_policy_text}", encoding="utf-8")
    source_fragment_path.write_text("SOURCE POLICY", encoding="utf-8")
    safety_fragment_path.write_text("SAFETY POLICY", encoding="utf-8")

    registry = PromptRegistry(catalog_dir=catalog_dir, environment="local", default_version="v1")
    registry.register_template(
        PromptTemplateDefinition(
            name="orchestrator",
            relative_path="roles/orchestrator",
            default_fragments=("source_policy", "safety_policy"),
        )
    )
    registry.register_template(PromptTemplateDefinition(name="source_policy", relative_path="fragments/source_policy", kind="fragment"))
    registry.register_template(PromptTemplateDefinition(name="safety_policy", relative_path="fragments/safety_policy", kind="fragment"))

    rendered = registry.render("orchestrator", context=_build_prompt_context())

    assert rendered.text == "Q: Clause 4.2\nSOURCE POLICY\nSAFETY POLICY"
    assert rendered.metadata["prompt_fragments"] == ["source_policy", "safety_policy"]


def test_prompt_registry_exposes_metadata_from_loaded_template(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "prompts"
    role_path = catalog_dir / "local" / "roles" / "query_analyzer.v2.md"
    role_path.parent.mkdir(parents=True, exist_ok=True)
    role_path.write_text("Analyze: {query_text}", encoding="utf-8")

    registry = PromptRegistry(
        catalog_dir=catalog_dir,
        environment="local",
        default_version="v1",
        version_overrides={"query_analyzer": "v2"},
    )
    registry.register_template(PromptTemplateDefinition(name="query_analyzer", relative_path="roles/query_analyzer"))

    loaded = registry.load_template("query_analyzer")

    assert loaded.metadata.name == "query_analyzer"
    assert loaded.metadata.version == "v2"
    assert loaded.metadata.environment == "local"
    assert loaded.path.endswith("query_analyzer.v2.md")


def _build_prompt_context() -> PromptRenderContext:
    return PromptRenderContext(
        session_id=uuid4(),
        query_id=uuid4(),
        query_text="Clause 4.2",
        session_summary="summary",
        recent_messages=[QAMessage(session_id=uuid4(), role=MessageRole.USER, content="prior question")],
    )
