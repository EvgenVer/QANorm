"""Anthropic provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from qanorm.providers.base import (
    ChatMessage,
    ChatModelProvider,
    ChatRequest,
    ChatResponse,
    ProviderCapabilities,
    ProviderName,
    ProviderRequestError,
    TokenUsage,
    run_provider_call,
)
from qanorm.settings import ProviderSelection, RuntimeConfig


class AnthropicProvider(ChatModelProvider):
    """Anthropic Messages API adapter for chat completions."""

    provider_name: ProviderName = "anthropic"
    capabilities = ProviderCapabilities(chat=True, native_transport=True)

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        selection: ProviderSelection,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = selection.model
        self.api_key = runtime_config.env.anthropic_api_key
        self.timeout_seconds = runtime_config.app.request_timeout_seconds
        self.max_retries = runtime_config.app.max_retries + 1
        self._client = client or httpx.AsyncClient(base_url="https://api.anthropic.com")

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Call the Anthropic Messages API."""

        payload = {
            "model": request.model or self.model,
            "messages": [self._serialize_message(message) for message in request.messages if message.role != "system"],
            "system": self._extract_system_prompt(request.messages),
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        else:
            payload["max_tokens"] = 1024
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        response = await self._request_json(payload=payload, model_name=request.model or self.model)
        content_blocks = response.get("content") or []
        text = "\n".join(str(block.get("text") or "") for block in content_blocks if block.get("type") == "text").strip()
        return ChatResponse(
            provider=self.provider_name,
            model=str(response.get("model") or payload["model"]),
            content=text,
            finish_reason=response.get("stop_reason"),
            usage=self._parse_usage(response.get("usage")),
            raw_response=response,
        )

    async def _request_json(self, *, payload: dict[str, Any], model_name: str) -> dict[str, Any]:
        """Issue one Anthropic request with shared timeout and retry handling."""

        async def _operation() -> dict[str, Any]:
            response = await self._client.post(
                "/v1/messages",
                headers={
                    "x-api-key": self.api_key or "",
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            )
            response.raise_for_status()
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ProviderRequestError("Anthropic returned a non-object JSON payload.")
            return decoded

        return await run_provider_call(
            _operation,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_retries,
            provider_name=self.provider_name,
            model_name=model_name,
            operation_name="chat",
        )

    def _serialize_message(self, message: ChatMessage) -> dict[str, Any]:
        """Translate the normalized message into Anthropic wire format."""

        return {
            "role": "assistant" if message.role == "assistant" else "user",
            "content": [{"type": "text", "text": message.content}],
        }

    def _extract_system_prompt(self, messages: list[ChatMessage]) -> str | None:
        """Merge system messages because Anthropic accepts them separately."""

        system_parts = [message.content for message in messages if message.role == "system"]
        if not system_parts:
            return None
        return "\n\n".join(system_parts)

    def _parse_usage(self, payload: dict[str, Any] | None) -> TokenUsage | None:
        """Normalize Anthropic usage counters when available."""

        if not payload:
            return None
        input_tokens = int(payload.get("input_tokens") or 0)
        output_tokens = int(payload.get("output_tokens") or 0)
        return TokenUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
