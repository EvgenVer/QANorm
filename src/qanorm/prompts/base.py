"""Typed prompt-catalog contracts shared across the Stage 2 runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PromptKind = Literal["role", "fragment"]


@dataclass(slots=True, frozen=True)
class PromptVersionMetadata:
    """Traceable metadata for one resolved prompt file version."""

    name: str
    kind: PromptKind
    version: str
    environment: str
    path: str


@dataclass(slots=True, frozen=True)
class PromptTemplateDefinition:
    """Registration metadata for one prompt role or shared fragment."""

    name: str
    relative_path: str
    kind: PromptKind = "role"
    description: str | None = None
    default_fragments: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class LoadedPromptTemplate:
    """Resolved prompt file plus the fragments it depends on."""

    name: str
    content: str
    metadata: PromptVersionMetadata
    fragments: tuple["LoadedPromptTemplate", ...] = ()

    @property
    def version(self) -> str:
        """Expose version directly for convenience in callers and tests."""

        return self.metadata.version

    @property
    def environment(self) -> str:
        """Expose environment directly for convenience in callers and tests."""

        return self.metadata.environment

    @property
    def path(self) -> str:
        """Expose file path directly for convenience in callers and tests."""

        return self.metadata.path


@dataclass(slots=True, frozen=True)
class PromptRenderResult:
    """Rendered prompt text plus traceable template metadata."""

    text: str
    prompt_template_name: str
    prompt_version: str
    metadata: dict[str, Any] = field(default_factory=dict)
