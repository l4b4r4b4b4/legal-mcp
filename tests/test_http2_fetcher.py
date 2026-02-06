"""Unit tests for the reusable HTTP/2 fetcher (async API).

These tests validate:
- Retry behavior for transient HTTP status codes (e.g., 429)
- Non-retry behavior for non-retryable errors (e.g., 404)
- Bounded read behavior (enforces max_bytes, returns truncation flag)

Design notes:
- Uses `httpx.MockTransport` with an `AsyncClient` injected into `Http2Fetcher`.
- Does not require real network access.
"""

from __future__ import annotations

import httpx
import pytest
from legal_mcp.net.http2_fetcher import (
    Http2Fetcher,
    Http2FetcherConfig,
    Http2FetchHttpStatusError,
)


@pytest.mark.asyncio
async def test_get_bytes_retries_on_429_then_succeeds() -> None:
    """It should retry on HTTP 429 and eventually return bytes on success."""
    call_count: dict[str, int] = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] < 3:
            return httpx.Response(429, content=b"rate-limited")
        return httpx.Response(200, content=b"ok")

    transport = httpx.MockTransport(handler)

    config = Http2FetcherConfig(
        retry_attempts=3,
        base_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        jitter_seconds=0.0,
    )

    async with (
        httpx.AsyncClient(transport=transport) as client,
        Http2Fetcher(config=config, client=client) as fetcher,
    ):
        response = await fetcher.get_bytes(
            "https://example.invalid/resource",
            max_bytes=10,
            range_request=False,
        )

    assert response.status_code == 200
    assert response.content == b"ok"
    assert call_count["count"] == 3


@pytest.mark.asyncio
async def test_get_bytes_does_not_retry_on_404() -> None:
    """It should not retry on non-retryable status codes like 404."""
    call_count: dict[str, int] = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        return httpx.Response(404, content=b"not found")

    transport = httpx.MockTransport(handler)

    config = Http2FetcherConfig(
        retry_attempts=5,
        base_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        jitter_seconds=0.0,
    )

    async with (
        httpx.AsyncClient(transport=transport) as client,
        Http2Fetcher(config=config, client=client) as fetcher,
    ):
        with pytest.raises(Http2FetchHttpStatusError):
            await fetcher.get_bytes(
                "https://example.invalid/missing",
                max_bytes=100,
                range_request=False,
            )

    assert call_count["count"] == 1


@pytest.mark.asyncio
async def test_get_bytes_enforces_max_bytes_and_sets_truncated() -> None:
    """It should respect max_bytes and set `content_truncated` when payload is larger."""
    payload = b"x" * 50

    def handler(request: httpx.Request) -> httpx.Response:
        # Return a normal 200 response with a payload larger than max_bytes.
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/octet-stream"},
        )

    transport = httpx.MockTransport(handler)

    config = Http2FetcherConfig(
        retry_attempts=1,
        base_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        jitter_seconds=0.0,
    )

    async with (
        httpx.AsyncClient(transport=transport) as client,
        Http2Fetcher(config=config, client=client) as fetcher,
    ):
        response = await fetcher.get_bytes(
            "https://example.invalid/large",
            max_bytes=10,
            range_request=False,  # force stream-based truncation path
        )

    assert response.status_code == 200
    assert response.content == b"x" * 10
    assert response.content_truncated is True


@pytest.mark.asyncio
async def test_get_text_decodes_utf8_and_returns_response() -> None:
    """get_text should return a response whose .text() decodes the payload."""
    payload_text = "Berliner Vorschriften- und Rechtsprechungsdatenbank"
    payload_bytes = payload_text.encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload_bytes,
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    transport = httpx.MockTransport(handler)

    config = Http2FetcherConfig(
        retry_attempts=1,
        base_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        jitter_seconds=0.0,
    )

    async with (
        httpx.AsyncClient(transport=transport) as client,
        Http2Fetcher(config=config, client=client) as fetcher,
    ):
        response = await fetcher.get_text(
            "https://example.invalid/txt",
            max_bytes=10_000,
            range_request=False,
        )

    assert response.status_code == 200
    assert payload_text in response.text()
