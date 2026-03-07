"""DeepSeek provider adapter with shared compatible transport support."""

from __future__ import annotations

from typing import Literal

from qanorm.providers.base import ProviderCapabilities, ProviderName
from qanorm.providers.compatible_transport import CompatibleTransportClient
from qanorm.providers.openai import OpenAICompatibleProviderBase
from qanorm.settings import ProviderSelection, RuntimeConfig


class DeepSeekProvider(OpenAICompatibleProviderBase):
    """DeepSeek adapter that keeps room for native transport changes later."""

    provider_name: ProviderName = "deepseek"
    capabilities = ProviderCapabilities(chat=True, compatible_transport=True, native_transport=True)

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        selection: ProviderSelection,
        *,
        api_mode: Literal["compatible", "native"] = "compatible",
    ) -> None:
        # A separate adapter keeps DeepSeek-specific behavior isolated from generic compatible providers.
        base_url = "https://api.deepseek.com/v1" if api_mode == "compatible" else "https://api.deepseek.com/v1"
        transport = CompatibleTransportClient(
            base_url=base_url,
            timeout_seconds=runtime_config.app.request_timeout_seconds,
            max_retries=runtime_config.app.max_retries + 1,
            api_key=runtime_config.env.deepseek_api_key,
        )
        super().__init__(model=selection.model, transport=transport)
