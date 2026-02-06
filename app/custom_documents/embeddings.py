"""Custom document embedding store backed by ChromaDB.

This module provides a ChromaDB-backed vector store for **user-ingested custom
documents** (e.g., case files, briefs, contracts). It is designed to be used by
MCP tools that must enforce multi-tenant isolation (via `tenant_id`) and optional
case scoping (via `case_id`).

Key design goals:
- Separate collection from the German federal law corpus
- Enforceable metadata-based filtering (`tenant_id` required)
- Deterministic document and chunk identifiers
- Safe replace support for re-ingestion (delete prior chunks by tenant/case/document)
- No sensitive data exposure in logs or exceptions

Important note:
ChromaDB metadata supports only `str`, `int`, `float`, `bool`. Lists must be
encoded, and complex objects must be stringified.

Example:
    >>> store = CustomDocumentEmbeddingStore()
    >>> result = store.add_text_chunks(
    ...     chunks=[
    ...         TextChunk(
    ...             chunk_id="doc_1:0",
    ...             text="The tenant reports a mold issue in the bathroom.",
    ...             metadata={"document_id": "doc_1", "tenant_id": "t_123"},
    ...         )
    ...     ],
    ... )
    >>> assert result.vectors_added == 1
    >>> hits = store.search(
    ...     query="mold in bathroom",
    ...     n_results=3,
    ...     where={"tenant_id": {"$eq": "t_123"}},
    ... )
    >>> _ = hits[0].metadata["document_id"]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from pydantic import BaseModel, Field

from app.config import get_settings
from app.ingestion.embeddings import DEFAULT_MODEL_NAME

logger = logging.getLogger(__name__)

CUSTOM_DOCUMENTS_COLLECTION_NAME = "custom_documents"


class IngestDocumentMetadata(BaseModel):
    """Metadata for a custom document.

    Attributes:
        tenant_id: Tenant identifier used for isolation (required).
        case_id: Optional case identifier to further scope searches.
        source_name: Human-friendly label for the document (e.g., filename).
        tags: Optional list of tags. Stored as CSV in metadata for filtering.
        extra: Optional shallow extra metadata as string->string.
    """

    tenant_id: str = Field(min_length=1, max_length=200)
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=512)
    tags: list[str] | None = Field(default=None, max_length=50)
    extra: dict[str, str] | None = None


@dataclass(frozen=True)
class TextChunk:
    """A single chunk of text to embed and store."""

    chunk_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AddChunksResult:
    """Summary result of an add/upsert operation."""

    vectors_added: int
    chunk_ids: list[str]


@dataclass(frozen=True)
class SearchHit:
    """One semantic search hit."""

    chunk_id: str
    content: str
    metadata: dict[str, Any]
    distance: float

    @property
    def similarity(self) -> float:
        """Convert Chroma distance to similarity score (0..1).

        For cosine distance, Chroma uses `distance = 1 - cosine_similarity`.
        """
        return max(0.0, 1.0 - self.distance)


@dataclass
class CustomDocumentEmbeddingStore:
    """Vector store for custom user documents.

    Uses the same embedding backend as the German law store:
    - TEI (HTTP) if `USE_TEI=true`
    - local model manager otherwise

    Attributes:
        model_name: Embedding model name (defaults to the project default).
        persist_path: Path to Chroma persistence directory.
        collection_name: Chroma collection name for custom docs.

    Notes:
        - This store does not enforce permission checks by itself. Callers must
          enforce `tenant_id` scoping by passing `where` clauses that include
          tenant constraints, or by using helper builders that always include it.
    """

    model_name: str = DEFAULT_MODEL_NAME
    persist_path: Path = field(
        default_factory=lambda: Path(get_settings().chroma_persist_path)
    )
    collection_name: str = CUSTOM_DOCUMENTS_COLLECTION_NAME
    _client: chromadb.PersistentClient | None = field(default=None, repr=False)
    _collection: chromadb.Collection | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Ensure persistence path exists."""
        self.persist_path = Path(self.persist_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)

    @property
    def model(self) -> Any:
        """Return the embedding model (TEI client or local model manager)."""
        settings = get_settings()
        if settings.use_tei:
            from app.ingestion.tei_client import get_tei_client

            return get_tei_client(settings.tei_url)
        from app.ingestion.model_manager import get_embedding_model

        return get_embedding_model(self.model_name)

    @property
    def client(self) -> chromadb.PersistentClient:
        """Lazy-load persistent Chroma client."""
        if self._client is None:
            logger.info("Initializing ChromaDB at: %s", self.persist_path)
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
        """Get or create the custom documents collection."""
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={
                    "description": "Custom user documents (case files, briefs, contracts)",
                    "embedding_model": self.model_name,
                    "hnsw:space": "cosine",
                },
            )
            logger.info(
                "Collection '%s' ready. Document count: %d",
                self.collection_name,
                self._collection.count(),
            )
        return self._collection

    def _prepare_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Prepare metadata for ChromaDB storage.

        ChromaDB only supports: str, int, float, bool.
        - None values are removed
        - lists become CSV strings
        - other objects become strings

        This method must not log sensitive values.
        """
        clean_metadata: dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, str | int | float | bool):
                clean_metadata[key] = value
                continue
            if isinstance(value, list):
                # Keep stable representation for equality-based filters
                clean_metadata[key] = ",".join(str(item) for item in value)
                continue
            clean_metadata[key] = str(value)
        return clean_metadata

    @staticmethod
    def normalize_tags_csv(tags: list[str] | None) -> str | None:
        """Normalize tags into a deterministic CSV string.

        Args:
            tags: List of tag strings (may include whitespace).

        Returns:
            Normalized CSV string (sorted, lowercased, unique) or None.

        Example:
            >>> CustomDocumentEmbeddingStore.normalize_tags_csv([" Medical ", "urgent", "urgent"])
            'medical,urgent'
        """
        if not tags:
            return None
        normalized = sorted({tag.strip().lower() for tag in tags if tag.strip()})
        if not normalized:
            return None
        return ",".join(normalized)

    @staticmethod
    def build_tenant_where(
        tenant_id: str,
        *,
        case_id: str | None = None,
        document_id: str | None = None,
        source_name: str | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """Build a Chroma `where` clause enforcing tenant isolation.

        This helper ALWAYS includes `tenant_id` and optionally adds other filters.

        Args:
            tenant_id: Required tenant identifier.
            case_id: Optional case identifier.
            document_id: Optional document identifier.
            source_name: Optional source name (exact match).
            tag: Optional single tag token; matched using `tags_csv` contains
                semantics are NOT supported by Chroma, so this is implemented as
                exact match against a normalized CSV. (Best-effort for v1.)

        Returns:
            A Chroma `where` dict with `$and` when multiple conditions exist.
        """
        conditions: list[dict[str, Any]] = [{"tenant_id": {"$eq": tenant_id}}]

        if case_id:
            conditions.append({"case_id": {"$eq": case_id}})
        if document_id:
            conditions.append({"document_id": {"$eq": document_id}})
        if source_name:
            conditions.append({"source_name": {"$eq": source_name}})
        if tag:
            # Best-effort v1: `tags_csv` is stored as a normalized CSV string
            # (sorted, lowercased, unique). Chroma can't do "contains" on CSV,
            # so we also store a single-tag field `tag` for equality filtering.
            normalized_tag = tag.strip().lower()
            if normalized_tag:
                conditions.append({"tag": {"$eq": normalized_tag}})

        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def delete_document_chunks(
        self,
        *,
        tenant_id: str,
        document_id: str,
        case_id: str | None = None,
        source_name: str | None = None,
    ) -> int:
        """Delete all chunks for a document scoped by tenant (and optional case).

        This is intended for safe "replace" re-ingestion flows, where you want to
        remove existing chunks for a document before writing new chunks.

        Args:
            tenant_id: Required tenant identifier (isolation boundary).
            document_id: Document identifier whose chunks should be deleted.
            case_id: Optional case identifier to further scope deletion.
            source_name: Optional source name to further scope deletion (exact match).

        Returns:
            Number of vectors deleted (best-effort). If the underlying store does
            not report a count, returns 0.

        Raises:
            ValueError: If tenant_id or document_id are empty.
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")
        if not document_id or not document_id.strip():
            raise ValueError("document_id must be non-empty")

        where = self.build_tenant_where(
            tenant_id.strip(),
            case_id=case_id,
            document_id=document_id.strip(),
            source_name=source_name,
        )

        # Chroma deletion returns None; count is not reliably available across
        # versions. We return 0 in that case to keep this API stable.
        self.collection.delete(where=where)
        return 0

    def add_text_chunks(
        self,
        chunks: list[TextChunk],
        *,
        batch_size: int = 256,
        replace: bool = False,
    ) -> AddChunksResult:
        """Embed and upsert custom text chunks.

        Args:
            chunks: List of chunks. Each chunk must include required metadata
                fields for proper filtering (at minimum: `tenant_id`,
                `document_id`, `chunk_id`, `source_name`, `ingested_at`).
            batch_size: Batch size for embedding calls.
            replace: If True, best-effort delete of existing chunks for each
                `(tenant_id, case_id, document_id[, source_name])` found in the
                provided chunks before upserting new vectors.

        Returns:
            Summary result with number of vectors added and chunk IDs.

        Raises:
            ValueError: If no chunks were provided or chunk content is empty.
        """
        if not chunks:
            raise ValueError("No chunks provided")

        filtered_chunks: list[TextChunk] = []
        for chunk in chunks:
            if not chunk.text or not chunk.text.strip():
                continue
            filtered_chunks.append(chunk)

        if not filtered_chunks:
            raise ValueError("All chunks were empty or whitespace")

        if replace:
            # Delete per logical document scope (tenant + optional case + document_id).
            # This is best-effort; deletion does not surface document content.
            seen_scopes: set[tuple[str, str, str | None, str | None]] = set()
            for chunk in filtered_chunks:
                tenant_id_value = str(chunk.metadata.get("tenant_id", "")).strip()
                document_id_value = str(chunk.metadata.get("document_id", "")).strip()
                case_id_value: str | None = chunk.metadata.get("case_id")  # type: ignore[assignment]
                source_name_value: str | None = chunk.metadata.get("source_name")  # type: ignore[assignment]

                scope_key = (
                    tenant_id_value,
                    document_id_value,
                    case_id_value,
                    source_name_value,
                )
                if scope_key in seen_scopes:
                    continue
                seen_scopes.add(scope_key)

                if tenant_id_value and document_id_value:
                    self.delete_document_chunks(
                        tenant_id=tenant_id_value,
                        document_id=document_id_value,
                        case_id=case_id_value,
                        source_name=source_name_value,
                    )

        chunk_ids: list[str] = []
        for index in range(0, len(filtered_chunks), batch_size):
            batch = filtered_chunks[index : index + batch_size]

            ids: list[str] = []
            texts: list[str] = []
            metadatas: list[dict[str, Any]] = []

            for chunk in batch:
                ids.append(chunk.chunk_id)
                texts.append(chunk.text)
                metadatas.append(self._prepare_metadata(chunk.metadata))

            embeddings = self.model.encode(
                texts,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

            self.collection.upsert(
                ids=ids,
                embeddings=embeddings.tolist(),
                documents=texts,
                metadatas=metadatas,
            )
            chunk_ids.extend(ids)

        return AddChunksResult(vectors_added=len(chunk_ids), chunk_ids=chunk_ids)

    def search(
        self,
        query: str,
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Search custom documents using semantic similarity.

        Args:
            query: Free-text query.
            n_results: Number of results to return (1..50 is typical).
            where: Optional metadata filter. Callers SHOULD include tenant scoping.
            where_document: Optional document-level filter (Chroma feature).

        Returns:
            List of SearchHit objects, ordered best-first.

        Raises:
            ValueError: If query is empty.
        """
        if not query or not query.strip():
            raise ValueError("Query must be non-empty")

        query_embedding = self.model.encode([query], convert_to_numpy=True)[0]

        raw = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=n_results,
            where=where,
            where_document=where_document,
            include=["documents", "metadatas", "distances"],
        )

        ids = raw.get("ids", [[]])[0]
        documents = raw.get("documents", [[]])[0]
        metadatas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]

        hits: list[SearchHit] = []
        for chunk_id, content, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=False
        ):
            hits.append(
                SearchHit(
                    chunk_id=chunk_id,
                    content=content,
                    metadata=metadata or {},
                    distance=float(distance),
                )
            )

        return hits

    def count(self) -> int:
        """Return number of vectors in the custom documents collection."""
        return int(self.collection.count())
