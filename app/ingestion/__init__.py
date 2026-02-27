"""Ingestion module for Legal-MCP.

This module handles the ingestion pipeline for legal documents:
- Discovery: Find all laws and norms from official sources
- Loading: Parse HTML pages into LangChain Documents
- Embedding: Convert text to vectors using TEI
- Storage: Persist in ChromaDB for semantic search

Components:
- GermanLawEmbeddingStore: ChromaDB-backed vector store
- ingest_german_laws: Full ingestion pipeline

Usage:
    from app.ingestion import GermanLawEmbeddingStore

    store = GermanLawEmbeddingStore()
    results = store.search("Kaufvertrag Pflichten", n_results=5)
"""

from __future__ import annotations

__all__ = [
    "GermanLawEmbeddingStore",
    "IngestionProgress",
    "IngestionResult",
    "ingest_german_laws",
    "ingest_single_law",
    "search_laws",
]


# Lazy imports to avoid loading heavy dependencies at module import time
def __getattr__(name: str) -> object:
    """Lazy import of heavy dependencies."""
    if name == "GermanLawEmbeddingStore":
        from app.ingestion.embeddings import GermanLawEmbeddingStore

        return GermanLawEmbeddingStore

    if name == "IngestionProgress":
        from app.ingestion.pipeline import IngestionProgress

        return IngestionProgress

    if name == "IngestionResult":
        from app.ingestion.pipeline import IngestionResult

        return IngestionResult

    if name == "ingest_german_laws":
        from app.ingestion.pipeline import ingest_german_laws

        return ingest_german_laws

    if name == "ingest_single_law":
        from app.ingestion.pipeline import ingest_single_law

        return ingest_single_law

    if name == "search_laws":
        from app.ingestion.pipeline import search_laws

        return search_laws
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
