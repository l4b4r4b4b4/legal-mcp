"""MCP tools for ingesting and searching custom documents.

This module exposes MCP tool factory functions for:
- Ingesting user-provided plain text documents (e.g., case files) into a
  dedicated vector store collection.
- Ingesting Markdown files from disk under an allowlisted root directory.
- Converting files (e.g., PDFs) from disk under an allowlisted root to Markdown/text,
  primarily by writing the converted Markdown back to disk under the allowlisted root.
- Ingesting PDFs from disk (read → convert → write markdown → ingest).
- Semantic search over the ingested documents with filter capabilities.

Design goals:
- Mandatory tenant isolation (`tenant_id` is required for ingestion and search)
- Optional case scoping (`case_id`)
- Deterministic and stable identifiers (`document_id`, `chunk_id`)
- Safe error handling: never include raw document text in errors/logs
- Return shapes that are stable for downstream RAG usage

Important:
- `ingest_documents` is text-only ingestion.
- `ingest_markdown_files` reads markdown files from disk under an allowlisted root.
  If `LEGAL_MCP_INGEST_ROOT` is unset, the server defaults to `{worktree_root}/.agent/tmp`
  (because Zed runs the server with `cwd={worktree_root}`).
- `convert_files_to_markdown` and `ingest_pdf_files` only read from the allowlisted
  root and enforce suffix allowlists (e.g., `.pdf`). They write converted `.md` files
  back under the allowlisted root and return output paths + metadata (not raw text).

Caching:
- Tools are wrapped with `@cache.cached(...)` to return reference-based results.
- Results may be returned as a reference (`ref_id`) depending on size/configuration;
  clients can then poll/retrieve via `get_cached_result(ref_id=...)`.

Note on return types:
- Cached tools MUST return `dict[str, Any]` to match MCP schema expectations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from mcp_refcache import RefCache


# -----------------------------------------------------------------------------
# Pydantic models (public tool schemas)
# -----------------------------------------------------------------------------


class IngestChunkingOptions(BaseModel):
    """Chunking configuration for ingestion.

    Chunking is deterministic character-based chunking. Token-aware chunking can
    be added later without changing the tool interface (by interpreting these
    values as hints).

    Attributes:
        chunk_size_chars: Target chunk size in characters.
        chunk_overlap_chars: Overlap between chunks in characters.
        max_chunks_per_document: Optional safety cap for chunks per document.
    """

    chunk_size_chars: int = Field(default=1000, ge=200, le=10_000)
    chunk_overlap_chars: int = Field(default=150, ge=0, le=5_000)
    max_chunks_per_document: int | None = Field(default=None, ge=1, le=50_000)

    @field_validator("chunk_overlap_chars")
    @classmethod
    def validate_overlap_smaller_than_size(
        cls, chunk_overlap_chars: int, info: Any
    ) -> int:
        """Validate that overlap is strictly smaller than chunk size.

        Args:
            chunk_overlap_chars: Overlap between chunks in characters.
            info: Pydantic validator context containing already-validated fields.

        Returns:
            The validated overlap value.

        Raises:
            ValueError: If `chunk_overlap_chars` is greater than or equal to
                `chunk_size_chars`.
        """
        chunk_size_chars = info.data.get("chunk_size_chars", 1000)
        if chunk_overlap_chars >= chunk_size_chars:
            raise ValueError(
                "chunk_overlap_chars must be smaller than chunk_size_chars"
            )
        return chunk_overlap_chars


class IngestDocumentItem(BaseModel):
    """One document to ingest.

    Attributes:
        source_name: Human-friendly label (e.g., filename).
        text: Extracted plain text.
        document_id: Optional stable document identifier. If omitted, the server
            generates one deterministically from `source_name` + content hash.
        metadata: Optional shallow string metadata map (e.g., {"document_type": "complaint"}).
    """

    source_name: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=5_000_000)
    document_id: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, str] | None = Field(default=None)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_is_shallow_strings(
        cls, metadata: dict[str, str] | None
    ) -> dict[str, str] | None:
        """Validate that metadata is a shallow string->string mapping.

        This validator rejects empty keys and any non-string values.

        Args:
            metadata: Optional metadata mapping.

        Returns:
            The metadata mapping unchanged (or None).

        Raises:
            ValueError: If any key is empty/non-string or any value is non-string.
        """
        if metadata is None:
            return None
        for metadata_key, metadata_value in metadata.items():
            if not isinstance(metadata_key, str) or not metadata_key.strip():
                raise ValueError("metadata keys must be non-empty strings")
            if not isinstance(metadata_value, str):
                raise ValueError("metadata values must be strings")
        return metadata


class IngestDocumentsInput(BaseModel):
    """Input model for ingesting custom documents.

    Tenant isolation is mandatory.

    Attributes:
        tenant_id: Required tenant ID used for isolation.
        case_id: Optional case ID for scoping.
        documents: List of documents to ingest.
        tags: Optional tags applied to all documents.
        chunking: Optional chunking settings.
    """

    tenant_id: str = Field(min_length=1, max_length=200)
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    documents: list[IngestDocumentItem] = Field(min_length=1, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=50)
    chunking: IngestChunkingOptions | None = None


class SearchDocumentsInput(BaseModel):
    """Input model for semantic search over custom documents.

    Attributes:
        query: Search query.
        tenant_id: Required tenant ID used for isolation.
        case_id: Optional case ID to scope search.
        n_results: Maximum number of results to return.
        document_id: Optional document ID exact match filter.
        source_name: Optional source name exact match filter.
        tag: Optional tag filter (single tag token).
        excerpt_chars: Maximum characters for the returned excerpt.
    """

    query: str = Field(min_length=2, max_length=1000)
    tenant_id: str = Field(min_length=1, max_length=200)
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    n_results: int = Field(default=10, ge=1, le=50)
    document_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_name: str | None = Field(default=None, min_length=1, max_length=512)
    tag: str | None = Field(default=None, min_length=1, max_length=100)
    excerpt_chars: int = Field(default=500, ge=50, le=5_000)


class IngestMarkdownFilesInput(BaseModel):
    """Input model for ingesting markdown files from disk.

    Attributes:
        tenant_id: Required tenant identifier used for isolation.
        case_id: Optional case identifier.
        paths: Relative file paths under the allowlisted ingestion root.
        tags: Optional tags applied to all ingested files.
        chunking: Optional chunking configuration (same shape as `ingest_documents`).
        max_chars_per_file: Optional safety cap to avoid ingesting extremely large files.
    """

    tenant_id: str = Field(min_length=1, max_length=200)
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=50)
    chunking: IngestChunkingOptions | None = None
    max_chars_per_file: int | None = Field(default=2_000_000, ge=1, le=5_000_000)


class ConvertFilesToMarkdownInput(BaseModel):
    """Input model for converting files from disk to Markdown/text.

    Primary behavior:
    - Read allowlisted input files under the ingestion root.
    - Convert to Markdown/text.
    - Write a `.md` file under the same allowlisted root.

    Attributes:
        paths: Relative file paths under the allowlisted ingestion root.
        max_chars_per_file: Optional safety cap for converted text size.
        overwrite: Whether to overwrite an existing output `.md` file.
    """

    paths: list[str] = Field(min_length=1, max_length=200)
    max_chars_per_file: int | None = Field(default=5_000_000, ge=1, le=5_000_000)
    overwrite: bool = Field(default=True)


class IngestPdfFilesInput(BaseModel):
    """Input model for ingesting PDF files from disk.

    The workflow is:
    allowlisted file read (path validation) → PDF conversion → ingestion into
    the custom documents vector store.

    Attributes:
        tenant_id: Required tenant identifier used for isolation.
        case_id: Optional case identifier.
        paths: Relative PDF file paths under the allowlisted ingestion root.
        tags: Optional tags applied to all ingested PDFs.
        chunking: Optional chunking configuration (same shape as `ingest_documents`).
        max_chars_per_file: Optional safety cap for converted text size.
        replace: If True, best-effort delete of existing chunks for each document
            (scoped by tenant and optional case) before upserting new vectors.
            Use this for re-ingestion without duplication.
    """

    tenant_id: str = Field(min_length=1, max_length=200)
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=50)
    chunking: IngestChunkingOptions | None = None
    max_chars_per_file: int | None = Field(default=5_000_000, ge=1, le=5_000_000)
    replace: bool = Field(default=False)


# -----------------------------------------------------------------------------
# Tool factories
# -----------------------------------------------------------------------------


def create_ingest_documents(cache: RefCache) -> Any:
    """Create an `ingest_documents` MCP tool bound to the given RefCache.

    This tool ingests custom text documents into a dedicated custom-docs
    collection.

    Args:
        cache: RefCache instance for caching/async job support.

    Returns:
        A FastMCP-compatible tool function.

    Example:
        >>> from mcp_refcache import RefCache
        >>> cache = RefCache(name="legal-mcp")
        >>> ingest_documents = create_ingest_documents(cache)
        >>> # in server.py: mcp.tool(ingest_documents)

    Notes:
        This tool is designed to be used with cache polling.
        The tool returns a dict; the cache layer may wrap it into a reference
        response (with a `ref_id`) depending on size and server configuration.

        If you need true background execution with immediate return for large
        ingestions, implement an explicit submit/status API or ensure the cache
        layer supports a non-blocking job mode.
    """

    @cache.cached(namespace="custom_documents")
    async def ingest_documents(
        tenant_id: str,
        documents: list[dict[str, Any]],
        case_id: str | None = None,
        tags: list[str] | None = None,
        chunking: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest custom documents into the custom documents vector store.

        Args:
            tenant_id: Required tenant identifier used for isolation.
            documents: List of documents where each item includes:
                - `source_name` (str)
                - `text` (str)
                Optional:
                - `document_id` (str)
                - `metadata` (dict[str, str])
            case_id: Optional case identifier.
            tags: Optional tags applied to all documents.
            chunking: Optional chunking configuration:
                - `chunk_size_chars` (int)
                - `chunk_overlap_chars` (int)
                - `max_chunks_per_document` (int | None)

        Returns:
            Structured ingestion result with status, totals, and per-document summaries.

        Raises:
            None. Exceptions are converted to structured error dicts.

        Example:
            >>> # Minimal ingestion payload
            >>> ingest_documents(
            ...     tenant_id="t_123",
            ...     case_id="c_001",
            ...     documents=[{"source_name": "notes.txt", "text": "Tenant reports mold."}],
            ... )
        """
        # Validate input first (tool schema + explicit coercion)
        validated = IngestDocumentsInput(
            tenant_id=tenant_id,
            case_id=case_id,
            documents=documents,
            tags=tags,
            chunking=chunking,
        )

        # Import inside tool to keep server startup light
        from app.custom_documents.pipeline import ingest_custom_documents

        try:
            result = ingest_custom_documents(
                tenant_id=validated.tenant_id,
                case_id=validated.case_id,
                documents=[
                    document_item.model_dump() for document_item in validated.documents
                ],
                tags=validated.tags,
                chunking=validated.chunking.model_dump()
                if validated.chunking is not None
                else None,
            )
            return result
        except Exception as error:
            # Never include raw document text; this exception should not contain it,
            # but we still truncate and avoid adding any payload.
            return {
                "status": "failed",
                "error": "Custom document ingestion failed",
                "message": str(error)[:500],
                "tenant_id": validated.tenant_id,
                "case_id": validated.case_id,
            }

    return ingest_documents


