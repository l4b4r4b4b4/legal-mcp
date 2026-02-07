"""RAG (Retrieval-Augmented Generation) pipeline for German legal Q&A.

Orchestrates the retrieve → rerank → generate flow:
1. Search ChromaDB for relevant law sections
2. Rerank results with cross-encoder (optional, improves precision)
3. Build context with citation formatting
4. Generate answer using LLM

Usage:
    from app.rag.pipeline import RAGPipeline, get_rag_pipeline

    # Using singleton
    pipeline = get_rag_pipeline()
    result = await pipeline.ask("Was ist ein Kaufvertrag?")

    # Or with custom config (with reranking)
    pipeline = RAGPipeline(max_sources=10, use_reranker=True)
    result = await pipeline.ask(question, law_filter="BGB")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.rag.context import RAGContext, build_context_from_results
from app.rag.llm_client import LLMClient, LLMResponse, get_llm_client
from app.rag.prompts import SYSTEM_PROMPT, format_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class RAGResult:
    """Result from RAG pipeline.

    Attributes:
        question: Original user question
        answer: Generated answer text
        sources: List of source citations
        model: LLM model used
        retrieval_count: Number of documents retrieved
        sources_used: Number of sources in context
        retrieval_time_ms: Time for vector search
        generation_time_ms: Time for LLM generation
        total_time_ms: Total pipeline time
        usage: Token usage from LLM
    """

    question: str
    answer: str
    sources: list[dict[str, Any]]
    model: str
    retrieval_count: int
    sources_used: int
    retrieval_time_ms: float
    generation_time_ms: float
    total_time_ms: float
    usage: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "question": self.question,
            "answer": self.answer,
            "sources": self.sources,
            "model": self.model,
            "retrieval_count": self.retrieval_count,
            "sources_used": self.sources_used,
            "timing": {
                "retrieval_ms": round(self.retrieval_time_ms, 2),
                "generation_ms": round(self.generation_time_ms, 2),
                "total_ms": round(self.total_time_ms, 2),
            },
            "usage": self.usage,
        }


@dataclass
class RAGPipeline:
    """RAG pipeline for German legal Q&A.

    Orchestrates retrieval from ChromaDB, optional reranking, and generation with LLM.

    Attributes:
        max_sources: Maximum sources to include in context
        retrieval_count: Number of documents to retrieve from search
        min_similarity: Minimum similarity score for sources
        max_content_length: Maximum characters per source in context
        use_reranker: Whether to use reranker for improved precision
        llm_client: LLM client for generation (uses singleton if not provided)
    """

    max_sources: int = 5
    retrieval_count: int = 20  # Retrieve more for reranking
    min_similarity: float = 0.3
    max_content_length: int = 2000
    use_reranker: bool = True  # Enable reranker by default
    llm_client: LLMClient | None = field(default=None, repr=False)
    _embedding_store: Any = field(default=None, repr=False)
    _reranker: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Initialize components."""
        if self.llm_client is None:
            self.llm_client = get_llm_client()
        logger.info(
            "RAG pipeline initialized: max_sources=%d, retrieval_count=%d, use_reranker=%s",
            self.max_sources,
            self.retrieval_count,
            self.use_reranker,
        )

    @property
    def embedding_store(self) -> Any:
        """Lazy-load embedding store to avoid startup overhead."""
        if self._embedding_store is None:
            from app.config import get_settings
            from app.ingestion.embeddings import GermanLawEmbeddingStore

            settings = get_settings()
            self._embedding_store = GermanLawEmbeddingStore(
                model_name=settings.embedding_model,
            )
        return self._embedding_store

    @property
    def reranker(self) -> Any:
        """Lazy-load reranker to avoid startup overhead."""
        if self._reranker is None and self.use_reranker:
            from app.rag.reranker import get_reranker

            self._reranker = get_reranker()
        return self._reranker

    async def _retrieve(
        self,
        question: str,
        law_filter: str | None = None,
        level_filter: str | None = None,
    ) -> tuple[RAGContext, float]:
        """Retrieve and optionally rerank relevant documents.

        Args:
            question: User's legal question
            law_filter: Optional law abbreviation filter (e.g., "BGB")
            level_filter: Optional level filter ("norm" or "paragraph")

        Returns:
            Tuple of (RAGContext, retrieval_time_ms)
        """
        start_time = time.perf_counter()

        # Build metadata filter
        where_filter: dict[str, Any] | None = None
        if law_filter or level_filter:
            conditions = []
            if law_filter:
                conditions.append({"law_abbrev": {"$eq": law_filter.upper()}})
            if level_filter:
                conditions.append({"level": {"$eq": level_filter}})

            if len(conditions) == 1:
                where_filter = conditions[0]
            else:
                where_filter = {"$and": conditions}

        # Search - retrieve more if using reranker
        results = self.embedding_store.search(
            query=question,
            n_results=self.retrieval_count,
            where=where_filter,
        )

        # Rerank if enabled and reranker is available
        if self.use_reranker and self.reranker is not None and len(results) > 0:
            try:
                results = await self._rerank_results(question, results)
                logger.debug("Reranked %d results", len(results))
            except Exception as e:
                logger.warning("Reranking failed, using original results: %s", e)

        retrieval_time_ms = (time.perf_counter() - start_time) * 1000

        # Build context
        context = build_context_from_results(
            question=question,
            search_results=results,
            max_sources=self.max_sources,
            min_similarity=self.min_similarity,
        )

        logger.debug(
            "Retrieved %d documents, using %d sources (%.2fms)",
            len(results),
            len(context.sources),
            retrieval_time_ms,
        )

        return context, retrieval_time_ms

    async def _rerank_results(
        self,
        question: str,
        results: list[Any],
    ) -> list[Any]:
        """Rerank search results using cross-encoder.

        Args:
            question: User's question
            results: List of SearchResult objects

        Returns:
            Reranked list of SearchResult objects
        """
        if not results:
            return results

        # Extract texts for reranking
        texts = [r.content for r in results]

        # Rerank
        reranked = await self.reranker.rerank(
            query=question,
            documents=texts,
            top_k=self.retrieval_count,
            return_text=False,
        )

        # Reorder results based on rerank scores
        reranked_results = []
        for rr in reranked:
            original_result = results[rr.index]
            # Update the distance/similarity based on rerank score
            # Higher rerank score = more relevant
            original_result.distance = 1.0 - rr.score  # Convert to distance
            reranked_results.append(original_result)

        return reranked_results

    async def _generate(
        self,
        context: RAGContext,
    ) -> LLMResponse:
        """Generate answer using LLM.

        Args:
            context: RAGContext with sources

        Returns:
            LLMResponse with generated answer
        """
        # Build messages
        user_prompt = format_user_prompt(
            question=context.question,
            sources=context.get_prompt_sources(),
            max_sources=self.max_sources,
            max_content_length=self.max_content_length,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # Generate
        assert self.llm_client is not None
        response = await self.llm_client.generate(messages)

        logger.debug(
            "Generated response: %d tokens, %.2fms",
            response.usage.get("completion_tokens", 0),
            response.latency_ms,
        )

        return response

    async def ask(
        self,
        question: str,
        law_filter: str | None = None,
        level_filter: str | None = None,
    ) -> RAGResult:
        """Ask a legal question and get an answer with citations.

        Args:
            question: User's legal question in German or English
            law_filter: Optional law abbreviation filter (e.g., "BGB", "StGB")
            level_filter: Optional level filter ("norm" or "paragraph")

        Returns:
            RAGResult with answer, sources, and timing information

        Example:
            >>> pipeline = RAGPipeline()
            >>> result = await pipeline.ask("Was ist ein Kaufvertrag?")
            >>> print(result.answer)
            >>> for source in result.sources:
            ...     print(f"{source['citation']} {source['law']} {source['norm_id']}")
        """
        total_start = time.perf_counter()

        # Retrieve
        context, retrieval_time_ms = await self._retrieve(
            question=question,
            law_filter=law_filter,
            level_filter=level_filter,
        )

        # Generate
        response = await self._generate(context)

        total_time_ms = (time.perf_counter() - total_start) * 1000

        return RAGResult(
            question=question,
            answer=response.content,
            sources=context.get_response_sources(),
            model=response.model,
            retrieval_count=context.total_retrieved,
            sources_used=len(context.sources),
            retrieval_time_ms=retrieval_time_ms,
            generation_time_ms=response.latency_ms,
            total_time_ms=total_time_ms,
            usage=response.usage,
        )

    async def health_check(self) -> dict[str, Any]:
        """Check health of pipeline components.

        Returns:
            Dictionary with component health status
        """
        health: dict[str, Any] = {
            "embedding_store": False,
            "llm": False,
            "document_count": 0,
        }

        # Check embedding store
        try:
            health["document_count"] = self.embedding_store.count()
            health["embedding_store"] = health["document_count"] > 0
        except Exception as e:
            logger.warning("Embedding store health check failed: %s", e)
            health["embedding_store_error"] = str(e)

        # Check LLM
        assert self.llm_client is not None
        health["llm"] = await self.llm_client.health_check()
        if not health["llm"]:
            health["llm_error"] = "LLM health check failed"

        health["healthy"] = health["embedding_store"] and health["llm"]
        return health

    def stats(self) -> dict[str, Any]:
        """Get pipeline statistics.

        Returns:
            Dictionary with pipeline configuration and status
        """
        assert self.llm_client is not None
        stats_dict = {
            "max_sources": self.max_sources,
            "retrieval_count": self.retrieval_count,
            "min_similarity": self.min_similarity,
            "max_content_length": self.max_content_length,
            "use_reranker": self.use_reranker,
            "llm": self.llm_client.stats(),
        }
        if self.use_reranker and self._reranker is not None:
            stats_dict["reranker"] = self._reranker.stats()
        return stats_dict


# =============================================================================
# Singleton Pipeline
# =============================================================================

_rag_pipeline: RAGPipeline | None = None


def get_rag_pipeline(
    max_sources: int | None = None,
    retrieval_count: int | None = None,
    min_similarity: float | None = None,
    use_reranker: bool | None = None,
) -> RAGPipeline:
    """Get the global RAG pipeline instance.

    Creates a singleton pipeline on first call.

    Args:
        max_sources: Maximum sources in context (only used on first call)
        retrieval_count: Documents to retrieve (only used on first call)
        min_similarity: Minimum similarity threshold (only used on first call)
        use_reranker: Whether to use reranker (only used on first call)

    Returns:
        Singleton RAGPipeline instance
    """
    global _rag_pipeline

    if _rag_pipeline is None:
        _rag_pipeline = RAGPipeline(
            max_sources=max_sources or 5,
            retrieval_count=retrieval_count or 20,
            min_similarity=min_similarity or 0.3,
            use_reranker=use_reranker if use_reranker is not None else True,
        )

    return _rag_pipeline


def reset_rag_pipeline() -> None:
    """Reset the global RAG pipeline (for testing)."""
    global _rag_pipeline
    _rag_pipeline = None


__all__ = [
    "RAGPipeline",
    "RAGResult",
    "get_rag_pipeline",
    "reset_rag_pipeline",
]
