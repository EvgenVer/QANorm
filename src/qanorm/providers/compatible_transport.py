"""Reusable OpenAI-compatible transport helpers for provider adapters."""

from __future__ import annotations

from typing import Any

import httpx

from qanorm.providers.base import ProviderRequestError, run_provider_call


class CompatibleTransportClient:
    """Thin JSON client for providers that expose an OpenAI-compatible API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        api_key: str | None = None,
        default_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.api_key = api_key
        self.default_headers = default_headers or {}
        # Allow injection in tests so adapters can be validated without network access.
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    async def request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        operation_name: str = "request",
    ) -> dict[str, Any]:
        """Send one JSON request and return the decoded JSON response."""

        request_headers = dict(self.default_headers)
        if self.api_key:
            request_headers.setdefault("Authorization", f"Bearer {self.api_key}")
        if headers:
            request_headers.update(headers)

        async def _operation() -> dict[str, Any]:
            response = await self._client.request(
                method=method.upper(),
                url=self._build_path(path),
                json=payload,
                headers=request_headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ProviderRequestError("Provider returned a non-object JSON payload.")
            return decoded

        return await run_provider_call(
            _operation,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_retries,
            provider_name=provider_name,
            model_name=model_name,
            operation_name=operation_name,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""

        await self._client.aclose()

    def _build_path(self, path: str) -> str:
        """Normalize relative request paths against the configured base URL."""

        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"
