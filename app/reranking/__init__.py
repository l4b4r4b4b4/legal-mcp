"""ColBERT re-ranking module for Legal-MCP.

Provides local ColBERT late-interaction re-ranking using
VAGOsolutions/SauerkrautLM-Reason-EuroColBERT (Apache 2.0).

Re-ranks candidate documents from ChromaDB using MaxSim scoring
for improved search precision on German legal text.

Imports are lazy to avoid pulling in ``torch`` at module load time.
The MCP server delegates embedding and reranking to external TEI/vLLM
services; local torch-based inference is only used as a fallback.

Usage:
    from app.reranking import get_colbert_reranker

    reranker = get_colbert_reranker()
    results = await reranker.rerank(
        query="Was ist ein Kaufvertrag?",
        documents=["Doc 1 text", "Doc 2 text", ...],
        top_k=10,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.reranking.colbert_reranker import ColBERTReranker

__all__ = [
    "ColBERTReranker",
    "cleanup_colbert_reranker",
    "get_colbert_reranker",
    "reset_colbert_reranker",
]


def __getattr__(name: str) -> Any:
    """Lazy import to avoid loading torch at module import time."""
    if name in __all__:
        from app.reranking import colbert_reranker

        return getattr(colbert_reranker, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
