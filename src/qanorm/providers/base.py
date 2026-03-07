"""Common provider contracts, capabilities, and factory helpers for Stage 2."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from httpx import HTTPError, TimeoutException
from tenacity import AsyncRetrying

from qanorm.settings import ProviderName, ProviderSelection, QAFileConfig, RuntimeConfig, get_settings
from qanorm.utils.retry import build_retry_kwargs


ModelRole = Literal["orchestration", "synthesis", "embeddings"]
PromptKind = Literal["role", "fragment"]
ProviderCapabilityName = Literal["chat", "embeddings", "rerank"]


class ProviderError(RuntimeError):
    """Base error for provider integration failures."""


class ProviderConfigurationError(ProviderError):
    """Raised when runtime configuration cannot build a provider."""


class ProviderRequestError(ProviderError):
    """Raised when a provider request fails after retries."""


class ProviderTimeoutError(ProviderRequestError):
    """Raised when a provider request exceeds the configured timeout."""


class UnsupportedProviderCapabilityError(ProviderConfigurationError):
    """Raised when the selected provider does not support the requested role."""


@dataclass(slots=True, frozen=True)
class TokenUsage:
    """Normalized token-usage counters returned by chat providers."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True, frozen=True)
class ChatMessage:
    """Normalized chat message passed to model providers."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None


@dataclass(slots=True, frozen=True)
class ChatRequest:
    """Normalized request payload for chat-completion providers."""

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ChatResponse:
    """Normalized response payload for chat-completion providers."""

    provider: ProviderName
    model: str
    content: str
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class EmbeddingRequest:
    """Normalized request payload for embedding providers."""

    model: str
    texts: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class EmbeddingResponse:
    """Normalized response payload for embedding providers."""

    provider: ProviderName
    model: str
    vectors: list[list[float]]
    dimensions: int
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class RerankRequest:
    """Normalized request payload for reranking providers."""

    model: str
    query: str
    documents: list[str]
    top_k: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RerankResult:
    """One ranked item in a reranker response."""

    index: int
    document: str
    score: float


@dataclass(slots=True, frozen=True)
class RerankResponse:
    """Normalized response payload for rerank providers."""

    provider: ProviderName
    model: str
    results: list[RerankResult]
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class PromptTemplateDefinition:
    """Prompt-catalog registration metadata."""

    name: str
    relative_path: str
    kind: PromptKind = "role"


@dataclass(slots=True, frozen=True)
class LoadedPromptTemplate:
    """Resolved prompt template content and selection metadata."""

    name: str
    version: str
    environment: str
    path: str
    content: str


@dataclass(slots=True, frozen=True)
class PromptRenderResult:
    """Rendered prompt text plus traceable template metadata."""

    text: str
    prompt_template_name: str
    prompt_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    """Capability flags advertised by one provider adapter."""

    chat: bool = False
    embeddings: bool = False
    rerank: bool = False
    compatible_transport: bool = False
    native_transport: bool = False
    streaming: bool = False

    def supports(self, capability: ProviderCapabilityName) -> bool:
        """Return whether the provider supports one capability name."""

        return bool(getattr(self, capability))


class ProviderFactory(Protocol):
    """Factory signature used by the provider registry."""

    def __call__(self, runtime_config: RuntimeConfig, selection: ProviderSelection) -> Provider: ...


class Provider(ABC):
    """Common base class shared by all provider adapters."""

    provider_name: ProviderName
    capabilities: ProviderCapabilities

    def supports(self, capability: ProviderCapabilityName) -> bool:
        """Return whether the current provider exposes one capability."""

        return self.capabilities.supports(capability)


class ChatModelProvider(Provider):
    """Contract for chat-model providers used by orchestration and synthesis."""

    @abstractmethod
    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Generate one assistant response for the provided chat request."""