def create_search_documents(cache: RefCache) -> Any:
    """Create a `search_documents` MCP tool bound to the given RefCache.

    Args:
        cache: RefCache instance for caching results.

    Returns:
        A FastMCP-compatible tool function.
    """

    @cache.cached(namespace="custom_documents")
    async def search_documents(
        query: str,
        tenant_id: str,
        case_id: str | None = None,
        n_results: int = 10,
        document_id: str | None = None,
        source_name: str | None = None,
        tag: str | None = None,
        excerpt_chars: int = 500,
    ) -> dict[str, Any]:
        """Semantic search over custom user documents with filters.

        Mandatory tenant isolation is enforced by including `tenant_id` in the
        underlying vector store filter.

        Args:
            query: Query string.
            tenant_id: Required tenant identifier used for isolation.
            case_id: Optional case identifier to scope search.
            n_results: Maximum number of hits (1-50).
            document_id: Optional filter for a single document ID (exact match).
            source_name: Optional filter for a specific source name (exact match).
            tag: Optional single tag filter.
            excerpt_chars: Maximum excerpt length returned per result.

        Returns:
            Dict with query, filters, count, and results list. Each result includes:
            - `chunk_id`
            - `document_id`
            - `similarity`
            - `excerpt`
            - selected metadata fields (tenant_id, case_id, source_name, ingested_at, tags)

        Example:
            >>> search_documents(
            ...     query="mold in bathroom",
            ...     tenant_id="t_123",
            ...     case_id="c_001",
            ...     n_results=5,
            ... )
        """
        validated = SearchDocumentsInput(
            query=query,
            tenant_id=tenant_id,
            case_id=case_id,
            n_results=n_results,
            document_id=document_id,
            source_name=source_name,
            tag=tag,
            excerpt_chars=excerpt_chars,
        )

        from app.custom_documents.embeddings import CustomDocumentEmbeddingStore

        try:
            store = CustomDocumentEmbeddingStore()
            where = CustomDocumentEmbeddingStore.build_tenant_where(
                validated.tenant_id,
                case_id=validated.case_id,
                document_id=validated.document_id,
                source_name=validated.source_name,
                tag=validated.tag,
            )
            hits = store.search(
                validated.query,
                n_results=validated.n_results,
                where=where,
            )

            results: list[dict[str, Any]] = []
            for hit in hits:
                full_content = hit.content or ""
                excerpt = (
                    (full_content[: validated.excerpt_chars] + "...")
                    if len(full_content) > validated.excerpt_chars
                    else full_content
                )

                metadata = hit.metadata or {}
                results.append(
                    {
                        "chunk_id": hit.chunk_id,
                        "document_id": metadata.get("document_id"),
                        "similarity": round(hit.similarity, 3),
                        "excerpt": excerpt,
                        "metadata": {
                            "tenant_id": metadata.get("tenant_id"),
                            "case_id": metadata.get("case_id"),
                            "source_name": metadata.get("source_name"),
                            "ingested_at": metadata.get("ingested_at"),
                            "tags_csv": metadata.get("tags_csv"),
                            "tag": metadata.get("tag"),
                        },
                    }
                )

            return {
                "query": validated.query,
                "filters": {
                    "tenant_id": validated.tenant_id,
                    "case_id": validated.case_id,
                    "document_id": validated.document_id,
                    "source_name": validated.source_name,
                    "tag": validated.tag,
                },
                "count": len(results),
                "results": results,
            }
        except Exception as error:
            return {
                "error": "Search failed",
                "message": str(error)[:500],
                "query": validated.query,
                "filters": {
                    "tenant_id": validated.tenant_id,
                    "case_id": validated.case_id,
                    "document_id": validated.document_id,
                    "source_name": validated.source_name,
                    "tag": validated.tag,
                },
            }

    return search_documents


