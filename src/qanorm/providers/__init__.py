"""Stage 2 provider registry exports."""

from __future__ import annotations

from qanorm.providers.anthropic import AnthropicProvider
from qanorm.providers.base import (
    ChatMessage,
    ChatModelProvider,
    ChatRequest,
    ChatResponse,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
    PromptRenderResult,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderRoleBindings,
    RerankerProvider,
    RerankRequest,
    RerankResponse,
    ROLE_REQUIREMENTS,
    PROVIDER_CAPABILITY_MATRIX,
    create_role_bound_providers,
    validate_provider_roles,
)
from qanorm.providers.deepseek import DeepSeekProvider
from qanorm.providers.gemini import GeminiProvider
from qanorm.providers.lmstudio import LMStudioProvider
from qanorm.providers.ollama import OllamaProvider
from qanorm.providers.openai import OpenAIProvider
from qanorm.providers.qwen import QwenProvider
from qanorm.providers.searxng import SearXNGProvider
from qanorm.providers.vllm import VLLMProvider


def create_provider_registry() -> ProviderRegistry:
    """Register all provider adapters supported by Stage 2."""

    registry = ProviderRegistry()
    registry.register("gemini", factory=GeminiProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["gemini"])
    registry.register("openai", factory=OpenAIProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["openai"])
    registry.register("anthropic", factory=AnthropicProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["anthropic"])
    registry.register("qwen", factory=QwenProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["qwen"])
    registry.register("deepseek", factory=DeepSeekProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["deepseek"])
    registry.register("ollama", factory=OllamaProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["ollama"])
    registry.register("lmstudio", factory=LMStudioProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["lmstudio"])
    registry.register("vllm", factory=VLLMProvider, capabilities=PROVIDER_CAPABILITY_MATRIX["vllm"])
    return registry


__all__ = [
    "AnthropicProvider",
    "ChatMessage",
    "ChatModelProvider",
    "ChatRequest",
    "ChatResponse",
    "DeepSeekProvider",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "GeminiProvider",
    "LMStudioProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "PROVIDER_CAPABILITY_MATRIX",
    "PromptRenderResult",
    "ProviderCapabilities",
    "ProviderRegistry",
    "ProviderRoleBindings",
    "QwenProvider",
    "RerankerProvider",
    "RerankRequest",
    "RerankResponse",
    "SearXNGProvider",
    "ROLE_REQUIREMENTS",
    "VLLMProvider",
    "create_provider_registry",
    "create_role_bound_providers",
    "validate_provider_roles",
]
