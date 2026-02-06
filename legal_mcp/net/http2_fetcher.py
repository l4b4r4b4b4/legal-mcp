"""Reusable HTTP/2 fetcher with bounded reads and retry/backoff.

This module provides a small, dependency-light networking layer that mirrors the
high-throughput strategy used for the federal German law downloader:

- HTTP/2 multiplexing via `httpx.AsyncClient(http2=True)`
- Connection pooling
- Bounded concurrency (semaphore)
- Retry + exponential backoff (with respect for `Retry-After` where possible)
- Bounded reads / range requests to avoid downloading large resources (e.g. JS bundles)

It is intentionally generic: it does not contain Berlin-portal-specific logic.

Security / safety notes:
- Callers should avoid logging full URLs if they may contain tokens.
- Bounded reads are enforced to prevent large downloads during probing.

Example:
    >>> import asyncio
    >>> from legal_mcp.net.http2_fetcher import Http2Fetcher, Http2FetcherConfig
    >>>
    >>> async def main() -> None:
    ...     config = Http2FetcherConfig(max_concurrent_requests=20)
    ...     async with Http2Fetcher(config=config) as fetcher:
    ...         response = await fetcher.get_text(
    ...             "https://www.gesetze.berlin.de",
    ...             max_bytes=30_000,
    ...         )
    ...         print(response.status_code, response.content_type)
    ...
    >>> asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import dataclasses
import email.utils
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": _DEFAULT_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    # Let httpx handle decompression; keep header explicit for realism/parity.
    "Accept-Encoding": "gzip, deflate, br",
}


class Http2FetchError(RuntimeError):
    """Base exception for HTTP/2 fetcher failures."""


class Http2FetchTimeoutError(Http2FetchError):
    """Raised when a request times out after retries."""


class Http2FetchNetworkError(Http2FetchError):
    """Raised when network-related errors persist after retries."""


class Http2FetchHttpStatusError(Http2FetchError):
    """Raised when server returns a non-success status after retries."""


@dataclass(frozen=True, slots=True)
class Http2FetcherConfig:
    """Configuration for :class:`Http2Fetcher`.

    Attributes:
        max_concurrent_requests: Maximum number of in-flight requests.
        max_connections: Connection pool max connections.
        max_keepalive_connections: Keep-alive pool size.
        timeout_seconds: Per-request timeout (seconds).
        retry_attempts: Total attempts (initial try + retries).
        base_backoff_seconds: Base delay for exponential backoff.
        max_backoff_seconds: Maximum sleep between retries.
        jitter_seconds: Max jitter added to backoff (uniform [0, jitter]).
        retry_on_status_codes: HTTP status codes that should be retried.
        follow_redirects: Whether to follow redirects.
        default_headers: Default headers applied to all requests.
        http2: Enable HTTP/2.
    """

    max_concurrent_requests: int = 50
    max_connections: int = 50
    max_keepalive_connections: int = 20
    timeout_seconds: float = 30.0

    retry_attempts: int = 4
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 10.0
    jitter_seconds: float = 0.25

    retry_on_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)
    follow_redirects: bool = True
    default_headers: Mapping[str, str] = dataclasses.field(
        default_factory=lambda: dict(DEFAULT_HEADERS)
    )
    http2: bool = True


@dataclass(frozen=True, slots=True)
class BoundedResponse:
    """Response container for bounded fetch methods.

    Attributes:
        url: Final URL (after redirects, if enabled).
        status_code: HTTP status code.
        headers: Response headers (as returned by httpx).
        content: Raw response bytes (bounded by max_bytes).
        content_truncated: True if response was larger than the bound and was truncated.
    """

    url: str
    status_code: int
    headers: httpx.Headers
    content: bytes
    content_truncated: bool

    @property
    def content_type(self) -> str | None:
        """Content-Type header value."""
        return self.headers.get("content-type")

    def text(self, encoding: str | None = None) -> str:
        """Decode content as text.

        Args:
            encoding: Optional override encoding.

        Returns:
            Decoded text. Errors are replaced to keep probing robust.
        """
        if encoding is not None:
            return self.content.decode(encoding, errors="replace")

        detected_encoding = _guess_encoding_from_content_type(self.content_type)
        if detected_encoding:
            return self.content.decode(detected_encoding, errors="replace")

        return self.content.decode("utf-8", errors="replace")


def _guess_encoding_from_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    # Very small parser; we only care about `charset=...`.
    lowered = content_type.lower()
    if "charset=" not in lowered:
        return None
    charset_part = lowered.split("charset=", 1)[1].strip()
    # Strip common delimiters.
    for delimiter in (";", ",", " "):
        if delimiter in charset_part:
            charset_part = charset_part.split(delimiter, 1)[0]
    charset_part = charset_part.strip("\"'")
    return charset_part or None


def _parse_retry_after_seconds(retry_after_value: str | None) -> float | None:
    """Parse Retry-After header value.

    Returns:
        Seconds to wait, or None if parsing fails / header absent.

    Supports:
      - integer seconds
      - HTTP-date (RFC 7231)
    """
    if not retry_after_value:
        return None

    retry_after_value = retry_after_value.strip()
    if retry_after_value.isdigit():
        return float(int(retry_after_value))

    try:
        parsed_datetime = email.utils.parsedate_to_datetime(retry_after_value)
        if parsed_datetime is None:
            return None
        if parsed_datetime.tzinfo is None:
            parsed_datetime = parsed_datetime.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta_seconds = (parsed_datetime - now).total_seconds()
        return max(0.0, delta_seconds)
    except (TypeError, ValueError, OverflowError):
        return None


def _compute_backoff_seconds(
    *,
    attempt_index: int,
    base_backoff_seconds: float,
    max_backoff_seconds: float,
    jitter_seconds: float,
) -> float:
    backoff_seconds = base_backoff_seconds * (2**attempt_index)
    backoff_seconds = min(backoff_seconds, max_backoff_seconds)
    if jitter_seconds <= 0:
        return backoff_seconds
    # Simple deterministic-ish jitter using current time fractional component
    # to avoid importing random in core networking module.
    fractional = time.time() % 1.0
    jitter = min(jitter_seconds, jitter_seconds * fractional)
    return backoff_seconds + jitter


class Http2Fetcher:
    """Async HTTP fetcher with HTTP/2 multiplexing, bounded reads, and retries.

    This class owns an `httpx.AsyncClient` and should be used as an async context manager.

    Example:
        >>> import asyncio
        >>> from legal_mcp.net.http2_fetcher import Http2Fetcher
        >>>
        >>> async def main() -> None:
        ...     async with Http2Fetcher() as fetcher:
        ...         response = await fetcher.get_bytes(
        ...             "https://example.com",
        ...             max_bytes=10_000,
        ...         )
        ...         print(response.status_code, len(response.content))
        ...
        >>> asyncio.run(main())
    """

    def __init__(
        self,
        config: Http2FetcherConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or Http2FetcherConfig()
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent_requests)
        self._owns_client = client is None
        self._client = client

    async def __aenter__(self) -> Http2Fetcher:
        """Enter the async context manager and initialize the HTTP client if needed.

        Returns:
            The fetcher instance.
        """
        if self._client is None:
            limits = httpx.Limits(
                max_connections=self._config.max_connections,
                max_keepalive_connections=self._config.max_keepalive_connections,
            )
            self._client = httpx.AsyncClient(
                headers=dict(self._config.default_headers),
                timeout=self._config.timeout_seconds,
                limits=limits,
                http2=self._config.http2,
                follow_redirects=self._config.follow_redirects,
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        """Exit the async context manager and close the HTTP client if owned.

        Args:
            exc_type: Exception type, if an exception occurred.
            exc: Exception instance, if an exception occurred.
            tb: Exception traceback-like object, if an exception occurred.
        """
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    async def get_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
        range_request: bool = True,
    ) -> BoundedResponse:
        """Fetch bytes from a URL with a hard cap.

        This method is designed for "probing" resources safely.

        Args:
            url: URL to fetch.
            max_bytes: Maximum bytes to download. Must be > 0.
            headers: Optional per-request headers.
            range_request: If True, attempts an HTTP Range request up front.

        Returns:
            BoundedResponse with content possibly truncated.

        Raises:
            ValueError: If max_bytes <= 0.
            Http2FetchError: On persistent failures.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")

        prepared_headers: dict[str, str] = {}
        if headers:
            prepared_headers.update(headers)

        if range_request:
            # Request one extra byte so we can mark truncation when supported.
            prepared_headers.setdefault("Range", f"bytes=0-{max_bytes}")

        response = await self._request_with_retries(
            method="GET",
            url=url,
            headers=prepared_headers or None,
            max_bytes=max_bytes,
        )
        return response

    async def get_text(
        self,
        url: str,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
        range_request: bool = True,
        encoding: str | None = None,
    ) -> BoundedResponse:
        """Fetch text-like content with bounded bytes.

        Args:
            url: URL to fetch.
            max_bytes: Maximum bytes to download.
            headers: Optional per-request headers.
            range_request: Attempt Range request.
            encoding: Optional forced encoding for callers who need it.

        Returns:
            BoundedResponse. Call :meth:`BoundedResponse.text` to decode.
        """
        response = await self.get_bytes(
            url,
            max_bytes=max_bytes,
            headers=headers,
            range_request=range_request,
        )
        if encoding is not None:
            _ = response.text(encoding=encoding)
        return response

    async def _request_with_retries(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        max_bytes: int,
    ) -> BoundedResponse:
        if self._client is None:
            raise RuntimeError("Http2Fetcher must be used as an async context manager")

        last_exception: Exception | None = None
        for attempt_index in range(self._config.retry_attempts):
            try:
                async with self._semaphore:
                    return await self._request_once(
                        method=method,
                        url=url,
                        headers=headers,
                        max_bytes=max_bytes,
                        attempt_index=attempt_index,
                    )
            except Http2FetchHttpStatusError as exception:
                last_exception = exception

                retryable_status_code = getattr(exception, "status_code", None)
                is_retryable = (
                    retryable_status_code in self._config.retry_on_status_codes
                    if retryable_status_code is not None
                    else False
                )

                if not is_retryable:
                    raise exception

                if attempt_index >= self._config.retry_attempts - 1:
                    break

                await self._sleep_before_retry(
                    attempt_index=attempt_index,
                    retry_after_seconds=getattr(exception, "retry_after_seconds", None),
                )
            except httpx.TimeoutException as exception:
                last_exception = exception
                if attempt_index >= self._config.retry_attempts - 1:
                    raise Http2FetchTimeoutError(str(exception)) from exception
                await self._sleep_before_retry(attempt_index=attempt_index)
            except (httpx.NetworkError, httpx.TransportError) as exception:
                last_exception = exception
                if attempt_index >= self._config.retry_attempts - 1:
                    raise Http2FetchNetworkError(str(exception)) from exception
                await self._sleep_before_retry(attempt_index=attempt_index)
            except Exception as exception:
                # Unexpected exception; do not retry by default.
                raise Http2FetchError(str(exception)) from exception

        # If we got here, we exhausted retries. Preserve the most specific
        # error type where possible instead of wrapping everything.
        if last_exception is None:
            raise Http2FetchError("Request failed without exception details")

        if isinstance(last_exception, Http2FetchHttpStatusError):
            raise last_exception

        raise Http2FetchError(str(last_exception)) from last_exception

    async def _sleep_before_retry(
        self,
        *,
        attempt_index: int,
        retry_after_seconds: float | None = None,
    ) -> None:
        if retry_after_seconds is not None:
            await asyncio.sleep(
                min(retry_after_seconds, self._config.max_backoff_seconds)
            )
            return

        backoff_seconds = _compute_backoff_seconds(
            attempt_index=attempt_index,
            base_backoff_seconds=self._config.base_backoff_seconds,
            max_backoff_seconds=self._config.max_backoff_seconds,
            jitter_seconds=self._config.jitter_seconds,
        )
        await asyncio.sleep(backoff_seconds)

    async def _request_once(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        max_bytes: int,
        attempt_index: int,
    ) -> BoundedResponse:
        if self._client is None:
            raise RuntimeError("Async client not initialized")

        # Stream response to enforce max_bytes even when Range unsupported.
        request_headers = dict(headers) if headers else None

        async with self._client.stream(
            method, url, headers=request_headers
        ) as response:
            status_code = response.status_code

            if status_code >= 400:
                exception = Http2FetchHttpStatusError(f"HTTP {status_code} for {url}")
                exception.status_code = status_code

                if status_code in self._config.retry_on_status_codes:
                    retry_after_seconds = _parse_retry_after_seconds(
                        response.headers.get("retry-after")
                    )
                    # Attach retry-after seconds for caller/backoff logic.
                    exception.retry_after_seconds = retry_after_seconds

                raise exception

            content_parts: list[bytes] = []
            downloaded_bytes = 0
            content_truncated = False

            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue

                remaining_bytes = max_bytes - downloaded_bytes
                if remaining_bytes <= 0:
                    content_truncated = True
                    break

                if len(chunk) <= remaining_bytes:
                    content_parts.append(chunk)
                    downloaded_bytes += len(chunk)
                else:
                    # Truncate and stop.
                    content_parts.append(chunk[:remaining_bytes])
                    downloaded_bytes += remaining_bytes
                    content_truncated = True
                    break

            # If we requested a range and got 206, we can also infer truncation if
            # Content-Range indicates more bytes exist beyond our bound.
            if not content_truncated and response.status_code == 206:
                content_range = response.headers.get("content-range")
                if content_range and "/" in content_range:
                    total_part = content_range.split("/", 1)[1].strip()
                    if total_part.isdigit():
                        total_size = int(total_part)
                        if total_size > max_bytes:
                            content_truncated = True

            return BoundedResponse(
                url=str(response.url),
                status_code=status_code,
                headers=response.headers,
                content=b"".join(content_parts),
                content_truncated=content_truncated,
            )
