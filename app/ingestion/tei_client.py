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

    def __post_init__(self) -> None:
        """Initialize HTTP clients for all endpoints."""
        for url in self.base_urls:
            self._clients[url] = httpx.Client(
                base_url=url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        self._url_cycle = itertools.cycle(self.base_urls)
        logger.info(
            "TEI client initialized with %d endpoints: %s",
            len(self.base_urls),
            self.base_urls,
        )

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

        # TEI handles batching efficiently - use larger batches
        batch_size = batch_size or 64
        all_embeddings: list[list[float]] = []

        # Process batches concurrently for better GPU utilization
        import concurrent.futures

        batches = [
            sentences[i : i + batch_size] for i in range(0, len(sentences), batch_size)
        ]

        # Use 4 concurrent requests to keep GPU saturated
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(self._embed_batch, batch) for batch in batches]
            for future in concurrent.futures.as_completed(futures):
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

        for attempt in range(self.max_retries * len(self.base_urls)):
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
                if e.response.status_code == 503:
                    # Server overloaded, try next endpoint immediately
                    logger.debug("TEI server %s overloaded, trying next", url)
                    if len(tried_urls) >= len(self.base_urls):
                        # All servers tried, wait before retry
                        wait_time = 2 ** (attempt // len(self.base_urls))
                        logger.warning(
                            "All TEI servers overloaded, retrying in %ds",
                            wait_time,
                        )
                        time.sleep(wait_time)
                        tried_urls.clear()
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
            f"Failed to embed after {self.max_retries * len(self.base_urls)} attempts: {last_error}"
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
            "batch_size": 64,  # TEI handles batching
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
