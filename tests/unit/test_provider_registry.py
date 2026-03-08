from __future__ import annotations

from pathlib import Path

import pytest

from qanorm.prompts.registry import PromptRegistry, PromptTemplateNotFoundError, create_prompt_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import (
    PROVIDER_CAPABILITY_MATRIX,
    PromptTemplateDefinition,
    UnsupportedProviderCapabilityError,
    create_role_bound_providers,
    validate_provider_roles,
)
from qanorm.providers.ollama import OllamaProvider
from qanorm.settings import (
    AppFileConfig,
    EnvironmentSettings,
    ProviderSelection,
    ProvidersRuntimeConfig,
    QAFileConfig,
    RuntimeConfig,
    SearchRuntimeConfig,
    SessionRuntimeConfig,
    SourcesConfig,
    StatusesConfig,
    TelegramRuntimeConfig,
    WebRuntimeConfig,
)


def _runtime_config(*, prompt_dir: Path | None = None) -> RuntimeConfig:
    qa_config = QAFileConfig(
        session=SessionRuntimeConfig(
            ttl_hours=24,
            summary_trigger_messages=12,
            summary_keep_recent_messages=8,
            max_parallel_queries_per_session=1,
        ),
        providers=ProvidersRuntimeConfig(
            orchestration=ProviderSelection(provider="ollama", model="qwen2.5:7b-instruct"),
            synthesis=ProviderSelection(provider="ollama", model="qwen2.5:14b-instruct"),
            embeddings=ProviderSelection(provider="ollama", model="bge-m3"),
            prompt_catalog_dir=prompt_dir or Path("src/qanorm/prompts/templates"),
            prompt_default_version="v1",
            prompt_versions={},
        ),
        web=WebRuntimeConfig(stream_transport="sse", session_cookie_name="qanorm_session_id"),
        telegram=TelegramRuntimeConfig(enabled=False, use_webhook=False),
        search=SearchRuntimeConfig(open_web_provider="searxng", open_web_max_results=5, trusted_domains=[]),
    )
    return RuntimeConfig(
        env=EnvironmentSettings(
            db_url="postgresql+psycopg://postgres:postgres@localhost:5432/qanorm",
            redis_url="redis://localhost:6379/0",
            api_public_url="http://localhost:8000",
            web_public_url="http://localhost:3000",
            ollama_base_url="http://localhost:11434",
            lmstudio_base_url="http://localhost:1234/v1",
            vllm_base_url="http://localhost:8001/v1",
        ),
        app=AppFileConfig(
            request_timeout_seconds=15,
            max_retries=1,
            rate_limit_per_second=5.0,
            user_agent="qanorm-tests",
            ocr_render_dpi=300,
            ocr_low_confidence_threshold=0.7,
        ),
        sources=SourcesConfig(seed_urls=["https://example.com"]),
        statuses=StatusesConfig(active=["active"], inactive=["inactive"]),
        qa=qa_config,
    )


def test_provider_registry_registers_all_declared_capabilities() -> None:
    registry = create_provider_registry()

    assert registry.list_registered() == PROVIDER_CAPABILITY_MATRIX


def test_create_role_bound_providers_builds_runtime_providers() -> None:
    registry = create_provider_registry()

    bindings = create_role_bound_providers(registry=registry, runtime_config=_runtime_config())

    assert isinstance(bindings.orchestration, OllamaProvider)
    assert isinstance(bindings.synthesis, OllamaProvider)
    assert isinstance(bindings.embeddings, OllamaProvider)


def test_validate_provider_roles_rejects_provider_without_embedding_capability() -> None:
    runtime_config = _runtime_config()
    runtime_config.qa.providers.embeddings = ProviderSelection(provider="anthropic", model="claude-3-5-sonnet")

    with pytest.raises(UnsupportedProviderCapabilityError):
        validate_provider_roles(runtime_config.qa)


def test_prompt_registry_resolves_environment_specific_version(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "prompts"
    template_path = catalog_dir / "local" / "roles" / "orchestrator.v2.md"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("Query: {query_text}\nSummary: {session_summary}", encoding="utf-8")

    registry = PromptRegistry(
        catalog_dir=catalog_dir,
        environment="local",
        default_version="v1",
        version_overrides={"orchestrator": "v2"},
    )
    registry.register_template(PromptTemplateDefinition(name="orchestrator", relative_path="roles/orchestrator"))

    rendered = registry.render(
        "orchestrator",
        context=_build_prompt_context(),
    )

    assert rendered.text == "Query: Clause 4.2\nSummary: short summary"
    assert rendered.prompt_template_name == "orchestrator"
    assert rendered.prompt_version == "v2"
    assert rendered.metadata["prompt_environment"] == "local"


def test_prompt_registry_falls_back_to_common_template(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "prompts"
    template_path = catalog_dir / "common" / "roles" / "answer_synthesizer.v1.md"
    source_policy_path = catalog_dir / "common" / "fragments" / "source_policy.v1.md"
    freshness_warning_path = catalog_dir / "common" / "fragments" / "freshness_warning.v1.md"
    safety_policy_path = catalog_dir / "common" / "fragments" / "safety_policy.v1.md"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    source_policy_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("Messages:\n{recent_messages_text}", encoding="utf-8")
    source_policy_path.write_text("SOURCE", encoding="utf-8")
    freshness_warning_path.write_text("FRESHNESS", encoding="utf-8")
    safety_policy_path.write_text("SAFETY", encoding="utf-8")

    runtime_config = _runtime_config(prompt_dir=catalog_dir)
    registry = create_prompt_registry(runtime_config)

    rendered = registry.render("answer_synthesizer", context=_build_prompt_context())

    assert "user: prior question" in rendered.text
    assert rendered.metadata["prompt_path"].endswith("answer_synthesizer.v1.md")


def test_prompt_registry_raises_for_missing_template(tmp_path: Path) -> None:
    registry = PromptRegistry(
        catalog_dir=tmp_path,
        environment="local",
        default_version="v1",
    )
    registry.register_template(PromptTemplateDefinition(name="task_decomposer", relative_path="roles/task_decomposer"))

    with pytest.raises(PromptTemplateNotFoundError):
        registry.load_template("task_decomposer")


def _build_prompt_context():
    from uuid import uuid4

    from qanorm.db.types import MessageRole
    from qanorm.models import QAMessage
    from qanorm.models.qa_state import PromptRenderContext

    return PromptRenderContext(
        session_id=uuid4(),
        query_id=uuid4(),
        query_text="Clause 4.2",
        session_summary="short summary",
        recent_messages=[QAMessage(session_id=uuid4(), role=MessageRole.USER, content="prior question")],
        stale_warning_messages=["stale"],
    )