class EmbeddingProvider(Provider):
    """Contract for embedding providers used by retrieval and indexing."""

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return embeddings for the provided text batch."""


class RerankerProvider(Provider):
    """Contract for reranking providers used by later retrieval steps."""

    @abstractmethod
    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """Rank candidate documents for one query."""


ROLE_REQUIREMENTS: dict[ModelRole, ProviderCapabilityName] = {
    "orchestration": "chat",
    "synthesis": "chat",
    "embeddings": "embeddings",
}

PROVIDER_CAPABILITY_MATRIX: dict[ProviderName, ProviderCapabilities] = {
    "gemini": ProviderCapabilities(chat=True, embeddings=True, native_transport=True),
    "openai": ProviderCapabilities(chat=True, embeddings=True, compatible_transport=True),
    "anthropic": ProviderCapabilities(chat=True, native_transport=True),
    "qwen": ProviderCapabilities(chat=True, compatible_transport=True, native_transport=True),
    "deepseek": ProviderCapabilities(chat=True, compatible_transport=True, native_transport=True),
    "ollama": ProviderCapabilities(chat=True, embeddings=True, native_transport=True, streaming=True),
    "lmstudio": ProviderCapabilities(chat=True, embeddings=True, compatible_transport=True, streaming=True),
    "vllm": ProviderCapabilities(chat=True, embeddings=True, compatible_transport=True, streaming=True),
}


@dataclass(slots=True, frozen=True)
class ProviderRegistration:
    """One registry row mapping a provider name to a factory and capabilities."""

    name: ProviderName
    factory: ProviderFactory
    capabilities: ProviderCapabilities


@dataclass(slots=True, frozen=True)
class ProviderRoleBindings:
    """Resolved providers bound to the runtime model roles."""

    orchestration: ChatModelProvider
    synthesis: ChatModelProvider
    embeddings: EmbeddingProvider


class ProviderRegistry:
    """Mutable registry of provider factories and capability declarations."""

    def __init__(self) -> None:
        self._registrations: dict[ProviderName, ProviderRegistration] = {}

    def register(self, name: ProviderName, *, factory: ProviderFactory, capabilities: ProviderCapabilities) -> None:
        """Register one provider factory."""

        self._registrations[name] = ProviderRegistration(name=name, factory=factory, capabilities=capabilities)

    def get(self, name: ProviderName) -> ProviderRegistration:
        """Return one provider registration by name."""

        try:
            return self._registrations[name]
        except KeyError as exc:
            raise ProviderConfigurationError(f"Provider '{name}' is not registered.") from exc

    def create(self, name: ProviderName, *, runtime_config: RuntimeConfig, selection: ProviderSelection) -> Provider:
        """Build a provider instance from the registered factory."""

        registration = self.get(name)
        provider = registration.factory(runtime_config, selection)
        provider_capabilities = getattr(provider, "capabilities", None)
        if provider_capabilities != registration.capabilities:
            raise ProviderConfigurationError(
                f"Provider '{name}' reported capabilities {provider_capabilities!r}, "
                f"expected {registration.capabilities!r}."
            )
        return provider

    def list_registered(self) -> dict[ProviderName, ProviderCapabilities]:
        """Return a capability snapshot for all registered providers."""

        return {name: registration.capabilities for name, registration in self._registrations.items()}


def validate_provider_selection(*, role: ModelRole, selection: ProviderSelection) -> None:
    """Validate that one provider selection satisfies the role capability."""

    required_capability = ROLE_REQUIREMENTS[role]
    advertised = PROVIDER_CAPABILITY_MATRIX[selection.provider]
    if not advertised.supports(required_capability):
        raise UnsupportedProviderCapabilityError(
            f"Provider '{selection.provider}' does not support role '{role}' "
            f"(missing capability '{required_capability}')."
        )


def validate_provider_roles(qa_config: QAFileConfig) -> None:
    """Validate all configured role assignments against the capability matrix."""

    validate_provider_selection(role="orchestration", selection=qa_config.providers.orchestration)
    validate_provider_selection(role="synthesis", selection=qa_config.providers.synthesis)
    validate_provider_selection(role="embeddings", selection=qa_config.providers.embeddings)


def create_role_bound_providers(
    *,
    registry: ProviderRegistry,
    runtime_config: RuntimeConfig | None = None,
) -> ProviderRoleBindings:
    """Build the runtime providers configured for orchestration, synthesis, and embeddings."""

    config = runtime_config or get_settings()
    validate_provider_roles(config.qa)

    orchestration = registry.create(
        config.qa.providers.orchestration.provider,
        runtime_config=config,
        selection=config.qa.providers.orchestration,
    )
    synthesis = registry.create(
        config.qa.providers.synthesis.provider,
        runtime_config=config,
        selection=config.qa.providers.synthesis,
    )
    embeddings = registry.create(
        config.qa.providers.embeddings.provider,
        runtime_config=config,
        selection=config.qa.providers.embeddings,
    )

    if not isinstance(orchestration, ChatModelProvider):
        raise ProviderConfigurationError("The orchestration provider must implement ChatModelProvider.")
    if not isinstance(synthesis, ChatModelProvider):
        raise ProviderConfigurationError("The synthesis provider must implement ChatModelProvider.")
    if not isinstance(embeddings, EmbeddingProvider):
        raise ProviderConfigurationError("The embeddings provider must implement EmbeddingProvider.")

    return ProviderRoleBindings(
        orchestration=cast(ChatModelProvider, orchestration),
        synthesis=cast(ChatModelProvider, synthesis),
        embeddings=cast(EmbeddingProvider, embeddings),
    )


async def run_provider_call(
    operation: Callable[[], Awaitable[Any]],
    *,
    timeout_seconds: float,
    max_attempts: int,
) -> Any:
    """Execute one provider call with timeout and retry semantics."""

    retry_kwargs = build_retry_kwargs(
        max_attempts=max_attempts,
        min_wait_seconds=0.25,
        max_wait_seconds=2.0,
        retry_on=(ProviderTimeoutError, TimeoutException, HTTPError),
    )

    async for attempt in AsyncRetrying(**retry_kwargs):
        with attempt:
            try:
                return await asyncio.wait_for(operation(), timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise ProviderTimeoutError("Provider request timed out.") from exc
            except (TimeoutException, HTTPError) as exc:
                raise ProviderRequestError("Provider request failed.") from exc

    raise ProviderRequestError("Provider request failed after retries.")
