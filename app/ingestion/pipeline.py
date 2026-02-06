"""Ingestion pipeline for German federal law documents.

Orchestrates the full ingestion flow:
1. Discovery: Find all laws and norms from gesetze-im-internet.de
2. Loading: Parse HTML pages into LangChain Documents (concurrent)
3. Embedding: Convert to vectors and store in ChromaDB

Designed for use with mcp-refcache's async_timeout feature for long-running jobs.

Usage:
    >>> from app.ingestion.pipeline import ingest_german_laws
    >>> result = ingest_german_laws(max_laws=10)  # Test with 10 laws
    >>> print(f"Ingested {result['documents_added']} documents")
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.ingestion.embeddings import GermanLawEmbeddingStore

if TYPE_CHECKING:
    from langchain_core.documents import Document

# Lazy import heavy modules
# from legal_mcp.loaders import GermanLawDiscovery, GermanLawHTMLLoader

logger = logging.getLogger(__name__)


@dataclass
class IngestionProgress:
    """Track progress of the ingestion pipeline."""

    total_laws: int = 0
    processed_laws: int = 0
    total_norms: int = 0
    processed_norms: int = 0
    documents_added: int = 0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time in seconds."""
        return time.time() - self.start_time

    @property
    def laws_per_second(self) -> float:
        """Processing rate for laws."""
        if self.elapsed_seconds == 0:
            return 0.0
        return self.processed_laws / self.elapsed_seconds

    @property
    def estimated_remaining_seconds(self) -> float:
        """Estimated time remaining based on current rate."""
        if self.laws_per_second == 0:
            return float("inf")
        remaining_laws = self.total_laws - self.processed_laws
        return remaining_laws / self.laws_per_second

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_laws": self.total_laws,
            "processed_laws": self.processed_laws,
            "total_norms": self.total_norms,
            "processed_norms": self.processed_norms,
            "documents_added": self.documents_added,
            "error_count": len(self.errors),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "laws_per_second": round(self.laws_per_second, 2),
            "estimated_remaining_seconds": round(self.estimated_remaining_seconds, 1),
        }


@dataclass
class IngestionResult:
    """Result of a completed ingestion run."""

    documents_added: int
    laws_processed: int
    norms_processed: int
    errors: list[str]
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "documents_added": self.documents_added,
            "laws_processed": self.laws_processed,
            "norms_processed": self.norms_processed,
            "error_count": len(self.errors),
            "errors": self.errors[:10],  # Limit errors in response
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "status": "complete",
        }


def _load_norm_documents(
    law_abbrev: str,
    norm_url: str,
    delay: float = 0.1,
) -> list[Document]:
    """Load documents for a single norm URL.

    Args:
        law_abbrev: Law abbreviation (e.g., "BGB")
        norm_url: URL to the norm HTML page
        delay: Delay in seconds before request (rate limiting)

    Returns:
        List of LangChain Documents (norm + paragraphs)
    """
    # Import here to avoid circular imports and heavy module loading
    from legal_mcp.loaders import GermanLawHTMLLoader

    # Rate limiting delay to avoid hammering the server
    if delay > 0:
        time.sleep(delay)

    loader = GermanLawHTMLLoader(url=norm_url, law_abbrev=law_abbrev)
    return loader.load()