def create_ingest_markdown_files(cache: RefCache) -> Any:
    """Create an `ingest_markdown_files` MCP tool bound to the given RefCache.

    This tool reads Markdown files from disk under an allowlisted root directory
    (configured via `LEGAL_MCP_INGEST_ROOT`) and ingests them into the custom
    documents vector store.

    Args:
        cache: RefCache instance for caching results.

    Returns:
        A FastMCP-compatible tool function.
    """

    @cache.cached(namespace="custom_documents")
    async def ingest_markdown_files(
        tenant_id: str,
        paths: list[str],
        case_id: str | None = None,
        tags: list[str] | None = None,
        chunking: dict[str, Any] | None = None,
        max_chars_per_file: int | None = 2_000_000,
    ) -> dict[str, Any]:
        """Ingest Markdown files from disk under an allowlisted root directory.

        This tool enforces that files are read only from under the configured
        allowlisted ingestion root (`LEGAL_MCP_INGEST_ROOT`). It rejects absolute
        paths and path traversal attempts.

        Args:
            tenant_id: Required tenant identifier used for isolation.
            paths: Relative file paths under the allowlisted ingestion root.
            case_id: Optional case identifier.
            tags: Optional tags applied to all files.
            chunking: Optional chunking configuration (same shape as ingest_documents).
            max_chars_per_file: Optional safety cap for file size (characters).

        Returns:
            Structured result with totals and per-file summaries.

        Example:
            >>> ingest_markdown_files(
            ...     tenant_id="t_123",
            ...     case_id="c_001",
            ...     paths=["converted/990-17_k25.md"],
            ...     tags=["mietrecht"],
            ... )
        """
        validated = IngestMarkdownFilesInput(
            tenant_id=tenant_id,
            case_id=case_id,
            paths=paths,
            tags=tags,
            chunking=chunking,
            max_chars_per_file=max_chars_per_file,
        )

        from pathlib import Path

        from app.config import get_settings
        from app.custom_documents.file_ingestion import (
            FileIngestionError,
            read_markdown_file_for_ingestion,
            require_allowlisted_root,
        )
        from app.custom_documents.pipeline import ingest_custom_documents

        settings = get_settings()

        try:
            root = require_allowlisted_root(
                settings.ingest_root_path,
                default_root=Path.cwd() / ".agent" / "tmp",
            )
        except FileIngestionError as error:
            return {
                "status": "failed",
                "error": "File ingestion is disabled or misconfigured",
                "message": str(error)[:500],
                "tenant_id": validated.tenant_id,
                "case_id": validated.case_id,
            }

        totals = {
            "files_received": len(validated.paths),
            "files_ingested": 0,
            "chunks_created": 0,
            "chunks_added": 0,
            "errors": 0,
        }
        file_summaries: list[dict[str, Any]] = []

        for relative_path in validated.paths:
            try:
                read_result = read_markdown_file_for_ingestion(
                    root,
                    relative_path,
                    max_chars=validated.max_chars_per_file,
                )
            except FileIngestionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": None,
                        "document_id": None,
                        "chunks_created": 0,
                        "chunks_added": 0,
                        "errors": [str(error)[:500]],
                    }
                )
                continue

            ingestion_result = ingest_custom_documents(
                tenant_id=validated.tenant_id,
                case_id=validated.case_id,
                documents=[
                    {
                        "source_name": read_result.source_name,
                        "text": read_result.text,
                        "metadata": {
                            "relative_path": relative_path,
                            "size_bytes": str(read_result.size_bytes),
                            "truncated": str(read_result.truncated).lower(),
                        },
                    }
                ],
                tags=validated.tags,
                chunking=validated.chunking.model_dump()
                if validated.chunking is not None
                else None,
            )

            if ingestion_result.get("status") != "complete":
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": read_result.source_name,
                        "document_id": None,
                        "chunks_created": 0,
                        "chunks_added": 0,
                        "errors": [
                            str(ingestion_result.get("message", "Ingestion failed"))[
                                :500
                            ]
                        ],
                    }
                )
                continue

            document_summaries = ingestion_result.get("documents", [])
            document_id = None
            chunks_created = 0
            chunks_added = 0
            if document_summaries:
                document_id = document_summaries[0].get("document_id")
                chunks_created = int(
                    document_summaries[0].get("chunks_created", 0) or 0
                )
                chunks_added = int(document_summaries[0].get("chunks_added", 0) or 0)

            totals["files_ingested"] += 1
            totals["chunks_created"] += chunks_created
            totals["chunks_added"] += chunks_added

            file_summaries.append(
                {
                    "path": relative_path,
                    "source_name": read_result.source_name,
                    "document_id": document_id,
                    "chunks_created": chunks_created,
                    "chunks_added": chunks_added,
                    "errors": [],
                }
            )

        status = "complete" if totals["files_ingested"] > 0 else "failed"
        return {
            "status": status,
            "tenant_id": validated.tenant_id,
            "case_id": validated.case_id,
            "totals": totals,
            "files": file_summaries,
        }

    return ingest_markdown_files


