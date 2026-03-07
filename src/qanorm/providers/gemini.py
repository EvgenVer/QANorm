"""Google Gemini provider adapters."""

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


class GeminiProvider(ChatModelProvider, EmbeddingProvider):
    """Gemini API adapter for chat and embeddings."""

    provider_name: ProviderName = "gemini"
    capabilities = ProviderCapabilities(chat=True, embeddings=True, native_transport=True)

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        selection: ProviderSelection,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = selection.model
        self.api_key = runtime_config.env.gemini_api_key
        self.timeout_seconds = runtime_config.app.request_timeout_seconds
        self.max_retries = runtime_config.app.max_retries + 1
        self._client = client or httpx.AsyncClient(base_url="https://generativelanguage.googleapis.com")

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Call the Gemini generateContent endpoint."""

        payload = {
            "contents": self._serialize_messages(request.messages),
        }
        system_prompt = self._extract_system_prompt(request.messages)
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
        if request.temperature is not None or request.max_tokens is not None:
            payload["generationConfig"] = {
                key: value
                for key, value in {
                    "temperature": request.temperature,
                    "maxOutputTokens": request.max_tokens,
                }.items()
                if value is not None
            }

        response = await self._request_json(
            path=f"/v1beta/models/{request.model or self.model}:generateContent",
            payload=payload,
        )
        candidate = (response.get("candidates") or [{}])[0]
        content = self._extract_candidate_text(candidate)
        return ChatResponse(
            provider=self.provider_name,
            model=request.model or self.model,
            content=content,
            finish_reason=candidate.get("finishReason"),
            usage=self._parse_usage(response.get("usageMetadata")),
            raw_response=response,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Call the Gemini batch embedding endpoint."""

        payload = {
            "requests": [
                {
                    "model": f"models/{request.model or self.model}",
                    "content": {"parts": [{"text": text}]},
                }
                for text in request.texts
            ]
        }
        response = await self._request_json(
            path=f"/v1beta/models/{request.model or self.model}:batchEmbedContents",
            payload=payload,
        )
        embeddings = response.get("embeddings") or []
        vectors = [list(item.get("values") or []) for item in embeddings]
        dimensions = len(vectors[0]) if vectors else 0
        return EmbeddingResponse(
            provider=self.provider_name,
            model=request.model or self.model,
            vectors=vectors,
            dimensions=dimensions,
            raw_response=response,
        )

    async def _request_json(self, *, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Issue one Gemini HTTP request with shared timeout and retry settings."""

        async def _operation() -> dict[str, Any]:
            response = await self._client.post(
                path,
                params={"key": self.api_key},
                json=payload,
            )
            response.raise_for_status()
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ProviderRequestError("Gemini returned a non-object JSON payload.")
            return decoded

        return await run_provider_call(
            _operation,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_retries,
        )

    def _serialize_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Translate non-system messages into Gemini content blocks."""

        payload: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            role = "model" if message.role == "assistant" else "user"
            payload.append({"role": role, "parts": [{"text": message.content}]})
        return payload

    def _extract_system_prompt(self, messages: list[ChatMessage]) -> str | None:
        """Merge system messages because Gemini expects them separately."""

        system_parts = [message.content for message in messages if message.role == "system"]
        if not system_parts:
            return None
        return "\n\n".join(system_parts)

    def _extract_candidate_text(self, candidate: dict[str, Any]) -> str:
        """Flatten the first candidate content into plain text."""

        parts = ((candidate.get("content") or {}).get("parts")) or []
        text_parts = [str(part.get("text") or "") for part in parts if part.get("text")]
        return "\n".join(text_parts).strip()

    def _parse_usage(self, payload: dict[str, Any] | None) -> TokenUsage | None:
        """Normalize Gemini usage counters when available."""

        if not payload:
            return None
        return TokenUsage(
            prompt_tokens=int(payload.get("promptTokenCount") or 0),
            completion_tokens=int(payload.get("candidatesTokenCount") or 0),
            total_tokens=int(payload.get("totalTokenCount") or 0),
        )
