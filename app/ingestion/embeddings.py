"""German Law Embedding Store using ChromaDB and sentence-transformers.

Provides vector storage and semantic search for German federal law documents.
Uses ChromaDB for persistence and sentence-transformers for embeddings.

Key features:
- Automatic embedding generation using multilingual model
- Metadata filtering for jurisdiction, law, and document level
- Batch ingestion for large document sets
- Similarity search with configurable result count

Usage:
    >>> from app.ingestion.embeddings import GermanLawEmbeddingStore
    >>> store = GermanLawEmbeddingStore()
    >>> store.add_documents(documents)
    >>> results = store.search("Kaufvertrag Pflichten", n_results=5)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import chromadb
from chromadb.config import Settings as ChromaSettings

if TYPE_CHECKING:
    from chromadb.api import ClientAPI
    from chromadb.api.types import Metadata, WhereDocument
    from langchain_core.documents import Document

from app.config import get_settings

logger = logging.getLogger(__name__)

# Default embedding model - German-English bilingual model optimized for German text
# jinaai/jina-embeddings-v2-base-de:
# - 161M parameters, 768-dim embeddings
# - 8192 token context length (ideal for legal text)
# - Specifically trained for German with English cross-lingual support
# - Requires trust_remote_code=True
DEFAULT_MODEL_NAME = "jinaai/jina-embeddings-v2-base-de"

# Default persistence path
DEFAULT_PERSIST_PATH = Path.home() / ".local" / "share" / "legal-mcp" / "chroma"

# Collection name for German federal laws
COLLECTION_NAME = "german_laws"


@dataclass
class SearchResult:
    """Result from a semantic search query."""

    doc_id: str
    content: str
    metadata: dict[str, Any]
    distance: float  # Lower is more similar (for cosine)

    @property
    def similarity(self) -> float:
        """Convert distance to similarity score (0-1 range)."""
        # ChromaDB uses L2 distance by default, but we use cosine
        # For cosine distance: similarity = 1 - distance
        return max(0.0, 1.0 - self.distance)


@dataclass
class GermanLawEmbeddingStore:
    """Vector store for German federal law documents.

    Uses ChromaDB for persistence and sentence-transformers for embeddings.
    Supports metadata filtering by jurisdiction, law abbreviation, and level.

    Attributes:
        model_name: Name of the sentence-transformers model to use
        persist_path: Path to ChromaDB persistence directory
        collection_name: Name of the ChromaDB collection

    Example:
        >>> store = GermanLawEmbeddingStore()
        >>> # Add documents from loader
        >>> from legal_mcp.loaders import GermanLawHTMLLoader
        >>> loader = GermanLawHTMLLoader(url, "BGB")
        >>> store.add_documents(loader.load())
        >>> # Search
        >>> results = store.search("Kaufvertrag", n_results=5)
        >>> for r in results:
        ...     print(f"{r.metadata['law_abbrev']} {r.metadata['norm_id']}: {r.similarity:.2f}")
    """

    model_name: str = DEFAULT_MODEL_NAME
    persist_path: Path = field(default_factory=lambda: DEFAULT_PERSIST_PATH)
    collection_name: str = COLLECTION_NAME
    chroma_host: str | None = field(default=None)
    chroma_port: int = field(default=8000)
    _client: ClientAPI | None = field(default=None, repr=False)
    _collection: chromadb.Collection | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Resolve ChromaDB connection mode and ensure paths exist.

        If ``chroma_host`` is not provided explicitly, falls back to the
        ``chroma_host`` setting from the application config.  When a host
        is configured the store connects via ``HttpClient``; otherwise it
        uses a local ``PersistentClient`` at ``persist_path``.
        """
        # Auto-detect chroma_host/port from config if not provided explicitly
        if self.chroma_host is None:
            settings = get_settings()
            self.chroma_host = settings.chroma_host
            self.chroma_port = settings.chroma_port

        # Only create local directories when using PersistentClient
        if self.chroma_host is None:
            self.persist_path = Path(self.persist_path)
            self.persist_path.mkdir(parents=True, exist_ok=True)

    @property
    def model(self) -> Any:
        """Get the TEI embedding client."""
        settings = get_settings()
        from app.ingestion.tei_client import get_tei_client

        return get_tei_client(settings.tei_url)

    @property
    def client(self) -> ClientAPI:
        """Lazy-load the ChromaDB client (HTTP or persistent)."""
        if self._client is None:
            if self.chroma_host is not None:
                logger.info(
                    "Connecting to ChromaDB server at: %s:%d",
                    self.chroma_host,
                    self.chroma_port,
                )
                self._client = chromadb.HttpClient(
                    host=self.chroma_host,
                    port=self.chroma_port,
                    settings=ChromaSettings(
                        anonymized_telemetry=False,
                    ),
                )
            else:
                logger.info("Initializing local ChromaDB at: %s", self.persist_path)
                self._client = chromadb.PersistentClient(
                    path=str(self.persist_path),
                    settings=ChromaSettings(
                        anonymized_telemetry=False,
                        allow_reset=True,
                    ),
                )
        return self._client

    @property
    def collection(self) -> chromadb.Collection:
        """Get or create the ChromaDB collection."""
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={
                    "description": "German federal law documents",
                    "embedding_model": self.model_name,
                    "hnsw:space": "cosine",  # Use cosine similarity
                },
            )
            logger.info(
                "Collection '%s' ready. Document count: %d",
                self.collection_name,
                self._collection.count(),
            )
        return self._collection

    def _prepare_metadata(self, metadata: dict[str, Any]) -> Metadata:
        """Prepare metadata for ChromaDB storage.

        ChromaDB only supports str, int, float, bool as metadata values.
        Convert any other types to strings.
        """
        clean_metadata: dict[str, str | int | float | bool | None] = {}
        for key, value in metadata.items():
            if value is None:
                continue  # Skip None values
            if isinstance(value, str | int | float | bool):
                clean_metadata[key] = value
            elif isinstance(value, list):
                # Convert lists to comma-separated strings
                clean_metadata[key] = ",".join(str(v) for v in value)
            else:
                clean_metadata[key] = str(value)
        return cast("Metadata", clean_metadata)

    def add_documents(
        self,
        documents: list[Document],
        batch_size: int = 256,
        show_progress: bool = True,
    ) -> int:
        """Add LangChain Documents to the vector store.

        Documents are embedded using the sentence-transformers model and
        stored in ChromaDB with their metadata.

        Args:
            documents: List of LangChain Document objects
            batch_size: Number of documents to embed at once
            show_progress: Whether to log progress

        Returns:
            Number of documents added

        Raises:
            ValueError: If documents have no page_content
        """
        if not documents:
            return 0

        total_added = 0
        total_batches = (len(documents) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(documents), batch_size):
            batch = documents[batch_idx : batch_idx + batch_size]
            batch_num = batch_idx // batch_size + 1

            if show_progress:
                logger.info(
                    "Processing batch %d/%d (%d documents)",
                    batch_num,
                    total_batches,
                    len(batch),
                )

            # Extract content and metadata, deduplicating by doc_id
            seen_ids: set[str] = set()
            ids: list[str] = []
            contents: list[str] = []
            metadatas: list[Metadata] = []

            for doc in batch:
                if not doc.page_content:
                    continue

                # Cast bare dict from langchain Document to typed dict
                doc_meta = cast("dict[str, Any]", doc.metadata)

                # Use doc_id from metadata or generate one
                doc_id = doc_meta.get("doc_id", f"doc_{hash(doc.page_content)}")

                # Skip duplicates within batch
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)

                ids.append(doc_id)
                contents.append(doc.page_content)
                metadatas.append(self._prepare_metadata(doc_meta))

            if not contents:
                continue

            # Generate embeddings using the singleton model manager
            embeddings = self.model.encode(
                contents,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

            # Upsert to ChromaDB (handles duplicates by doc_id)
            self.collection.upsert(
                ids=ids,
                embeddings=embeddings.tolist(),
                documents=contents,
                metadatas=metadatas,
            )

            total_added += len(ids)

        if show_progress:
            logger.info("Added %d documents to collection", total_added)

        return total_added

    def search(
        self,
        query: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: WhereDocument | None = None,
    ) -> list[SearchResult]:
        """Search for similar documents using semantic similarity.

        Args:
            query: Search query text
            n_results: Maximum number of results to return
            where: Metadata filter (e.g., {"law_abbrev": "BGB"})
            where_document: Document content filter

        Returns:
            List of SearchResult objects ordered by similarity

        Example:
            >>> # Search in BGB only
            >>> results = store.search(
            ...     "Kaufvertrag",
            ...     where={"law_abbrev": "BGB"},
            ...     n_results=5
            ... )
        """
        # Generate query embedding
        query_embedding = self.model.encode([query], convert_to_numpy=True)[0]

        # Query ChromaDB
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=n_results,
            where=where,
            where_document=where_document,
            include=["documents", "metadatas", "distances"],
        )

        # Convert to SearchResult objects
        search_results: list[SearchResult] = []

        if not results["ids"] or not results["ids"][0]:
            return search_results

        ids = results["ids"][0]
        documents = results["documents"][0] if results["documents"] else [""] * len(ids)
        raw_metadatas = (
            results["metadatas"][0] if results["metadatas"] else [{}] * len(ids)
        )
        distances = (
            results["distances"][0] if results["distances"] else [0.0] * len(ids)
        )

        for i, doc_id in enumerate(ids):
            search_results.append(
                SearchResult(
                    doc_id=doc_id,
                    content=documents[i] if documents else "",
                    metadata=dict(raw_metadatas[i]) if raw_metadatas else {},
                    distance=distances[i] if distances else 0.0,
                )
            )

        return search_results

    def get_by_id(self, doc_id: str) -> SearchResult | None:
        """Retrieve a document by its ID.

        Args:
            doc_id: The document ID

        Returns:
            SearchResult if found, None otherwise
        """
        result = self.collection.get(
            ids=[doc_id],
            include=["documents", "metadatas"],
        )

        if not result["ids"]:
            return None

        raw_metadata = result["metadatas"][0] if result["metadatas"] else {}
        return SearchResult(
            doc_id=result["ids"][0],
            content=result["documents"][0] if result["documents"] else "",
            metadata=dict(raw_metadata) if raw_metadata else {},
            distance=0.0,  # Exact match
        )

    def get_by_law(
        self,
        law_abbrev: str,
        norm_id: str | None = None,
        level: str = "norm",
    ) -> list[SearchResult]:
        """Get documents by law abbreviation and optional norm ID.

        Args:
            law_abbrev: Law abbreviation (e.g., "BGB", "StGB")
            norm_id: Optional norm identifier (e.g., "§ 433")
            level: Document level filter ("norm" or "paragraph")

        Returns:
            List of matching documents
        """
        # ChromaDB requires $and operator for multiple conditions
        conditions = [
            {"law_abbrev": {"$eq": law_abbrev}},
            {"level": {"$eq": level}},
        ]
        if norm_id:
            conditions.append({"norm_id": {"$eq": norm_id}})

        where_filter: dict[str, Any] = {"$and": conditions}

        result = self.collection.get(
            where=where_filter,
            include=["documents", "metadatas"],
        )

        search_results: list[SearchResult] = []
        if not result["ids"]:
            return search_results

        raw_metadatas = result["metadatas"] or []
        for i, doc_id in enumerate(result["ids"]):
            raw_meta = raw_metadatas[i] if i < len(raw_metadatas) else {}
            search_results.append(
                SearchResult(
                    doc_id=doc_id,
                    content=result["documents"][i] if result["documents"] else "",
                    metadata=dict(raw_meta) if raw_meta else {},
                    distance=0.0,
                )
            )

        return search_results

    def count(self) -> int:
        """Get the total number of documents in the collection."""
        return self.collection.count()

    def delete_all(self) -> None:
        """Delete all documents from the collection.

        Warning: This is destructive and cannot be undone.
        """
        # ChromaDB doesn't have a direct "delete all" method
        # We need to delete the collection and recreate it
        self.client.delete_collection(self.collection_name)
        self._collection = None
        logger.warning(
            "Deleted all documents from collection '%s'", self.collection_name
        )

    def stats(self) -> dict[str, Any]:
        """Get statistics about the collection.

        Returns:
            Dictionary with collection statistics
        """
        count = self.count()

        # Get unique values for key metadata fields
        # Note: ChromaDB doesn't have native aggregation, so we sample
        stats: dict[str, Any] = {
            "total_documents": count,
            "collection_name": self.collection_name,
            "embedding_model": self.model_name,
            "embedding_dimension": self.model.get_sentence_embedding_dimension(),
            "persist_path": str(self.persist_path),
            "model_stats": self.model.stats(),
        }

        if count > 0:
            # Sample to get metadata field distribution
            sample = self.collection.get(
                limit=min(1000, count),
                include=["metadatas"],
            )
            if sample["metadatas"]:
                # Count unique laws — cast metadata values to str for set ops
                laws = {
                    str(m.get("law_abbrev"))
                    for m in sample["metadatas"]
                    if m and m.get("law_abbrev") is not None
                }
                levels = {
                    str(m.get("level"))
                    for m in sample["metadatas"]
                    if m and m.get("level") is not None
                }
                stats["sampled_unique_laws"] = len(laws)
                stats["levels"] = sorted(levels)

        return stats
