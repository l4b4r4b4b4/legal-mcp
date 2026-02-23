"""TEI (Text Embeddings Inference) client for HTTP-based embeddings.

Uses HuggingFace Text Embeddings Inference server instead of loading
models locally. Benefits:
- Efficient continuous batching
- Flash attention optimization
- Better GPU memory management
- Shared inference across processes
- Multi-endpoint round-robin load balancing

Usage:
    from app.ingestion.tei_client import TEIEmbeddingClient

    # Single endpoint
    client = TEIEmbeddingClient(base_urls=["http://localhost:8011"])

    # Multiple endpoints with load balancing
    client = TEIEmbeddingClient(base_urls=["http://localhost:8011", "http://localhost:8012"])
    embeddings = client.encode(["Hello world", "Guten Tag"])

    # Or use as drop-in replacement for model manager
    from app.ingestion.tei_client import get_tei_client
    client = get_tei_client()
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# Default TEI server URLs (comma-separated for multiple)
DEFAULT_TEI_URLS = os.getenv(
    "TEI_URLS",
    "http://localhost:8011,http://localhost:8012,http://localhost:8013,http://localhost:8014,http://localhost:8015,http://localhost:8016",
)

# Global client instance
_tei_client: TEIEmbeddingClient | None = None
_client_lock = threading.Lock()


@dataclass
class TEIEmbeddingClient:
    """HTTP client for Text Embeddings Inference server(s).

    Provides the same interface as EmbeddingModelManager but uses
    external TEI server(s) for inference. Supports multiple endpoints
    with round-robin load balancing.

    Attributes:
        base_urls: List of TEI server URLs for load balancing
        timeout: Request timeout in seconds
        max_retries: Maximum retry attempts for failed requests
    """

    base_urls: list[str] = field(default_factory=lambda: DEFAULT_TEI_URLS.split(","))
    timeout: float = 120.0
    max_retries: int = 3
    _clients: dict[str, httpx.Client] = field(default_factory=dict, repr=False)
    _model_info: dict[str, Any] | None = field(default=None, repr=False)
    _url_cycle: itertools.cycle | None = field(default=None, repr=False)
    _cycle_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _server_batch_size: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Initialize HTTP clients for all endpoints."""
        for url in self.base_urls:
            self._clients[url] = httpx.Client(
                base_url=url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        self._url_cycle = itertools.cycle(self.base_urls)
        # Auto-detect server batch size limit and concurrency capacity
        self._server_batch_size = self._detect_server_batch_size()
        self._server_max_concurrent = self._detect_max_concurrent()
        logger.info(
            "TEI client initialized with %d endpoints: %s "
            "(server batch_size=%s, max_concurrent=%s)",
            len(self.base_urls),
            self.base_urls,
            self._server_batch_size,
            self._server_max_concurrent,
        )

    # Servers reporting max_client_batch_size >= this threshold are
    # considered GPU-backed and allowed larger per-request batches.
    _GPU_BATCH_THRESHOLD: int = 32

    # Conservative per-request cap for CPU / low-capacity TEI servers.
    _CPU_BATCH_CAP: int = 8

    def _detect_server_batch_size(self) -> int | None:
        """Detect a safe per-request batch size from the /info endpoint.

        The TEI server reports ``max_client_batch_size`` (max texts per
        request) and ``max_batch_tokens`` (total token budget per batch).
        For long legal texts the token budget is usually the binding
        constraint, so we pick the *minimum* of the reported client batch
        size and a token-budget-derived heuristic (tokens / 2048 average
        legal-text length, floored at 2).

        GPU-backed servers (identified by ``max_client_batch_size >= 32``)
        are allowed much larger batches — up to the server's own limit —
        because they have the VRAM and throughput to handle them.  CPU or
        low-capacity servers are capped conservatively at 8 texts/request.

        Returns:
            A safe per-request batch size, or None if detection fails.
        """
        try:
            info = self.get_model_info()
            client_limit = info.get("max_client_batch_size")
            token_budget = info.get("max_batch_tokens")
            max_concurrent = info.get("max_concurrent_requests")

            # Heuristic: assume ~2048 tokens per average legal paragraph.
            # This is conservative — many paragraphs are shorter, but some
            # are 4000+ tokens, and a single oversized batch causes a 429.
            safe_limit: int | None = None
            if client_limit is not None and isinstance(client_limit, int):
                safe_limit = client_limit
            if token_budget is not None and isinstance(token_budget, int):
                token_derived = max(2, token_budget // 2048)
                if safe_limit is not None:
                    safe_limit = min(safe_limit, token_derived)
                else:
                    safe_limit = token_derived

            # For low-concurrency servers (CPU / single-replica), use very
            # small batches to avoid saturating the request queue.
            if (
                max_concurrent is not None
                and isinstance(max_concurrent, int)
                and max_concurrent <= 8
            ):
                safe_limit = min(safe_limit or 4, 4)

            # Apply tier-appropriate cap based on server capacity.
            is_gpu_server = (
                client_limit is not None
                and isinstance(client_limit, int)
                and client_limit >= self._GPU_BATCH_THRESHOLD
            )
            if safe_limit is not None:
                if is_gpu_server:
                    # GPU server — respect its reported limit (typically 64-128).
                    safe_limit = min(safe_limit, client_limit)  # type: ignore[arg-type]
                else:
                    # CPU / low-capacity — conservative cap for legal texts.
                    safe_limit = min(safe_limit, self._CPU_BATCH_CAP)

            if safe_limit is not None:
                logger.info(
                    "Detected TEI safe batch size: %d "
                    "(client_limit=%s, token_budget=%s, gpu=%s)",
                    safe_limit,
                    client_limit,
                    token_budget,
                    is_gpu_server,
                )
            return safe_limit
        except Exception as error:
            logger.debug("Could not detect server batch size: %s", error)
        return None

    def _detect_max_concurrent(self) -> int | None:
        """Detect the TEI server's max_concurrent_requests from /info.

        Returns:
            The server's max_concurrent_requests, or None if unknown.
        """
        try:
            info = self.get_model_info()
            value = info.get("max_concurrent_requests")
            if value is not None and isinstance(value, int):
                logger.info("Detected TEI max_concurrent_requests: %d", value)
                return value
        except Exception as error:
            logger.debug("Could not detect max concurrent: %s", error)
        return None

    def _get_next_url(self) -> str:
        """Get next URL in round-robin fashion (thread-safe)."""
        with self._cycle_lock:
            return next(self._url_cycle)

    def _get_client(self, url: str) -> httpx.Client:
        """Get HTTP client for specific URL."""
        if url not in self._clients:
            self._clients[url] = httpx.Client(
                base_url=url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._clients[url]

    def health_check(self) -> bool:
        """Check if any TEI server is healthy.

        Returns:
            True if at least one server is healthy, False otherwise
        """
        for url in self.base_urls:
            try:
                response = self._get_client(url).get("/health")
                if response.status_code == 200:
                    return True
            except Exception as e:
                logger.warning("TEI health check failed for %s: %s", url, e)
        return False

    def get_model_info(self) -> dict[str, Any]:
        """Get model information from TEI server.

        Returns:
            Dictionary with model metadata
        """
        if self._model_info is not None:
            return self._model_info

        for url in self.base_urls:
            try:
                response = self._get_client(url).get("/info")
                response.raise_for_status()
                self._model_info = response.json()
                return self._model_info
            except Exception as e:
                logger.warning("Failed to get model info from %s: %s", url, e)
        return {}

    def encode(
        self,
        sentences: list[str] | str,
        batch_size: int | None = None,
        show_progress_bar: bool = False,
        convert_to_numpy: bool = True,
    ) -> np.ndarray:
        """Encode sentences using TEI server.

        Compatible with sentence-transformers interface.

        Args:
            sentences: Text(s) to encode
            batch_size: Batch size for requests (TEI handles batching internally)
            show_progress_bar: Ignored (TEI handles progress)
            convert_to_numpy: Always returns numpy (for compatibility)

        Returns:
            Numpy array of embeddings [n_sentences, embedding_dim]
        """
        # Normalize input to list
        if isinstance(sentences, str):
            sentences = [sentences]

        if not sentences:
            return np.array([])

        # Use server-reported safe batch size if available, otherwise
        # fall back to a conservative default that works with most TEI configs.
        if batch_size is None:
            batch_size = self._server_batch_size or 4
        all_embeddings: list[list[float]] = []

        import concurrent.futures

        batches = [
            sentences[i : i + batch_size] for i in range(0, len(sentences), batch_size)
        ]

        # Decide concurrency based on server capacity.
        # Low-capacity servers (max_concurrent_requests <= 8, typical for CPU)
        # are processed sequentially to avoid 429s.  High-capacity servers
        # (GPU with large request queues) benefit from concurrent batches
        # that keep the inference pipeline saturated.
        server_capacity = self._server_max_concurrent or 5
        if server_capacity <= 8:
            # CPU TEI / single-replica — sequential to avoid 429s
            max_workers = 1
        elif server_capacity >= 512:
            # GPU server with large queue — moderate concurrency
            max_workers = min(6, len(batches))
        else:
            # Mid-range — light concurrency
            max_workers = min(3, len(batches))

        if max_workers <= 1:
            # Sequential processing — no thread pool overhead.
            # Add a small delay between requests to avoid overwhelming
            # low-capacity TEI servers (CPU / single-replica).
            inter_request_delay = 0.1 if server_capacity <= 8 else 0.0
            for batch_index, batch in enumerate(batches):
                if batch_index > 0 and inter_request_delay > 0:
                    time.sleep(inter_request_delay)
                embeddings = self._embed_batch(batch)
                all_embeddings.extend(embeddings)
        else:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers,
            ) as executor:
                # Collect results in order to preserve sentence ↔ embedding
                # correspondence.
                futures = [
                    executor.submit(self._embed_batch, batch) for batch in batches
                ]
                for future in futures:
                    embeddings = future.result()
                    all_embeddings.extend(embeddings)

        return np.array(all_embeddings, dtype=np.float32)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts with retries and load balancing.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        last_error: Exception | None = None
        tried_urls: set[str] = set()

        # Allow more retry cycles for rate-limited servers
        max_attempts = max(self.max_retries * len(self.base_urls), 6)
        for attempt in range(max_attempts):
            url = self._get_next_url()
            client = self._get_client(url)

            try:
                response = client.post(
                    "/embed",
                    json={"inputs": texts, "truncate": True},
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                last_error = e
                tried_urls.add(url)
                if e.response.status_code in (429, 503):
                    # 429 = rate limited, 503 = overloaded — both are transient.
                    # Try the next endpoint; if all endpoints are saturated,
                    # back off exponentially before retrying the cycle.
                    logger.debug(
                        "TEI server %s returned %d, trying next",
                        url,
                        e.response.status_code,
                    )
                    if len(tried_urls) >= len(self.base_urls):
                        wait_time = min(2 ** (attempt // len(self.base_urls)), 30)
                        logger.warning(
                            "All TEI servers saturated (%d), retrying in %ds",
                            e.response.status_code,
                            wait_time,
                        )
                        time.sleep(wait_time)
                        tried_urls.clear()
                elif e.response.status_code == 422:
                    # Payload too large or malformed — no point retrying
                    # the same batch. Log details and re-raise.
                    logger.error(
                        "TEI server %s rejected payload (422): %d texts, response=%s",
                        url,
                        len(texts),
                        e.response.text[:500],
                    )
                    raise
                else:
                    raise

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                tried_urls.add(url)
                logger.debug("TEI connection error on %s: %s", url, e)
                if len(tried_urls) >= len(self.base_urls):
                    wait_time = 2 ** (attempt // len(self.base_urls))
                    logger.warning(
                        "All TEI endpoints failed, retrying in %ds: %s",
                        wait_time,
                        e,
                    )
                    time.sleep(wait_time)
                    tried_urls.clear()

        raise RuntimeError(
            f"Failed to embed after {max_attempts} attempts: {last_error}"
        )

    def get_sentence_embedding_dimension(self) -> int:
        """Get embedding dimension from model info.

        Returns:
            Embedding dimension (e.g., 768 for jina-embeddings-v2-base-de)
        """
        info = self.get_model_info()
        # TEI returns max_input_length and other fields, but not always dim
        # Try to get from a test embedding if not in info
        if "dim" in info:
            return info["dim"]

        # Fallback: embed a test string and check dimension
        test_embedding = self.encode(["test"])
        return test_embedding.shape[1]

    def cleanup(self) -> None:
        """Close HTTP client connections."""
        for _url, client in self._clients.items():
            client.close()
        self._clients.clear()

    def stats(self) -> dict[str, Any]:
        """Get client statistics.

        Returns:
            Dictionary with client and server stats
        """
        info = self.get_model_info()
        return {
            "model_name": info.get("model_id", "unknown"),
            "device": "tei-server",
            "max_seq_length": info.get("max_input_length", 8192),
            "batch_size": self._server_batch_size or 4,
            "model_loaded": self.health_check(),
            "last_used": time.time(),
            "idle_timeout": 0,  # No idle timeout for HTTP client
            "cuda_available": True,  # Assumed for TEI server
            "tei_urls": self.base_urls,
            "num_endpoints": len(self.base_urls),
            "tei_info": info,
        }

    def __del__(self) -> None:
        """Cleanup on deletion."""
        self.cleanup()


def get_tei_client(base_urls: list[str] | str | None = None) -> TEIEmbeddingClient:
    """Get the global TEI client instance.

    Args:
        base_urls: Override server URL(s) (only used on first call).
                   Can be a list or comma-separated string.

    Returns:
        Singleton TEIEmbeddingClient instance
    """
    global _tei_client

    with _client_lock:
        if _tei_client is None:
            if base_urls is None:
                urls = DEFAULT_TEI_URLS.split(",")
            elif isinstance(base_urls, str):
                urls = base_urls.split(",")
            else:
                urls = base_urls
            _tei_client = TEIEmbeddingClient(base_urls=urls)
        return _tei_client


def reset_tei_client() -> None:
    """Reset the global TEI client (for testing)."""
    global _tei_client

    with _client_lock:
        if _tei_client is not None:
            _tei_client.cleanup()
        _tei_client = None