def create_convert_files_to_markdown(cache: RefCache) -> Any:
    """Create a `convert_files_to_markdown` MCP tool bound to the given RefCache.

    This tool reads files from the allowlisted ingestion root and converts them
    to Markdown/text using the server-side converter (MarkItDown).

    Security:
    - Only reads files under the allowlisted root.
    - Rejects absolute paths and traversal segments.
    - Enforces a file suffix allowlist.

    Args:
        cache: RefCache instance for caching results.

    Returns:
        A FastMCP-compatible tool function.
    """

    @cache.cached(namespace="custom_documents")
    async def convert_files_to_markdown(
        paths: list[str],
        max_chars_per_file: int | None = 5_000_000,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Convert files to Markdown/text from disk under the allowlisted root.

        Args:
            paths: Relative file paths under the allowlisted ingestion root.
            max_chars_per_file: Optional maximum characters for converted text.
            overwrite: Whether to overwrite an existing output `.md` file.

        Returns:
            Structured result with per-file conversion summaries.
        """
        validated = ConvertFilesToMarkdownInput(
            paths=paths,
            max_chars_per_file=max_chars_per_file,
            overwrite=overwrite,
        )

        from pathlib import Path

        from app.config import get_settings
        from app.custom_documents.conversion.markitdown_converter import (
            FileConversionError,
            convert_allowlisted_file_to_markdown,
        )
        from app.custom_documents.file_ingestion import (
            FileIngestionError,
            require_allowlisted_root,
            resolve_allowlisted_file,
            write_text_utf8_under_allowlisted_root,
        )

        settings = get_settings()

        try:
            root = require_allowlisted_root(
                settings.ingest_root_path,
                default_root=Path.cwd() / ".agent" / "tmp",
            )
        except FileIngestionError as error:
            return {
                "status": "failed",
                "error": "File conversion is disabled or misconfigured",
                "message": str(error)[:500],
                "totals": {
                    "files_received": len(validated.paths),
                    "files_converted": 0,
                    "errors": 0,
                },
                "files": [],
            }

        totals = {
            "files_received": len(validated.paths),
            "files_converted": 0,
            "errors": 0,
        }
        file_summaries: list[dict[str, Any]] = []

        allowed_suffixes = {
            ".pdf",
            ".txt",
            ".md",
            ".markdown",
            ".html",
            ".htm",
            ".docx",
        }

        for relative_path in validated.paths:
            try:
                allowlisted_path = resolve_allowlisted_file(
                    root,
                    relative_path,
                    allowed_suffixes=allowed_suffixes,
                )
            except FileIngestionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": None,
                        "file_suffix": None,
                        "status": "failed",
                        "error": str(error)[:500],
                    }
                )
                continue

            try:
                conversion_result = convert_allowlisted_file_to_markdown(
                    allowlisted_path,
                    max_chars=validated.max_chars_per_file,
                )
            except FileConversionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": allowlisted_path.name,
                        "file_suffix": allowlisted_path.suffix.lower(),
                        "status": "failed",
                        "error": str(error)[:500],
                    }
                )
                continue

            output_relative_path = f"{relative_path}.md"
            try:
                write_result = write_text_utf8_under_allowlisted_root(
                    root,
                    output_relative_path,
                    text=conversion_result.markdown,
                    allowed_suffixes={".md"},
                    overwrite=validated.overwrite,
                )
            except FileIngestionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "output_path": output_relative_path,
                        "source_name": conversion_result.source_name,
                        "file_suffix": conversion_result.file_suffix,
                        "status": "failed",
                        "error": str(error)[:500],
                    }
                )
                continue

            totals["files_converted"] += 1
            file_summaries.append(
                {
                    "path": relative_path,
                    "output_path": write_result.relative_path,
                    "source_name": conversion_result.source_name,
                    "file_suffix": conversion_result.file_suffix,
                    "status": "complete",
                    "metadata": {
                        **conversion_result.metadata,
                        "output_size_bytes": str(write_result.size_bytes),
                        "overwritten": str(write_result.overwritten).lower(),
                    },
                }
            )

        status = "complete" if totals["files_converted"] > 0 else "failed"
        return {
            "status": status,
            "totals": totals,
            "files": file_summaries,
        }

    return convert_files_to_markdown


def create_ingest_pdf_files(cache: RefCache) -> Any:
    """Create an `ingest_pdf_files` MCP tool bound to the given RefCache.

    This tool reads PDF files from disk under the allowlisted ingestion root,
    converts them to Markdown/text, then ingests them into the custom documents
    vector store.

    Args:
        cache: RefCache instance for caching results.

    Returns:
        A FastMCP-compatible tool function.
    """

    @cache.cached(namespace="custom_documents")
    async def ingest_pdf_files(
        tenant_id: str,
        paths: list[str],
        case_id: str | None = None,
        tags: list[str] | None = None,
        chunking: dict[str, Any] | None = None,
        max_chars_per_file: int | None = 5_000_000,
        replace: bool = True,
    ) -> dict[str, Any]:
        """Ingest PDF files from disk under an allowlisted root directory.

        Args:
            tenant_id: Required tenant identifier used for isolation.
            paths: Relative PDF file paths under the allowlisted ingestion root.
            case_id: Optional case identifier.
            tags: Optional tags applied to all PDFs.
            chunking: Optional chunking configuration (same shape as ingest_documents).
            max_chars_per_file: Optional safety cap for converted text size.
            replace: If True, best-effort delete of existing chunks for each
                document (scoped by tenant and optional case) before upserting
                new vectors. Use this for re-ingestion without duplication.

        Returns:
            Structured result with totals and per-file summaries.
        """
        validated = IngestPdfFilesInput(
            tenant_id=tenant_id,
            case_id=case_id,
            paths=paths,
            tags=tags,
            chunking=chunking,
            max_chars_per_file=max_chars_per_file,
            replace=replace,
        )

        from pathlib import Path

        from app.config import get_settings
        from app.custom_documents.conversion.markitdown_converter import (
            FileConversionError,
            convert_pdf_to_markdown,
        )
        from app.custom_documents.file_ingestion import (
            FileIngestionError,
            require_allowlisted_root,
            resolve_allowlisted_file,
            write_text_utf8_under_allowlisted_root,
        )
        from app.custom_documents.pipeline import ingest_custom_documents

        settings = get_settings()

        try:
            root = require_allowlisted_root(
                settings.ingest_root_path,
                default_root=Path.cwd() / ".agent" / "tmp",
            )
        except FileIngestionError as error:
            return {
                "status": "failed",
                "error": "File ingestion is disabled or misconfigured",
                "message": str(error)[:500],
                "tenant_id": validated.tenant_id,
                "case_id": validated.case_id,
            }

        totals = {
            "files_received": len(validated.paths),
            "files_ingested": 0,
            "chunks_created": 0,
            "chunks_added": 0,
            "errors": 0,
        }
        file_summaries: list[dict[str, Any]] = []

        for relative_path in validated.paths:
            try:
                pdf_path = resolve_allowlisted_file(
                    root,
                    relative_path,
                    allowed_suffixes={".pdf"},
                )
            except FileIngestionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": None,
                        "document_id": None,
                        "chunks_created": 0,
                        "chunks_added": 0,
                        "errors": [str(error)[:500]],
                    }
                )
                continue

            try:
                conversion_result = convert_pdf_to_markdown(
                    pdf_path,
                    max_chars=validated.max_chars_per_file,
                )
            except FileConversionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": pdf_path.name,
                        "document_id": None,
                        "chunks_created": 0,
                        "chunks_added": 0,
                        "errors": [str(error)[:500]],
                    }
                )
                continue

            output_relative_path = f"{relative_path}.md"
            try:
                write_result = write_text_utf8_under_allowlisted_root(
                    root,
                    output_relative_path,
                    text=conversion_result.markdown,
                    allowed_suffixes={".md"},
                    overwrite=True,
                )
            except FileIngestionError as error:
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "output_path": output_relative_path,
                        "source_name": conversion_result.source_name,
                        "document_id": None,
                        "chunks_created": 0,
                        "chunks_added": 0,
                        "errors": [str(error)[:500]],
                    }
                )
                continue

            ingestion_result = ingest_custom_documents(
                tenant_id=validated.tenant_id,
                case_id=validated.case_id,
                documents=[
                    {
                        "source_name": conversion_result.source_name,
                        "text": conversion_result.markdown,
                        "metadata": {
                            "relative_path": relative_path,
                            "output_relative_path": write_result.relative_path,
                            "output_size_bytes": str(write_result.size_bytes),
                            "output_overwritten": str(write_result.overwritten).lower(),
                            **conversion_result.metadata,
                        },
                    }
                ],
                tags=validated.tags,
                chunking=validated.chunking.model_dump()
                if validated.chunking is not None
                else None,
                replace=validated.replace,
            )

            if ingestion_result.get("status") != "complete":
                totals["errors"] += 1
                file_summaries.append(
                    {
                        "path": relative_path,
                        "source_name": conversion_result.source_name,
                        "document_id": None,
                        "chunks_created": 0,
                        "chunks_added": 0,
                        "errors": [
                            str(ingestion_result.get("message", "Ingestion failed"))[
                                :500
                            ]
                        ],
                    }
                )
                continue

            document_summaries = ingestion_result.get("documents", [])
            document_id = None
            chunks_created = 0
            chunks_added = 0
            if document_summaries:
                document_id = document_summaries[0].get("document_id")
                chunks_created = int(
                    document_summaries[0].get("chunks_created", 0) or 0
                )
                chunks_added = int(document_summaries[0].get("chunks_added", 0) or 0)

            totals["files_ingested"] += 1
            totals["chunks_created"] += chunks_created
            totals["chunks_added"] += chunks_added

            file_summaries.append(
                {
                    "path": relative_path,
                    "output_path": f"{relative_path}.md",
                    "source_name": conversion_result.source_name,
                    "document_id": document_id,
                    "chunks_created": chunks_created,
                    "chunks_added": chunks_added,
                    "errors": [],
                }
            )

        status = "complete" if totals["files_ingested"] > 0 else "failed"
        return {
            "status": status,
            "tenant_id": validated.tenant_id,
            "case_id": validated.case_id,
            "totals": totals,
            "files": file_summaries,
        }

    return ingest_pdf_files


__all__ = [
    "ConvertFilesToMarkdownInput",
    "IngestChunkingOptions",
    "IngestDocumentItem",
    "IngestDocumentsInput",
    "IngestMarkdownFilesInput",
    "IngestPdfFilesInput",
    "SearchDocumentsInput",
    "create_convert_files_to_markdown",
    "create_ingest_documents",
    "create_ingest_markdown_files",
    "create_ingest_pdf_files",
    "create_search_documents",
]
