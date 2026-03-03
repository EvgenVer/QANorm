"""HTTP client helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import Retrying

from qanorm.settings import get_settings
from qanorm.utils.retry import build_retry_kwargs


logger = logging.getLogger(__name__)


class HttpFetcher:
    """Thin wrapper around ``httpx.Client`` with retry and rate limiting."""

    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        rate_limit_per_second: float | None = None,
        user_agent: str | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        settings = get_settings().app
        self.timeout_seconds = timeout_seconds or settings.request_timeout_seconds
        self.max_retries = settings.max_retries if max_retries is None else max_retries
        self.rate_limit_per_second = (
            settings.rate_limit_per_second if rate_limit_per_second is None else rate_limit_per_second
        )
        self.user_agent = user_agent or settings.user_agent
        self._sleep_fn = sleep_fn or time.sleep
        self._time_fn = time_fn or time.monotonic
        self._last_request_started_at: float | None = None
        self._min_request_interval = 1.0 / self.rate_limit_per_second

        if client is not None and transport is not None:
            raise ValueError("Pass either 'client' or 'transport', not both")

        if client is not None:
            self.client = client
            self._owns_client = False
        else:
            timeout = httpx.Timeout(
                connect=self.timeout_seconds,
                read=self.timeout_seconds,
                write=self.timeout_seconds,
                pool=self.timeout_seconds,
            )
            self.client = httpx.Client(
                timeout=timeout,
                transport=transport,
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
            self._owns_client = True

        self._retrying = Retrying(
            **build_retry_kwargs(
                max_attempts=self.max_retries + 1,
                min_wait_seconds=1.0,
                max_wait_seconds=max(1.0, float(self.timeout_seconds)),
                retry_on=(httpx.RequestError,),
            ),
            sleep=self._sleep_fn,
        )

    def close(self) -> None:
        """Close the underlying client if it is owned by the fetcher."""

        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def get_html(self, url: str) -> str:
        """Fetch an HTML page and return decoded text."""

        response = self._request("GET", url)
        return response.text

    def get_bytes(self, url: str) -> bytes:
        """Fetch a binary payload and return raw bytes."""

        response = self._request("GET", url)
        return response.content

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._apply_rate_limit()

        try:
            response = self._retrying(self.client.request, method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "HTTP status error while fetching %s: %s",
                url,
                exc.response.status_code,
            )
            raise
        except httpx.RequestError:
            logger.exception("HTTP request failed while fetching %s", url)
            raise

    def _apply_rate_limit(self) -> None:
        now = self._time_fn()
        if self._last_request_started_at is not None:
            elapsed = now - self._last_request_started_at
        else:
            elapsed = self._min_request_interval

        if self._last_request_started_at is not None and elapsed < self._min_request_interval:
            self._sleep_fn(self._min_request_interval - elapsed)

        self._last_request_started_at = self._time_fn()
