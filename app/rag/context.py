"""Context builder for RAG pipeline.

Converts search results from ChromaDB into citation-formatted sources
ready for LLM consumption.

Usage:
    from app.rag.context import build_context_from_results

    # From GermanLawEmbeddingStore.search() results
    context = build_context_from_results(search_results, max_sources=5)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ingestion.embeddings import SearchResult


@dataclass
class SourceContext:
    """A single source with citation metadata.

    Attributes:
        index: 1-based citation index (e.g., 1 for [1])
        law_abbrev: Law abbreviation (e.g., "BGB", "StGB")
        norm_id: Norm identifier (e.g., "§ 433", "Art. 1")
        title: Section title
        content: Full text content
        doc_id: Original document ID
        similarity: Similarity score (0-1)
    """

    index: int
    law_abbrev: str
    norm_id: str
    title: str
    content: str
    doc_id: str
    similarity: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "citation": f"[{self.index}]",
            "law": self.law_abbrev,
            "norm_id": self.norm_id,
            "title": self.title,
            "excerpt": self._get_excerpt(200),
            "similarity": round(self.similarity, 3),
            "doc_id": self.doc_id,
        }

    def to_prompt_dict(self) -> dict[str, str]:
        """Convert to dictionary for prompt formatting."""
        return {
            "law_abbrev": self.law_abbrev,
            "norm_id": self.norm_id,
            "title": self.title,
            "content": self.content,
        }

    def _get_excerpt(self, max_length: int = 200) -> str:
        """Get a truncated excerpt of the content."""
        if len(self.content) <= max_length:
            return self.content
        return self.content[: max_length - 3] + "..."


@dataclass
class RAGContext:
    """Complete context for RAG generation.

    Attributes:
        question: Original user question
        sources: List of SourceContext objects
        total_retrieved: Total number of results from search
    """

    question: str
    sources: list[SourceContext]
    total_retrieved: int

    @property
    def has_sources(self) -> bool:
        """Check if any sources are available."""
        return len(self.sources) > 0

    def get_prompt_sources(self) -> list[dict[str, str]]:
        """Get sources formatted for prompt templates."""
        return [source.to_prompt_dict() for source in self.sources]

    def get_response_sources(self) -> list[dict[str, Any]]:
        """Get sources formatted for API response."""
        return [source.to_dict() for source in self.sources]


def build_context_from_results(
    question: str,
    search_results: list[SearchResult],
    max_sources: int = 5,
    min_similarity: float = 0.0,
) -> RAGContext:
    """Build RAG context from search results.

    Args:
        question: User's legal question
        search_results: Results from GermanLawEmbeddingStore.search()
        max_sources: Maximum number of sources to include
        min_similarity: Minimum similarity score to include (0-1)

    Returns:
        RAGContext with formatted sources ready for LLM

    Example:
        >>> from app.ingestion.embeddings import GermanLawEmbeddingStore
        >>> store = GermanLawEmbeddingStore()
        >>> results = store.search("Kaufvertrag", n_results=10)
        >>> context = build_context_from_results("Was ist ein Kaufvertrag?", results)
        >>> print(context.sources[0].law_abbrev)  # "BGB"
    """
    sources: list[SourceContext] = []

    for i, result in enumerate(search_results[:max_sources], start=1):
        # Filter by minimum similarity
        if result.similarity < min_similarity:
            continue

        # Extract metadata with fallbacks
        metadata = result.metadata
        source = SourceContext(
            index=i,
            law_abbrev=metadata.get("law_abbrev", ""),
            norm_id=metadata.get("norm_id", ""),
            title=metadata.get("title", metadata.get("norm_title", "")),
            content=result.content,
            doc_id=result.doc_id,
            similarity=result.similarity,
        )
        sources.append(source)

    return RAGContext(
        question=question,
        sources=sources,
        total_retrieved=len(search_results),
    )


def extract_metadata_from_result(result: SearchResult) -> dict[str, Any]:
    """Extract and normalize metadata from a search result.

    Args:
        result: SearchResult from embedding store

    Returns:
        Normalized metadata dictionary
    """
    metadata = result.metadata
    return {
        "law_abbrev": metadata.get("law_abbrev", ""),
        "norm_id": metadata.get("norm_id", ""),
        "title": metadata.get("title", metadata.get("norm_title", "")),
        "level": metadata.get("level", "norm"),
        "source_url": metadata.get("source_url", ""),
        "jurisdiction": metadata.get("jurisdiction", "federal"),
    }


__all__ = [
    "RAGContext",
    "SourceContext",
    "build_context_from_results",
    "extract_metadata_from_result",
]
