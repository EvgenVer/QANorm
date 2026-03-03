from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from qanorm.fetchers.http import HttpFetcher


def test_get_html_returns_response_text_and_sends_user_agent() -> None:
    observed_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_headers["User-Agent"] = request.headers["User-Agent"]
        return httpx.Response(200, text="<html>ok</html>")

    fetcher = HttpFetcher(transport=httpx.MockTransport(handler), user_agent="QANormTest/1.0")
    try:
        assert fetcher.get_html("https://example.test/page") == "<html>ok</html>"
    finally:
        fetcher.close()

    assert observed_headers["User-Agent"] == "QANormTest/1.0"


def test_get_bytes_returns_binary_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.7")

    fetcher = HttpFetcher(transport=httpx.MockTransport(handler))
    try:
        assert fetcher.get_bytes("https://example.test/file.pdf") == b"%PDF-1.7"
    finally:
        fetcher.close()


def test_fetcher_retries_request_errors() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("temporary failure", request=request)
        return httpx.Response(200, text="ok")

    sleep_calls: list[float] = []
    fetcher = HttpFetcher(
        transport=httpx.MockTransport(handler),
        max_retries=1,
        sleep_fn=sleep_calls.append,
    )
    try:
        assert fetcher.get_html("https://example.test/retry") == "ok"
    finally:
        fetcher.close()

    assert call_count == 2
    assert sleep_calls


def test_fetcher_logs_http_status_errors(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable", request=request)

    fetcher = HttpFetcher(transport=httpx.MockTransport(handler))
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(httpx.HTTPStatusError):
                fetcher.get_html("https://example.test/unavailable")
    finally:
        fetcher.close()

    assert "HTTP status error while fetching" in caplog.text


def test_fetcher_applies_rate_limit_before_second_request() -> None:
    current_time = 0.0
    sleep_calls: list[float] = []

    def fake_time() -> float:
        return current_time

    def fake_sleep(duration: float) -> None:
        nonlocal current_time
        sleep_calls.append(duration)
        current_time += duration

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    fetcher = HttpFetcher(
        transport=httpx.MockTransport(handler),
        rate_limit_per_second=2.0,
        sleep_fn=fake_sleep,
        time_fn=fake_time,
    )
    try:
        assert fetcher.get_html("https://example.test/first") == "ok"
        assert fetcher.get_html("https://example.test/second") == "ok"
    finally:
        fetcher.close()

    assert sleep_calls == [0.5]


def test_fetcher_rejects_client_and_transport_together() -> None:
    client = httpx.Client()
    try:
        with pytest.raises(ValueError):
            HttpFetcher(client=client, transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    finally:
        client.close()
