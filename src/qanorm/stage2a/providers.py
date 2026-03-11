"""DSPy bootstrap and provider abstraction for the Stage 2A agent layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import dspy
from dspy.clients import configure_cache

from qanorm.settings import PROJECT_ROOT
from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config


class Stage2AProviderError(RuntimeError):
    """Base provider bootstrap error."""


class MissingProviderSecretError(Stage2AProviderError):
    """Raised when a required provider secret is missing."""


class UnsupportedProviderError(Stage2AProviderError):
    """Raised when no provider adapter is registered for the config."""


@dataclass(frozen=True, slots=True)
class Stage2AResolvedProviderConfig:
    """Resolved provider credentials and cache paths."""

    provider_name: str
    api_base_url: str
    api_key: str
    cache_enabled: bool
    cache_dir: Path


@dataclass(frozen=True, slots=True)
class Stage2ADspyModelBundle:
    """All DSPy LM handles used by the Stage 2A agent layer."""

    provider_name: str
    controller: object
    composer: object
    verifier: object
    reranker: object


class Stage2ALMProvider(Protocol):
    """Provider adapter contract for Stage 2A LM bootstrap."""

    name: str

    def build_bundle(self, config: Stage2AConfig) -> Stage2ADspyModelBundle:
        """Create all LM handles for the configured provider."""


class GeminiDSPyProvider:
    """Gemini adapter implemented through DSPy/LiteLLM."""

    name = "gemini"

    def build_bundle(self, config: Stage2AConfig) -> Stage2ADspyModelBundle:
        """Create and register all DSPy LMs needed by Stage 2A."""

        resolved = resolve_provider_environment(config)
        configure_dspy_cache(config, resolved)

        controller = self._build_lm(
            config.models.controller,
            temperature=config.generation.controller_temperature,
            max_tokens=config.generation.max_answer_tokens,
            resolved=resolved,
            retries=config.runtime.retry_attempts,
            timeout_seconds=config.runtime.request_timeout_seconds,
            cache_enabled=config.dspy.cache_enabled,
        )
        composer = self._build_lm(
            config.models.composer,
            temperature=config.generation.composer_temperature,
            max_tokens=config.generation.max_answer_tokens,
            resolved=resolved,
            retries=config.runtime.retry_attempts,
            timeout_seconds=config.runtime.request_timeout_seconds,
            cache_enabled=config.dspy.cache_enabled,
        )
        verifier = self._build_lm(
            config.models.verifier,
            temperature=config.generation.verifier_temperature,
            max_tokens=config.generation.max_verifier_tokens,
            resolved=resolved,
            retries=config.runtime.retry_attempts,
            timeout_seconds=config.runtime.request_timeout_seconds,
            cache_enabled=config.dspy.cache_enabled,
        )
        reranker = self._build_lm(
            config.models.reranker,
            temperature=0.0,
            max_tokens=config.generation.max_verifier_tokens,
            resolved=resolved,
            retries=config.runtime.retry_attempts,
            timeout_seconds=config.runtime.request_timeout_seconds,
            cache_enabled=config.dspy.cache_enabled,
        )

        # Keep the controller LM as the DSPy default. Later modules can override via dspy.context(...).
        dspy.configure(lm=controller)

        return Stage2ADspyModelBundle(
            provider_name=self.name,
            controller=controller,
            composer=composer,
            verifier=verifier,
            reranker=reranker,
        )

    def _build_lm(
        self,
        model_name: str,
        *,
        temperature: float,
        max_tokens: int,
        resolved: Stage2AResolvedProviderConfig,
        retries: int,
        timeout_seconds: int,
        cache_enabled: bool,
    ) -> object:
        """Build one DSPy LM handle with consistent Gemini settings."""

        qualified_model_name = qualify_model_name(resolved.provider_name, model_name)
        return dspy.LM(
            qualified_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            cache=cache_enabled,
            num_retries=retries,
            api_key=resolved.api_key,
            api_base=resolved.api_base_url,
            timeout=timeout_seconds,
        )


_PROVIDER_REGISTRY: dict[str, Stage2ALMProvider] = {
    GeminiDSPyProvider.name: GeminiDSPyProvider(),
}


def qualify_model_name(provider_name: str, model_name: str) -> str:
    """Normalize model ids into the provider/model form expected by DSPy."""

    if "/" in model_name:
        return model_name
    return f"{provider_name}/{model_name}"


def resolve_provider_environment(config: Stage2AConfig) -> Stage2AResolvedProviderConfig:
    """Resolve provider secrets and cache paths from the environment."""

    base_url = _require_env_value(config.provider.api.base_url_env)
    api_key = _require_env_value(config.provider.api.api_key_env)
    cache_dir_raw = _require_env_value(config.dspy.cache_dir_env) if config.dspy.cache_enabled else ".dspy_cache"
    cache_dir = _normalize_path(cache_dir_raw)
    return Stage2AResolvedProviderConfig(
        provider_name=config.provider.name,
        api_base_url=base_url,
        api_key=api_key,
        cache_enabled=config.dspy.cache_enabled,
        cache_dir=cache_dir,
    )


def configure_dspy_cache(config: Stage2AConfig, resolved: Stage2AResolvedProviderConfig) -> None:
    """Configure DSPy cache according to the Stage 2A runtime settings."""

    resolved.cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DSPY_CACHEDIR"] = str(resolved.cache_dir)
    configure_cache(
        enable_disk_cache=config.dspy.cache_enabled,
        enable_memory_cache=config.dspy.cache_enabled,
        disk_cache_dir=str(resolved.cache_dir),
    )


def build_stage2a_dspy_models(config: Stage2AConfig | None = None) -> Stage2ADspyModelBundle:
    """Bootstrap the configured provider and return all DSPy LM handles."""

    resolved_config = config or get_stage2a_config()
    provider = _PROVIDER_REGISTRY.get(resolved_config.provider.name)
    if provider is None:
        raise UnsupportedProviderError(f"Unsupported Stage 2A provider: {resolved_config.provider.name}")
    return provider.build_bundle(resolved_config)


def _require_env_value(env_name: str) -> str:
    value = os.environ.get(env_name)
    if value is None or not value.strip():
        raise MissingProviderSecretError(f"Environment variable {env_name} must be set")
    return value.strip()


def _normalize_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()