def ingest_german_laws(
    max_laws: int | None = None,
    max_norms_per_law: int | None = None,
    batch_size: int = 256,
    persist_path: Path | str | None = None,
    progress_callback: callable | None = None,
    max_workers: int = 16,
) -> IngestionResult:
    """Ingest German federal laws into the vector store.

    This is designed to be a long-running operation. When used with
    mcp-refcache's @cache.cached(async_timeout=5.0), it runs in the
    background and returns a ref_id for polling.

    Args:
        max_laws: Maximum number of laws to process (None for all ~6,871)
        max_norms_per_law: Maximum norms per law (None for all)
        batch_size: Documents per embedding batch
        persist_path: Override ChromaDB persistence path
        progress_callback: Optional callback(IngestionProgress) for updates
        max_workers: Number of concurrent workers for fetching/parsing (default: 8)

    Returns:
        IngestionResult with statistics

    Example:
        >>> # Quick test with 5 laws
        >>> result = ingest_german_laws(max_laws=5)
        >>> print(f"Added {result.documents_added} documents")

        >>> # Full ingestion (takes ~30-60 minutes)
        >>> result = ingest_german_laws()
    """
    # Import discovery here to avoid loading heavy modules at import time
    from legal_mcp.loaders.discovery import GermanLawDiscovery

    settings = get_settings()
    progress = IngestionProgress()

    # Initialize embedding store
    store_path = (
        Path(persist_path) if persist_path else Path(settings.chroma_persist_path)
    )
    store = GermanLawEmbeddingStore(
        model_name=settings.embedding_model,
        persist_path=store_path,
    )

    # Phase 1: Discovery
    logger.info("Starting law discovery...")
    discovery = GermanLawDiscovery()

    # Collect all laws first to get total count
    laws = list(discovery.discover_laws())
    if max_laws:
        laws = laws[:max_laws]

    progress.total_laws = len(laws)
    logger.info("Discovered %d laws to process", progress.total_laws)

    # Phase 2: Collect all norm URLs for concurrent processing
    all_tasks: list[tuple[str, str]] = []  # (law_abbrev, norm_url)

    for law in laws:
        try:
            norms = list(discovery.discover_norms(law))
            if max_norms_per_law:
                norms = norms[:max_norms_per_law]
            progress.total_norms += len(norms)
            for norm in norms:
                all_tasks.append((law.abbreviation, norm.url))
        except Exception as e:
            error_msg = f"Error discovering norms for {law.abbreviation}: {e}"
            progress.errors.append(error_msg)
            logger.warning(error_msg)

    logger.info(
        "Collected %d norms to process with %d workers", len(all_tasks), max_workers
    )

    # Phase 3: Process norms concurrently
    document_batch: list[Document] = []
    batch_lock = Lock()
    processed_laws_set: set[str] = set()

    def process_norm(
        task: tuple[str, str],
    ) -> tuple[str, list[Document] | None, str | None]:
        """Process a single norm, return (law_abbrev, documents, error)."""
        law_abbrev, norm_url = task
        try:
            documents = _load_norm_documents(law_abbrev, norm_url)
            return (law_abbrev, documents, None)
        except Exception as e:
            return (law_abbrev, None, f"Error loading {norm_url}: {e}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_norm, task): task for task in all_tasks}

        for future in as_completed(futures):
            law_abbrev, documents, error = future.result()

            if error:
                progress.errors.append(error)
                logger.warning(error)
            elif documents:
                with batch_lock:
                    document_batch.extend(documents)
                    progress.processed_norms += 1

                    # Track unique laws processed
                    if law_abbrev not in processed_laws_set:
                        processed_laws_set.add(law_abbrev)
                        progress.processed_laws = len(processed_laws_set)

                    # Batch insert when we have enough documents
                    if len(document_batch) >= batch_size:
                        added = store.add_documents(document_batch, show_progress=False)
                        progress.documents_added += added
                        document_batch = []

                        # Log progress
                        logger.info(
                            "Progress: %d/%d laws, %d/%d norms, %d docs (%.1f norms/sec)",
                            progress.processed_laws,
                            progress.total_laws,
                            progress.processed_norms,
                            progress.total_norms,
                            progress.documents_added,
                            progress.processed_norms
                            / max(0.1, progress.elapsed_seconds),
                        )

            # Call progress callback if provided
            if progress_callback:
                progress_callback(progress)

    # Insert remaining documents
    if document_batch:
        added = store.add_documents(document_batch, show_progress=False)
        progress.documents_added += added

    progress.processed_laws = len(processed_laws_set)

    # Create result
    result = IngestionResult(
        documents_added=progress.documents_added,
        laws_processed=progress.processed_laws,
        norms_processed=progress.processed_norms,
        errors=progress.errors,
        elapsed_seconds=progress.elapsed_seconds,
    )

    logger.info(
        "Ingestion complete: %d documents from %d laws in %.1f seconds",
        result.documents_added,
        result.laws_processed,
        result.elapsed_seconds,
    )

    return result


def ingest_single_law(
    law_abbrev: str,
    persist_path: Path | str | None = None,
    max_workers: int = 16,
    batch_size: int = 128,
) -> IngestionResult:
    """Ingest a single law by abbreviation.

    Useful for testing or updating specific laws. Uses concurrent processing
    and TEI backend for efficient GPU utilization.

    Args:
        law_abbrev: Law abbreviation (e.g., "BGB", "GG", "StGB")
        persist_path: Override ChromaDB persistence path
        max_workers: Number of concurrent workers for fetching/parsing
        batch_size: Documents per embedding batch

    Returns:
        IngestionResult with statistics

    Example:
        >>> result = ingest_single_law("BGB")
        >>> print(f"Added {result.documents_added} BGB documents")
    """
    from legal_mcp.loaders.discovery import GermanLawDiscovery, LawInfo

    settings = get_settings()
    progress = IngestionProgress()
    progress.total_laws = 1

    logger.info("Starting ingestion for %s...", law_abbrev)

    # Initialize embedding store
    store_path = (
        Path(persist_path) if persist_path else Path(settings.chroma_persist_path)
    )
    store = GermanLawEmbeddingStore(
        model_name=settings.embedding_model,
        persist_path=store_path,
    )

    discovery = GermanLawDiscovery()

    # Find the law by abbreviation
    # Construct URL from abbreviation (lowercase)
    law_url = f"https://www.gesetze-im-internet.de/{law_abbrev.lower()}/"
    law = LawInfo(abbreviation=law_abbrev, title="", url=law_url)

    document_batch: list[Document] = []
    batch_lock = Lock()

    try:
        logger.info("Discovering norms for %s...", law_abbrev)
        norms = list(discovery.discover_norms(law))
        progress.total_norms = len(norms)
        logger.info("Found %d norms for %s", len(norms), law_abbrev)

        # Prepare tasks for concurrent processing
        norm_urls = [(law.abbreviation, norm.url) for norm in norms]

        def process_norm(
            task: tuple[str, str],
        ) -> tuple[list[Document] | None, str | None]:
            """Process a single norm, return (documents, error)."""
            abbrev, norm_url = task
            try:
                documents = _load_norm_documents(abbrev, norm_url)
                return (documents, None)
            except Exception as e:
                return (None, f"Error loading {norm_url}: {e}")

        # Process norms concurrently
        logger.info(
            "Processing %d norms with %d workers...", len(norm_urls), max_workers
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_norm, task): task for task in norm_urls}

            for future in as_completed(futures):
                documents, error = future.result()

                if error:
                    progress.errors.append(error)
                    logger.warning(error)
                elif documents:
                    with batch_lock:
                        document_batch.extend(documents)
                        progress.processed_norms += 1

                        # Log progress every 10 norms
                        if progress.processed_norms % 10 == 0:
                            rate = progress.processed_norms / max(
                                0.1, progress.elapsed_seconds
                            )
                            logger.info(
                                "[%s] Progress: %d/%d norms, %d docs (%.1f norms/sec)",
                                law_abbrev,
                                progress.processed_norms,
                                progress.total_norms,
                                len(document_batch),
                                rate,
                            )

                        # Batch insert when we have enough documents
                        if len(document_batch) >= batch_size:
                            added = store.add_documents(
                                document_batch, show_progress=False
                            )
                            progress.documents_added += added
                            logger.info(
                                "[%s] Batch inserted %d docs (total: %d)",
                                law_abbrev,
                                added,
                                progress.documents_added,
                            )
                            document_batch = []

        # Insert remaining documents
        if document_batch:
            added = store.add_documents(document_batch, show_progress=False)
            progress.documents_added += added
            logger.info(
                "[%s] Final batch: %d docs (total: %d)",
                law_abbrev,
                added,
                progress.documents_added,
            )

        progress.processed_laws = 1

    except Exception as e:
        error_msg = f"Error processing {law_abbrev}: {e}"
        progress.errors.append(error_msg)
        logger.error(error_msg)

    logger.info(
        "[%s] Complete: %d documents, %d norms in %.1fs (%d errors)",
        law_abbrev,
        progress.documents_added,
        progress.processed_norms,
        progress.elapsed_seconds,
        len(progress.errors),
    )

    return IngestionResult(
        documents_added=progress.documents_added,
        laws_processed=progress.processed_laws,
        norms_processed=progress.processed_norms,
        errors=progress.errors,
        elapsed_seconds=progress.elapsed_seconds,
    )


