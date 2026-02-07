"""TEI Reranker client for two-stage retrieval.

Uses HuggingFace Text Embeddings Inference server in reranking mode
to improve search precision by reranking initial semantic search results.

Usage:
    from app.rag.reranker import TEIReranker, get_reranker

    reranker = get_reranker()
    reranked = await reranker.rerank(
        query="Was ist ein Kaufvertrag?",
        documents=["Doc 1 text", "Doc 2 text", ...],
        top_k=5
    )
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default TEI reranker URL
DEFAULT_RERANKER_URL = os.getenv("RERANKER_URL", "http://localhost:8020")


@dataclass
class RerankResult:
    """Result from reranking.

    Attributes:
        index: Original index in the input documents list
        score: Reranking score (higher is more relevant)
        text: The document text
    """

    index: int
    score: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "index": self.index,
            "score": round(self.score, 4),
        }


@dataclass
class TEIReranker:
    """HTTP client for TEI reranking server.

    Uses the /rerank endpoint of Text Embeddings Inference server
    running with a cross-encoder reranking model (e.g., BAAI/bge-reranker-large).

    Attributes:
        base_url: TEI reranker server URL
        timeout: Request timeout in seconds
        max_retries: Maximum retry attempts
    """

    base_url: str = DEFAULT_RERANKER_URL
    timeout: float = 30.0
    max_retries: int = 3
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Initialize async HTTP client."""
        logger.info("TEI Reranker initialized with URL: %s", self.base_url)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def health_check(self) -> bool:
        """Check if reranker server is healthy.

        Returns:
            True if server is healthy, False otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning("Reranker health check failed: %s", e)
            return False

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
        return_text: bool = True,
    ) -> list[RerankResult]:
        """Rerank documents based on relevance to query.

        Args:
            query: The search query
            documents: List of document texts to rerank
            top_k: Number of top results to return (default: all)
            return_text: Whether to include text in results

        Returns:
            List of RerankResult sorted by score (highest first)

        Raises:
            RuntimeError: If reranking fails after retries
        """
        if not documents:
            return []

        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                # TEI rerank endpoint format
                response = await client.post(
                    "/rerank",
                    json={
                        "query": query,
                        "texts": documents,
                        "truncate": True,
                    },
                )
                response.raise_for_status()

                # Parse response - TEI returns list of {index, score}
                data = response.json()
                results: list[RerankResult] = []

                for item in data:
                    idx = item["index"]
                    results.append(
                        RerankResult(
                            index=idx,
                            score=item["score"],
                            text=documents[idx] if return_text else "",
                        )
                    )

                # Sort by score descending
                results.sort(key=lambda x: x.score, reverse=True)

                # Apply top_k
                if top_k is not None:
                    results = results[:top_k]

                logger.debug(
                    "Reranked %d documents, returning top %d",
                    len(documents),
                    len(results),
                )

                return results

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    "Reranker HTTP error (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Reranker error (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )

        raise RuntimeError(
            f"Reranking failed after {self.max_retries} attempts: {last_error}"
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def stats(self) -> dict[str, Any]:
        """Get reranker statistics.

        Returns:
            Dictionary with reranker configuration
        """
        return {
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }


# =============================================================================
# Singleton Reranker
# =============================================================================

_reranker: TEIReranker | None = None
_reranker_lock = threading.Lock()


def get_reranker(base_url: str | None = None) -> TEIReranker:
    """Get the global TEI reranker instance.

    Args:
        base_url: Override reranker URL (only used on first call)

    Returns:
        Singleton TEIReranker instance
    """
    global _reranker

    with _reranker_lock:
        if _reranker is None:
            url = base_url or os.getenv("RERANKER_URL", DEFAULT_RERANKER_URL)
            _reranker = TEIReranker(base_url=url)
        return _reranker


def reset_reranker() -> None:
    """Reset the global reranker (for testing)."""
    global _reranker

    with _reranker_lock:
        _reranker = None


__all__ = [
    "RerankResult",
    "TEIReranker",
    "get_reranker",
    "reset_reranker",
]
