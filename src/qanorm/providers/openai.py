"""OpenAI provider adapters and reusable compatible-provider base class."""

from __future__ import annotations

from typing import Any

from qanorm.providers.base import (
    ChatMessage,
    ChatModelProvider,
    ChatRequest,
    ChatResponse,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderCapabilities,
    ProviderName,
    TokenUsage,
)
from qanorm.providers.compatible_transport import CompatibleTransportClient
from qanorm.settings import ProviderSelection, RuntimeConfig


class OpenAICompatibleProviderBase(ChatModelProvider, EmbeddingProvider):
    """Shared implementation for providers that expose an OpenAI-style transport."""

    provider_name: ProviderName = "openai"
    capabilities = ProviderCapabilities(chat=True, embeddings=True, compatible_transport=True)

    def __init__(
        self,
        *,
        model: str,
        transport: CompatibleTransportClient,
    ) -> None:
        self.model = model
        self.transport = transport

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Send one chat-completions request over the compatible transport."""

        payload = {
            "model": request.model or self.model,
            "messages": [self._serialize_message(message) for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        response = await self.transport.request_json(method="POST", path="/chat/completions", payload=payload)
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        return ChatResponse(
            provider=self.provider_name,
            model=str(response.get("model") or payload["model"]),
            content=str(content),
            finish_reason=choice.get("finish_reason"),
            usage=self._parse_usage(response.get("usage")),
            raw_response=response,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Send one embeddings request over the compatible transport."""

        payload = {
            "model": request.model or self.model,
            "input": request.texts,
        }
        response = await self.transport.request_json(method="POST", path="/embeddings", payload=payload)
        vectors = [list(item.get("embedding") or []) for item in response.get("data") or []]
        dimensions = len(vectors[0]) if vectors else 0
        return EmbeddingResponse(
            provider=self.provider_name,
            model=str(response.get("model") or payload["model"]),
            vectors=vectors,
            dimensions=dimensions,
            raw_response=response,
        )

    def _serialize_message(self, message: ChatMessage) -> dict[str, str]:
        """Translate the normalized message into OpenAI-style wire format."""

        payload = {"role": message.role, "content": message.content}
        if message.name:
            payload["name"] = message.name
        return payload

    def _parse_usage(self, payload: dict[str, Any] | None) -> TokenUsage | None:
        """Normalize usage counters when the provider returns them."""

        if not payload:
            return None
        return TokenUsage(
            prompt_tokens=int(payload.get("prompt_tokens") or 0),
            completion_tokens=int(payload.get("completion_tokens") or 0),
            total_tokens=int(payload.get("total_tokens") or 0),
        )


class OpenAIProvider(OpenAICompatibleProviderBase):
    """OpenAI hosted API provider."""

    provider_name: ProviderName = "openai"
    capabilities = ProviderCapabilities(chat=True, embeddings=True, compatible_transport=True)

    def __init__(self, runtime_config: RuntimeConfig, selection: ProviderSelection) -> None:
        transport = CompatibleTransportClient(
            base_url="https://api.openai.com/v1",
            timeout_seconds=runtime_config.app.request_timeout_seconds,
            max_retries=runtime_config.app.max_retries + 1,
            api_key=runtime_config.env.openai_api_key,
        )
        super().__init__(model=selection.model, transport=transport)