def search_laws(
    query: str,
    n_results: int = 10,
    law_abbrev: str | None = None,
    level: str | None = None,
    persist_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search German laws using semantic similarity.

    Convenience function for quick searches without managing the store directly.

    Args:
        query: Search query text
        n_results: Maximum results to return
        law_abbrev: Filter by law abbreviation (e.g., "BGB")
        level: Filter by document level ("norm" or "paragraph")
        persist_path: Override ChromaDB persistence path

    Returns:
        List of result dictionaries with content and metadata

    Example:
        >>> results = search_laws("Kaufvertrag Pflichten")
        >>> for r in results:
        ...     print(f"{r['law_abbrev']} {r['norm_id']}: {r['similarity']:.2f}")
    """
    settings = get_settings()
    store_path = (
        Path(persist_path) if persist_path else Path(settings.chroma_persist_path)
    )

    store = GermanLawEmbeddingStore(
        model_name=settings.embedding_model,
        persist_path=store_path,
    )

    # Build metadata filter
    # ChromaDB requires $and operator for multiple conditions
    where: dict[str, Any] | None = None
    if law_abbrev and level:
        # Both filters - use $and
        where = {
            "$and": [
                {"law_abbrev": {"$eq": law_abbrev}},
                {"level": {"$eq": level}},
            ]
        }
    elif law_abbrev:
        where = {"law_abbrev": {"$eq": law_abbrev}}
    elif level:
        where = {"level": {"$eq": level}}

    results = store.search(query, n_results=n_results, where=where)

    return [
        {
            "doc_id": r.doc_id,
            "content": r.content[:500] + "..." if len(r.content) > 500 else r.content,
            "similarity": round(r.similarity, 3),
            **r.metadata,
        }
        for r in results
    ]
