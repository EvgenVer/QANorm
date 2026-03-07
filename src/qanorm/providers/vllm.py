"""vLLM provider adapter."""

from __future__ import annotations

from qanorm.providers.base import ProviderCapabilities, ProviderName
from qanorm.providers.compatible_transport import CompatibleTransportClient
from qanorm.providers.openai import OpenAICompatibleProviderBase
from qanorm.settings import ProviderSelection, RuntimeConfig


class VLLMProvider(OpenAICompatibleProviderBase):
    """vLLM adapter over an OpenAI-compatible server."""

    provider_name: ProviderName = "vllm"
    capabilities = ProviderCapabilities(chat=True, embeddings=True, compatible_transport=True, streaming=True)

    def __init__(self, runtime_config: RuntimeConfig, selection: ProviderSelection) -> None:
        transport = CompatibleTransportClient(
            base_url=runtime_config.env.vllm_base_url,
            timeout_seconds=runtime_config.app.request_timeout_seconds,
            max_retries=runtime_config.app.max_retries + 1,
        )
        super().__init__(model=selection.model, transport=transport)
