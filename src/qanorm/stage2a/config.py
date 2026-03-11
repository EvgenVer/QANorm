"""Stage 2A YAML configuration loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from qanorm.settings import DEFAULT_CONFIG_DIR, _load_yaml_file


class Stage2AProviderApiConfig(BaseModel):
    """Provider API environment wiring."""

    base_url_env: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)


class Stage2AProviderConfig(BaseModel):
    """Primary provider configuration."""

    name: str = Field(min_length=1)
    api: Stage2AProviderApiConfig


class Stage2AModelConfig(BaseModel):
    """Configured model names for Stage 2A."""

    controller: str = Field(min_length=1)
    composer: str = Field(min_length=1)
    verifier: str = Field(min_length=1)
    reranker: str = Field(min_length=1)
    embeddings: str = Field(min_length=1)


class Stage2AIndexingConfig(BaseModel):
    """Derived indexing thresholds."""

    semantic_block_min_chars: int = Field(ge=1)
    semantic_block_target_chars: int = Field(ge=1)
    semantic_block_max_chars: int = Field(ge=1)
    semantic_block_max_nodes: int = Field(ge=1)
    document_card_max_headings: int = Field(ge=1)
    embed_batch_size: int = Field(ge=1)


class Stage2AEmbeddingsConfig(BaseModel):
    """Embedding behavior and estimation knobs."""

    query_task_type: str = Field(min_length=1)
    document_task_type: str = Field(min_length=1)
    output_dimensionality: int = Field(ge=1)
    estimated_text_input_price_per_million_tokens: float | None = Field(default=None, ge=0.0)
    average_chars_per_token: float = Field(default=4.0, gt=0.0)


class Stage2AConfig(BaseModel):
    """Normalized Stage 2A configuration bundle."""

    provider: Stage2AProviderConfig
    models: Stage2AModelConfig
    indexing: Stage2AIndexingConfig
    embeddings: Stage2AEmbeddingsConfig


def load_stage2a_config(config_path: Path | None = None) -> Stage2AConfig:
    """Load and validate the Stage 2A YAML config."""

    configured_path = os.environ.get("QANORM_STAGE2A_CONFIG_PATH")
    path = Path(configured_path) if configured_path else (config_path or DEFAULT_CONFIG_DIR / "stage2a.yaml")
    payload = _load_yaml_file(path)
    if "stage2a" not in payload:
        raise ValueError(f"{path} must contain a 'stage2a' section")
    return Stage2AConfig.model_validate(payload["stage2a"])


@lru_cache(maxsize=1)
def get_stage2a_config() -> Stage2AConfig:
    """Return cached Stage 2A configuration."""

    return load_stage2a_config()

