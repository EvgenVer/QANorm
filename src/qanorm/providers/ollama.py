"""Ollama provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

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
    ProviderRequestError,
    TokenUsage,
    run_provider_call,
)
from qanorm.settings import ProviderSelection, RuntimeConfig


class OllamaProvider(ChatModelProvider, EmbeddingProvider):
    """Ollama adapter for local chat and embedding models."""

    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(chat=True, embeddings=True, native_transport=True, streaming=True)

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        selection: ProviderSelection,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = selection.model
        self.timeout_seconds = runtime_config.app.request_timeout_seconds
        self.max_retries = runtime_config.app.max_retries + 1
        self._client = client or httpx.AsyncClient(base_url=runtime_config.env.ollama_base_url.rstrip("/"))

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Call the Ollama chat endpoint."""

        payload = {
            "model": request.model or self.model,
            "messages": [self._serialize_message(message) for message in request.messages],
            "stream": False,
        }
        if request.temperature is not None:
            payload["options"] = {"temperature": request.temperature}

        response = await self._request_json(path="/api/chat", payload=payload, operation_name="chat", model_name=request.model or self.model)
        message = response.get("message") or {}
        return ChatResponse(
            provider=self.provider_name,
            model=str(response.get("model") or payload["model"]),
            content=str(message.get("content") or ""),
            finish_reason=response.get("done_reason"),
            usage=self._parse_usage(response),
            raw_response=response,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Call the Ollama embeddings endpoint."""

        payload = {
            "model": request.model or self.model,
            "input": request.texts,
        }
        response = await self._request_json(
            path="/api/embed",
            payload=payload,
            operation_name="embeddings",
            model_name=request.model or self.model,
        )
        vectors = [list(item) for item in response.get("embeddings") or []]
        dimensions = len(vectors[0]) if vectors else 0
        return EmbeddingResponse(
            provider=self.provider_name,
            model=str(response.get("model") or payload["model"]),
            vectors=vectors,
            dimensions=dimensions,
            raw_response=response,
        )

    async def _request_json(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        operation_name: str,
        model_name: str,
    ) -> dict[str, Any]:
        """Issue one Ollama HTTP request with shared timeout and retry handling."""

        async def _operation() -> dict[str, Any]:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ProviderRequestError("Ollama returned a non-object JSON payload.")
            return decoded

        return await run_provider_call(
            _operation,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_retries,
            provider_name=self.provider_name,
            model_name=model_name,
            operation_name=operation_name,
        )

    def _serialize_message(self, message: ChatMessage) -> dict[str, str]:
        """Translate the normalized message into Ollama wire format."""

        payload = {"role": message.role, "content": message.content}
        if message.name:
            payload["name"] = message.name
        return payload

    def _parse_usage(self, payload: dict[str, Any]) -> TokenUsage | None:
        """Normalize Ollama token counters when they are present."""

        prompt_tokens = int(payload.get("prompt_eval_count") or 0)
        completion_tokens = int(payload.get("eval_count") or 0)
        if prompt_tokens == 0 and completion_tokens == 0:
            return None
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
