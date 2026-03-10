"""Smoke-check the Stage 2 web UI against a running local backend.

The script intentionally uses only the public HTTP surface:
* the Next.js web root on port 3000
* the Stage 2 API on port 8000

This keeps the smoke close to how the browser uses the system in practice.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request


WEB_BASE_URL = "http://localhost:3000"
API_BASE_URL = "http://localhost:8000"


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Execute one HTTP request and return the raw response payload."""

    payload = None
    request_headers = dict(headers or {})
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, response.read()


def _assert_contains(text: str, needle: str) -> None:
    """Fail with a precise message when the expected marker is missing."""

    if needle not in text:
        raise AssertionError(f"Expected marker not found: {needle!r}")


def main() -> int:
    """Run the smoke scenario and print a compact success report."""

    status, page_bytes = _request(WEB_BASE_URL)
    if status != 200:
        raise AssertionError(f"Unexpected web status: {status}")
    page_text = page_bytes.decode("utf-8", errors="replace")
    # These strings come from the visible chat shell and prove the UI bundle was served.
    _assert_contains(page_text, "QANorm Stage 2")
    _assert_contains(page_text, "Engineering assistant")
    _assert_contains(page_text, "Session memory stays scoped to this chat")

    status, session_bytes = _request(
        f"{API_BASE_URL}/sessions",
        method="POST",
        body={"channel": "web"},
    )
    if status != 200:
        raise AssertionError(f"Session creation failed with status {status}")
    session_payload = json.loads(session_bytes.decode("utf-8"))
    session_id = str(session_payload["id"])

    status, history_bytes = _request(f"{API_BASE_URL}/sessions/{session_id}/messages")
    if status != 200:
        raise AssertionError(f"Message history request failed with status {status}")
    history_payload = json.loads(history_bytes.decode("utf-8"))
    if history_payload:
        raise AssertionError("New session must start with an empty message history.")

    status, query_bytes = _request(
        f"{API_BASE_URL}/sessions/{session_id}/queries",
        method="POST",
        body={"content": "List the main fire-safety design considerations for a public building."},
    )
    if status != 200:
        raise AssertionError(f"Query creation failed with status {status}")
    query_payload = json.loads(query_bytes.decode("utf-8"))
    query_id = str(query_payload["query_id"])

    status, detail_bytes = _request(f"{API_BASE_URL}/queries/{query_id}")
    if status != 200:
        raise AssertionError(f"Query details request failed with status {status}")
    detail_payload = json.loads(detail_bytes.decode("utf-8"))
    if detail_payload["id"] != query_id:
        raise AssertionError("Query details returned an unexpected query identifier.")
    if detail_payload["session_id"] != session_id:
        raise AssertionError("Query details returned an unexpected session identifier.")

    # Read just the bootstrap event from the SSE stream. The web UI depends on this
    # handshake to know that streaming is connected.
    sse_request = urllib.request.Request(
        f"{API_BASE_URL}/queries/{query_id}/events",
        headers={"Accept": "text/event-stream"},
        method="GET",
    )
    with urllib.request.urlopen(sse_request, timeout=15) as response:
        first_chunk = response.read(256).decode("utf-8", errors="replace")
    _assert_contains(first_chunk, "stream_ready")

    print(
        json.dumps(
            {
                "status": "ok",
                "session_id": session_id,
                "query_id": query_id,
                "ui_markers_checked": 3,
                "sse_bootstrap": "stream_ready",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError, urllib.error.URLError) as exc:
        print(f"web-ui smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
