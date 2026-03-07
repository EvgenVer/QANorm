"""Qwen provider adapter with shared compatible transport support."""

from __future__ import annotations

from typing import Literal

from qanorm.providers.base import ProviderCapabilities, ProviderName
from qanorm.providers.compatible_transport import CompatibleTransportClient
from qanorm.providers.openai import OpenAICompatibleProviderBase
from qanorm.settings import ProviderSelection, RuntimeConfig


class QwenProvider(OpenAICompatibleProviderBase):
    """Qwen adapter that can use a compatible transport or a vendor-native base URL."""

    provider_name: ProviderName = "qwen"
    capabilities = ProviderCapabilities(chat=True, compatible_transport=True, native_transport=True)

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        selection: ProviderSelection,
        *,
        api_mode: Literal["compatible", "native"] = "compatible",
    ) -> None:
        # The adapter stays separate so later native integration can diverge without touching the registry contract.
        base_url = (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            if api_mode == "compatible"
            else "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        transport = CompatibleTransportClient(
            base_url=base_url,
            timeout_seconds=runtime_config.app.request_timeout_seconds,
            max_retries=runtime_config.app.max_retries + 1,
            api_key=runtime_config.env.qwen_api_key,
        )
        super().__init__(model=selection.model, transport=transport)
