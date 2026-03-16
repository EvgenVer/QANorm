from __future__ import annotations

from pathlib import Path

import pytest

from qanorm.stage2a.config import load_stage2a_config
from qanorm.stage2a.providers import MissingProviderSecretError, build_stage2a_dspy_models


class _FakeLM:
    def __init__(self, model: str, **kwargs) -> None:
        self.model = model
        self.kwargs = kwargs


def test_build_stage2a_dspy_models_bootstraps_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_stage2a_config(Path("configs/stage2a.yaml"))
    cache_calls: list[dict[str, object]] = []
    configure_calls: list[object] = []

    monkeypatch.setenv("QANORM_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("QANORM_GEMINI_API_BASE_URL", "https://example.test/v1beta")
    monkeypatch.setenv("QANORM_DSPY_CACHE_DIR", ".cache/test-dspy")
    monkeypatch.setattr("qanorm.stage2a.providers.dspy.LM", _FakeLM)
    monkeypatch.setattr("qanorm.stage2a.providers.dspy.configure", lambda **kwargs: configure_calls.append(kwargs["lm"]))
    monkeypatch.setattr("qanorm.stage2a.providers.configure_cache", lambda **kwargs: cache_calls.append(kwargs))

    bundle = build_stage2a_dspy_models(config)

    assert bundle.provider_name == "gemini"
    assert bundle.controller.model == f"gemini/{config.models.controller}"
    assert bundle.composer.model == f"gemini/{config.models.composer}"
    assert bundle.verifier.kwargs["timeout"] == 90
    assert bundle.reranker.kwargs["num_retries"] == 4
    assert bundle.controller.kwargs["api_key"] == "test-key"
    assert cache_calls[0]["enable_disk_cache"] is True
    assert str(cache_calls[0]["disk_cache_dir"]).endswith(".cache\\test-dspy")
    assert configure_calls[0] is bundle.controller


def test_build_stage2a_dspy_models_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_stage2a_config(Path("configs/stage2a.yaml"))

    monkeypatch.setenv("QANORM_GEMINI_API_BASE_URL", "https://example.test/v1beta")
    monkeypatch.setenv("QANORM_DSPY_CACHE_DIR", ".cache/test-dspy")
    monkeypatch.delenv("QANORM_GEMINI_API_KEY", raising=False)

    with pytest.raises(MissingProviderSecretError):
        build_stage2a_dspy_models(config)
