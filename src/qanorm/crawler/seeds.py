"""Seed discovery helpers."""

from __future__ import annotations

from collections.abc import Iterable

from qanorm.settings import SourcesConfig, get_settings


def load_seed_urls(config: SourcesConfig | None = None) -> list[str]:
    """Load the configured seed URLs."""

    sources = config or get_settings().sources
    return list(sources.seed_urls)


def iter_seed_urls(config: SourcesConfig | None = None) -> Iterable[str]:
    """Iterate over configured seed URLs."""

    yield from load_seed_urls(config=config)
