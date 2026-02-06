"""RAG (Retrieval-Augmented Generation) package for German legal Q&A.

This package provides a complete RAG pipeline for answering legal questions
using German federal law documents stored in ChromaDB.

Components:
    - pipeline: Main RAGPipeline class and get_rag_pipeline singleton
    - llm_client: LiteLLM wrapper for Ollama/vLLM/OpenAI
    - context: Context builder with citation formatting
    - prompts: German legal prompt templates
    - reranker: TEI reranker client for two-stage retrieval

Usage:
    from app.rag import RAGPipeline, get_rag_pipeline

    # Using singleton
    pipeline = get_rag_pipeline()
    result = await pipeline.ask("Was ist ein Kaufvertrag?")
    print(result.answer)
    for source in result.sources:
        print(f"{source['citation']} {source['law']} {source['norm_id']}")

    # With filters
    result = await pipeline.ask(
        "Welche Grundrechte gibt es?",
        law_filter="GG",
    )
"""

from __future__ import annotations

from app.rag.context import (
    RAGContext,
    SourceContext,
    build_context_from_results,
)
from app.rag.llm_client import (
    LLMClient,
    LLMResponse,
    ProviderType,
    get_llm_client,
    reset_llm_client,
)
from app.rag.pipeline import (
    RAGPipeline,
    RAGResult,
    get_rag_pipeline,
    reset_rag_pipeline,
)
from app.rag.prompts import (
    SYSTEM_PROMPT,
    format_source,
    format_sources,
    format_user_prompt,
)
from app.rag.reranker import (
    RerankResult,
    TEIReranker,
    get_reranker,
    reset_reranker,
)

__all__ = [
    # Prompts
    "SYSTEM_PROMPT",
    # LLM Client
    "LLMClient",
    "LLMResponse",
    "ProviderType",
    # Context
    "RAGContext",
    # Pipeline
    "RAGPipeline",
    "RAGResult",
    # Reranker
    "RerankResult",
    "SourceContext",
    "TEIReranker",
    "build_context_from_results",
    "format_source",
    "format_sources",
    "format_user_prompt",
    "get_llm_client",
    "get_rag_pipeline",
    "get_reranker",
    "reset_llm_client",
    "reset_rag_pipeline",
    "reset_reranker",
]
