"""German law tools (query-time MCP tools + dev-only ingestion support).

This module provides MCP tools for:
- Semantic search across German federal laws using embeddings
- Exact lookups by law/norm identifier
- Collection statistics and model status

It also contains a *dev-only* ingestion tool factory for the German federal law
corpus. Ingestion of the federal corpus is operationally heavy and is **not**
intended to be exposed as a public MCP tool. It remains available for
development workflows (CLI/scripts/library usage).

All tools integrate with mcp-refcache for:
- Automatic caching of search results
- Reference-based large result handling

Note:
- Custom document ingestion (e.g., case files) is handled separately.

Usage:
    # In server.py (query tools only)
    from app.tools.german_laws import (
        create_get_law_by_id,
        create_get_law_stats,
        create_search_laws,
    )

    search_laws = create_search_laws(cache)
    mcp.tool(search_laws)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from mcp_refcache import RefCache


class SearchLawsInput(BaseModel):
    """Input model for semantic law search."""

    query: str = Field(
        description="Search query in German or English (e.g., 'Kaufvertrag Pflichten', 'tenant rights')",
        min_length=2,
        max_length=1000,
    )
    n_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return",
    )
    law_abbrev: str | None = Field(
        default=None,
        description="Filter by law abbreviation (e.g., 'BGB', 'StGB', 'GG')",
    )
    level: str | None = Field(
        default=None,
        description="Filter by document level: 'norm' (section) or 'paragraph'",
    )


class IngestGermanLawsInput(BaseModel):
    """Input model for German law ingestion."""

    max_laws: int = Field(
        default=100,
        ge=1,
        le=7000,
        description="Maximum number of laws to ingest. Use 10-50 for testing, 7000 for full corpus (~30-60 min).",
    )
    max_norms_per_law: int | None = Field(
        default=None,
        ge=1,
        description="Optional limit on norms per law (for testing)",
    )


def create_search_laws(cache: RefCache) -> Any:
    """Create a search_laws tool function bound to the given cache.

    Args:
        cache: The RefCache instance for caching results.

    Returns:
        The search_laws tool function decorated with caching.
    """

    @cache.cached(namespace="german_laws")
    async def search_laws(
        query: str,
        n_results: int = 10,
        law_abbrev: str | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        """Search German federal laws using semantic similarity.

        Performs vector similarity search across embedded German law documents.
        Uses jinaai/jina-embeddings-v2-base-de for German-English bilingual embeddings.

        Args:
            query: Search query in German or English.
            n_results: Maximum results to return (1-50).
            law_abbrev: Filter by law abbreviation (e.g., 'BGB', 'StGB').
            level: Filter by document level ('norm' or 'paragraph').

        Returns:
            Search results with content, metadata, and similarity scores.

        Example queries:
            - "Kaufvertrag Pflichten" → § 433 BGB (purchase contract duties)
            - "Mietvertrag Kündigung" → § 542 BGB (rental termination)
            - "Grundrechte" → Art. 1-19 GG (fundamental rights)

        **Caching:** Results are cached for repeated queries.
        """
        # Validate input
        validated = SearchLawsInput(
            query=query,
            n_results=n_results,
            law_abbrev=law_abbrev,
            level=level,
        )

        # Import here to avoid loading heavy modules at startup
        from app.ingestion.pipeline import search_laws as search_laws_impl

        try:
            results = search_laws_impl(
                query=validated.query,
                n_results=validated.n_results,
                law_abbrev=validated.law_abbrev,
                level=validated.level,
            )

            return {
                "query": validated.query,
                "results": results,
                "count": len(results),
                "filters": {
                    "law_abbrev": validated.law_abbrev,
                    "level": validated.level,
                },
            }

        except Exception as e:
            return {
                "error": "Search failed",
                "message": str(e),
                "query": validated.query,
            }

    return search_laws


def create_ingest_german_laws(cache: RefCache) -> Any:
    """Create an ingestion tool for the German federal law corpus (dev-only).

    This is a long-running synchronous operation. It is intended for development
    workflows (CLI/scripts/library usage) and should not be exposed as a public
    MCP tool in production.

    For testing, use small `max_laws` values (10-50). Full corpus ingestion
    takes ~30-60 minutes.

    Args:
        cache: The RefCache instance for caching results.

    Returns:
        The ingest_german_laws tool function.
    """

    @cache.cached(namespace="ingestion")
    async def ingest_german_laws(
        max_laws: int = 100,
        max_norms_per_law: int | None = None,
    ) -> dict[str, Any]:
        """Ingest German federal laws into the vector store for semantic search.

        Downloads, parses, and embeds German law documents from gesetze-im-internet.de.

        WARNING: This is a long-running synchronous operation. Use small values
        for testing (10-50 laws). Full corpus (7000 laws) takes 30-60 minutes.

        Args:
            max_laws: Maximum laws to ingest (1-7000). Start with 10-50 for testing.
            max_norms_per_law: Optional limit on norms per law.

        Returns:
            Ingestion result with document counts and timing.

        Timing estimates:
            - 10 laws: ~30 seconds
            - 100 laws: ~5 minutes
            - 1000 laws: ~30 minutes
            - 7000 laws (full corpus): ~60 minutes

        **GPU Memory:** The embedding model uses ~1.5GB GPU memory and
        auto-unloads after 5 minutes idle.
        """
        # Validate input
        validated = IngestGermanLawsInput(
            max_laws=max_laws,
            max_norms_per_law=max_norms_per_law,
        )

        # Import here to avoid loading heavy modules at startup
        from app.ingestion.pipeline import ingest_german_laws as ingest_impl

        try:
            result = ingest_impl(
                max_laws=validated.max_laws,
                max_norms_per_law=validated.max_norms_per_law,
            )

            return result.to_dict()

        except Exception as e:
            return {
                "error": "Ingestion failed",
                "message": str(e),
                "status": "failed",
            }

    return ingest_german_laws


def create_get_law_stats(cache: RefCache) -> Any:
    """Create a get_law_stats tool function bound to the given cache.

    Args:
        cache: The RefCache instance for caching results.

    Returns:
        The get_law_stats tool function.
    """

    @cache.cached(namespace="stats")
    async def get_law_stats() -> dict[str, Any]:
        """Get statistics about the German law vector store.

        Returns information about:
        - Total documents in collection
        - Embedding model configuration
        - GPU/CPU device status
        - Memory usage (if GPU)
        - Sample of unique laws in collection

        Use this to check ingestion progress and model status.

        Returns:
            Dictionary with collection and model statistics.
        """
        # Import here to avoid loading heavy modules at startup
        from app.config import get_settings
        from app.ingestion.embeddings import GermanLawEmbeddingStore

        settings = get_settings()

        try:
            store = GermanLawEmbeddingStore(
                model_name=settings.embedding_model,
            )

            stats = store.stats()

            return {
                "status": "ok",
                **stats,
            }

        except Exception as e:
            # Return partial stats even if model isn't loaded
            return {
                "status": "error",
                "message": str(e),
                "embedding_model": settings.embedding_model,
                "chroma_persist_path": settings.chroma_persist_path,
            }

    return get_law_stats


def create_get_law_by_id(cache: RefCache) -> Any:
    """Create a get_law_by_id tool function for exact lookups.

    Args:
        cache: The RefCache instance for caching results.

    Returns:
        The get_law_by_id tool function.
    """

    @cache.cached(namespace="german_laws")
    async def get_law_by_id(
        law_abbrev: str,
        norm_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a specific law or norm by its identifier.

        Retrieves the full text of a specific law section without semantic search.
        Use this when you know the exact law and section you want.

        Args:
            law_abbrev: Law abbreviation (e.g., 'BGB', 'StGB', 'GG').
            norm_id: Optional norm identifier (e.g., '§ 433', 'Art. 1').

        Returns:
            The law/norm content and metadata, or list of norms if no norm_id.

        Examples:
            - get_law_by_id("BGB", "§ 433") → Purchase contract section
            - get_law_by_id("GG", "Art. 1") → Human dignity article
            - get_law_by_id("BGB") → List of all BGB sections in collection
        """
        # Import here to avoid loading heavy modules at startup
        from app.config import get_settings
        from app.ingestion.embeddings import GermanLawEmbeddingStore

        settings = get_settings()

        try:
            store = GermanLawEmbeddingStore(
                model_name=settings.embedding_model,
            )

            results = store.get_by_law(
                law_abbrev=law_abbrev.upper(),
                norm_id=norm_id,
            )

            if not results:
                return {
                    "error": "Not found",
                    "message": f"No documents found for {law_abbrev}"
                    + (f" {norm_id}" if norm_id else ""),
                    "law_abbrev": law_abbrev,
                    "norm_id": norm_id,
                }

            return {
                "law_abbrev": law_abbrev.upper(),
                "norm_id": norm_id,
                "count": len(results),
                "results": [
                    {
                        "doc_id": r.doc_id,
                        "content": r.content,
                        **r.metadata,
                    }
                    for r in results
                ],
            }

        except Exception as e:
            return {
                "error": "Lookup failed",
                "message": str(e),
                "law_abbrev": law_abbrev,
                "norm_id": norm_id,
            }

    return get_law_by_id


__all__ = [
    "IngestGermanLawsInput",
    "SearchLawsInput",
    "create_get_law_by_id",
    "create_get_law_stats",
    "create_search_laws",
]
