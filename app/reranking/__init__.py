"""ColBERT re-ranking module for Legal-MCP.

Provides local ColBERT late-interaction re-ranking using
VAGOsolutions/SauerkrautLM-Reason-EuroColBERT (Apache 2.0).

Re-ranks candidate documents from ChromaDB using MaxSim scoring
for improved search precision on German legal text.

Usage:
    from app.reranking import ColBERTReranker, get_colbert_reranker

    reranker = get_colbert_reranker()
    results = await reranker.rerank(
        query="Was ist ein Kaufvertrag?",
        documents=["Doc 1 text", "Doc 2 text", ...],
        top_k=10,
    )
"""

from __future__ import annotations

from app.reranking.colbert_reranker import (
    ColBERTReranker,
    cleanup_colbert_reranker,
    get_colbert_reranker,
    reset_colbert_reranker,
)

__all__ = [
    "ColBERTReranker",
    "cleanup_colbert_reranker",
    "get_colbert_reranker",
    "reset_colbert_reranker",
]
