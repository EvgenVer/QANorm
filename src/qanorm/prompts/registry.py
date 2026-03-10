"""Prompt catalog registration, loading, and rendering helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qanorm.models.qa_state import PromptRenderContext
from qanorm.prompts.base import (
    LoadedPromptTemplate,
    PromptKind,
    PromptRenderResult,
    PromptTemplateDefinition,
    PromptVersionMetadata,
)
from qanorm.settings import RuntimeConfig, get_settings


DEFAULT_PROMPT_DEFINITIONS: tuple[PromptTemplateDefinition, ...] = (
    PromptTemplateDefinition(
        name="orchestrator",
        relative_path="roles/orchestrator",
        kind="role",
        default_fragments=("source_policy", "freshness_warning", "safety_policy"),
    ),
    PromptTemplateDefinition(
        name="query_analyzer",
        relative_path="roles/query_analyzer",
        kind="role",
        default_fragments=("source_policy", "safety_policy"),
    ),
    PromptTemplateDefinition(
        name="task_decomposer",
        relative_path="roles/task_decomposer",
        kind="role",
        default_fragments=("source_policy", "safety_policy"),
    ),
    PromptTemplateDefinition(
        name="answer_synthesizer",
        relative_path="roles/answer_synthesizer",
        kind="role",
        default_fragments=("source_policy", "freshness_warning", "safety_policy"),
    ),
    PromptTemplateDefinition(
        name="citation_auditor",
        relative_path="roles/citation_auditor",
        kind="role",
        default_fragments=("source_policy", "freshness_warning", "safety_policy"),
    ),
    PromptTemplateDefinition(
        name="coverage_auditor",
        relative_path="roles/coverage_auditor",
        kind="role",
        default_fragments=("source_policy", "freshness_warning", "safety_policy"),
    ),
    PromptTemplateDefinition(
        name="hallucination_guard",
        relative_path="roles/hallucination_guard",
        kind="role",
        default_fragments=("source_policy", "freshness_warning", "safety_policy"),
    ),
    PromptTemplateDefinition(name="source_policy", relative_path="fragments/source_policy", kind="fragment"),
    PromptTemplateDefinition(name="freshness_warning", relative_path="fragments/freshness_warning", kind="fragment"),
    PromptTemplateDefinition(name="safety_policy", relative_path="fragments/safety_policy", kind="fragment"),
)


class PromptTemplateNotFoundError(FileNotFoundError):
    """Raised when the requested prompt template cannot be resolved from the catalog."""


class PromptRegistry:
    """Load and render versioned prompt templates from the configured catalog."""

    def __init__(
        self,
        *,
        catalog_dir: Path,
        environment: str,
        default_version: str,
        version_overrides: dict[str, str] | None = None,
    ) -> None:
        self.catalog_dir = catalog_dir
        self.environment = environment
        self.default_version = default_version
        self.version_overrides = version_overrides or {}
        self._definitions: dict[str, PromptTemplateDefinition] = {}

    def register_template(self, definition: PromptTemplateDefinition) -> None:
        """Register one prompt template or fragment in the catalog."""

        self._definitions[definition.name] = definition

    def register_defaults(self) -> None:
        """Register the base role templates and shared policy fragments."""

        for definition in DEFAULT_PROMPT_DEFINITIONS:
            self.register_template(definition)

    def resolve_version(self, name: str, *, version: str | None = None) -> str:
        """Select the template version from explicit input, config overrides, or default."""

        if version:
            return version
        return self.version_overrides.get(name, self.default_version)

    def load_template(self, name: str, *, version: str | None = None) -> LoadedPromptTemplate:
        """Resolve and load one template from the prompt catalog."""

        definition = self._definitions.get(name)
        if definition is None:
            raise PromptTemplateNotFoundError(f"Prompt template '{name}' is not registered.")

        selected_version = self.resolve_version(name, version=version)
        for candidate in self._build_candidates(definition, selected_version):
            if candidate.exists():
                fragments = self._load_fragments(definition, selected_version)
                return LoadedPromptTemplate(
                    name=name,
                    content=candidate.read_text(encoding="utf-8"),
                    metadata=PromptVersionMetadata(
                        name=name,
                        kind=definition.kind,
                        version=selected_version,
                        environment=self.environment,
                        path=str(candidate),
                    ),
                    fragments=fragments,
                )

        raise PromptTemplateNotFoundError(
            f"Prompt template '{name}' version '{selected_version}' was not found in '{self.catalog_dir}'."
        )

    def render(
        self,
        name: str,
        *,
        context: PromptRenderContext,
        version: str | None = None,
        extra_variables: dict[str, Any] | None = None,
    ) -> PromptRenderResult:
        """Render one prompt template with normalized context variables."""

        template = self.load_template(name, version=version)
        render_variables = self._build_render_variables(context)
        # Fragments are rendered first so role templates can reference them as ordinary variables.
        render_variables.update(self._build_fragment_variables(template, context))
        if extra_variables:
            render_variables.update(extra_variables)
        text = template.content.format_map(render_variables)
        metadata = {
            "prompt_template_name": template.name,
            "prompt_version": template.version,
            "prompt_environment": template.environment,
            "prompt_path": template.path,
            "prompt_kind": template.metadata.kind,
            "prompt_fragments": [fragment.name for fragment in template.fragments],
        }
        return PromptRenderResult(
            text=text,
            prompt_template_name=template.name,
            prompt_version=template.version,
            metadata=metadata,
        )

    def list_registered(self, *, kind: PromptKind | None = None) -> dict[str, PromptTemplateDefinition]:
        """Return registered prompt definitions, optionally filtered by kind."""

        if kind is None:
            return dict(self._definitions)
        return {name: definition for name, definition in self._definitions.items() if definition.kind == kind}

    def _load_fragments(
        self,
        definition: PromptTemplateDefinition,
        selected_version: str,
    ) -> tuple[LoadedPromptTemplate, ...]:
        """Resolve declared shared fragments for one role template."""

        loaded: list[LoadedPromptTemplate] = []
        for fragment_name in definition.default_fragments:
            fragment_definition = self._definitions.get(fragment_name)
            if fragment_definition is None:
                raise PromptTemplateNotFoundError(
                    f"Prompt fragment '{fragment_name}' is not registered for template '{definition.name}'."
                )
            loaded.append(self.load_template(fragment_name, version=selected_version))
        return tuple(loaded)

    def _build_candidates(self, definition: PromptTemplateDefinition, selected_version: str) -> list[Path]:
        """Build search candidates using both environment-specific and common directories."""

        return [
            self.catalog_dir / self.environment / f"{definition.relative_path}.{selected_version}.md",
            self.catalog_dir / self.environment / f"{definition.relative_path}.md",
            self.catalog_dir / "common" / f"{definition.relative_path}.{selected_version}.md",
            self.catalog_dir / "common" / f"{definition.relative_path}.md",
        ]

    def _build_fragment_variables(
        self,
        template: LoadedPromptTemplate,
        context: PromptRenderContext,
    ) -> dict[str, Any]:
        """Render fragment templates into variables consumable by role templates."""

        render_variables = self._build_render_variables(context)
        fragment_variables: dict[str, Any] = {}
        for fragment in template.fragments:
            variable_name = f"{fragment.name}_text"
            fragment_variables[variable_name] = fragment.content.format_map(render_variables)
        return fragment_variables

    def _build_render_variables(self, context: PromptRenderContext) -> dict[str, Any]:
        """Flatten the runtime prompt context into format-ready strings."""

        recent_messages_text = "\n".join(
            f"- {getattr(message.role, 'value', message.role)}: {message.content}" for message in context.recent_messages
        )
        evidence_bundle = context.evidence_bundle
        return {
            "session_id": str(context.session_id),
            "query_id": str(context.query_id) if context.query_id else "",
            "query_text": context.query_text,
            "session_summary": context.session_summary or "",
            "intent": context.intent or "",
            "retrieval_mode": context.retrieval_mode or "",
            "clarification_required": json.dumps(context.clarification_required, ensure_ascii=False),
            "clarification_question": context.clarification_question or "",
            "document_hints_text": "\n".join(f"- {item}" for item in context.document_hints),
            "locator_hints_text": "\n".join(f"- {item}" for item in context.locator_hints),
            "subject": context.subject or "",
            "engineering_aspects_text": "\n".join(f"- {item}" for item in context.engineering_aspects),
            "constraints_text": "\n".join(f"- {item}" for item in context.constraints),
            "document_resolution_json": json.dumps(context.document_resolution or {}, ensure_ascii=False, sort_keys=True),
            "recent_messages_text": recent_messages_text,
            "normative_evidence_text": self._render_evidence_list(evidence_bundle.normative),
            "trusted_web_evidence_text": self._render_evidence_list(evidence_bundle.trusted_web),
            "open_web_evidence_text": self._render_evidence_list(evidence_bundle.open_web),
            "stale_warning_text": "\n".join(context.stale_warning_messages),
            "prompt_context_json": json.dumps(
                {
                    "session_id": str(context.session_id),
                    "query_id": str(context.query_id) if context.query_id else None,
                    "query_text": context.query_text,
                    "session_summary": context.session_summary,
                    "intent": context.intent,
                    "retrieval_mode": context.retrieval_mode,
                    "clarification_required": context.clarification_required,
                    "document_hint_count": len(context.document_hints),
                    "locator_hint_count": len(context.locator_hints),
                    "subject": context.subject,
                    "engineering_aspect_count": len(context.engineering_aspects),
                    "constraint_count": len(context.constraints),
                    "document_resolution": context.document_resolution,
                    "recent_message_count": len(context.recent_messages),
                    "normative_evidence_count": len(evidence_bundle.normative),
                    "trusted_web_evidence_count": len(evidence_bundle.trusted_web),
                    "open_web_evidence_count": len(evidence_bundle.open_web),
                    "stale_warning_count": len(context.stale_warning_messages),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        }

    def _render_evidence_list(self, evidence_list: list[Any]) -> str:
        """Render evidence rows into a stable text block for prompt templates."""

        rows: list[str] = []
        for item in evidence_list:
            locator = getattr(item, "locator", None) or "n/a"
            quote = getattr(item, "quote", None) or ""
            title = getattr(item, "document_title", None) or getattr(item, "title", None) or "Untitled source"
            rows.append(f"- {title} [{locator}] {quote}".strip())
        return "\n".join(rows)


def create_prompt_registry(runtime_config: RuntimeConfig | None = None) -> PromptRegistry:
    """Build the prompt registry from runtime configuration."""

    config = runtime_config or get_settings()
    registry = PromptRegistry(
        catalog_dir=config.qa.providers.prompt_catalog_dir,
        environment=config.env.app_env,
        default_version=config.qa.providers.prompt_default_version,
        version_overrides=config.qa.providers.prompt_versions,
    )
    registry.register_defaults()
    return registry
