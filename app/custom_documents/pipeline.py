r"""Custom document ingestion pipeline (plain text).

This module implements a minimal ingestion pipeline for **custom user documents**
(e.g., case files) suitable for use in an MCP server.

Design goals:
- Deterministic chunking (stable chunk boundaries and stable chunk IDs)
- Safe metadata handling (no sensitive content in errors)
- Multi-tenant isolation support: `tenant_id` is required and stored per chunk
- Optional case scoping: `case_id` stored per chunk when present
- Persist to ChromaDB via `CustomDocumentEmbeddingStore`
- Section-aware chunking: when input text contains Markdown headings, chunking is
  performed per section and each chunk is annotated with `section_*` metadata.

This module is intentionally **text-only**. PDF parsing/extraction is expected to
be performed outside of this ingestion function for now (e.g., by a future tool
or client-side extraction), and then the extracted text is ingested here.

Public API:
- `ingest_custom_documents(...)` - ingest one or more documents with chunking
- `chunk_text_deterministic(...)` - deterministic character-based chunking helper

Safety:
- Do not log or return raw document text in error messages.
- Only include bounded snippets/excerpts in results when needed (currently none).

Example:
    >>> result = ingest_custom_documents(
    ...     tenant_id="t_123",
    ...     case_id="c_456",
    ...     documents=[
    ...         {"source_name": "case_notes.txt", "text": "# Intro\\nSome text..."},
    ...     ],
    ... )
    >>> result["status"] in {"complete", "failed"}
    True
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.custom_documents.embeddings import (
    CustomDocumentEmbeddingStore,
    TextChunk,
)
from app.custom_documents.sectioning.markdown_sections import extract_markdown_sections


def _hash_text_to_document_id(text: str, *, source_name: str) -> str:
    """Compute a stable document ID based on source name and content hash.

    This is used only when the caller does not provide a `document_id`.
    """
    sha256 = hashlib.sha256()
    sha256.update(source_name.encode("utf-8", errors="ignore"))
    sha256.update(b"\n")
    sha256.update(text.encode("utf-8", errors="ignore"))
    return f"doc_{sha256.hexdigest()[:16]}"


def _iso_timestamp_seconds() -> str:
    """Return a coarse ISO-like timestamp string without requiring datetime deps.

    Note:
        This is intended for metadata / debugging but should not be relied on for
        strict time semantics. We avoid timezone confusion by using epoch seconds.
    """
    return str(int(time.time()))


def chunk_text_deterministic(
    text: str,
    *,
    chunk_size_chars: int = 1000,
    chunk_overlap_chars: int = 150,
    max_chunks_per_document: int | None = None,
) -> list[str]:
    """Split text into deterministic overlapping character chunks.

    This chunker:
    - uses fixed character windows
    - is deterministic for a given input text and parameters
    - can cap the number of chunks for safety

    Args:
        text: Input text to chunk.
        chunk_size_chars: Target size of each chunk in characters.
        chunk_overlap_chars: Overlap between consecutive chunks.
        max_chunks_per_document: Optional hard cap to prevent runaway chunking.

    Returns:
        List of chunk strings.

    Raises:
        ValueError: If parameters are invalid.
    """
    if chunk_size_chars <= 0:
        raise ValueError("chunk_size_chars must be positive")
    if chunk_overlap_chars < 0:
        raise ValueError("chunk_overlap_chars must be non-negative")
    if chunk_overlap_chars >= chunk_size_chars:
        raise ValueError("chunk_overlap_chars must be smaller than chunk_size_chars")

    normalized_text = text.strip()
    if not normalized_text:
        return []

    step_size = chunk_size_chars - chunk_overlap_chars
    chunks: list[str] = []

    start_index = 0
    while start_index < len(normalized_text):
        end_index = min(start_index + chunk_size_chars, len(normalized_text))
        chunk = normalized_text[start_index:end_index].strip()
        if chunk:
            chunks.append(chunk)

        if end_index >= len(normalized_text):
            break

        start_index += step_size

        if (
            max_chunks_per_document is not None
            and len(chunks) >= max_chunks_per_document
        ):
            break

    return chunks


def _chunk_document_with_sections(
    document_text: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
    max_chunks_per_document: int | None,
) -> list[tuple[str, dict[str, Any]]]:
    """Chunk a document text using conservative Markdown section boundaries.

    Sectioning is conservative and based only on Markdown ATX headings (`#`...`######`).
    If no headings exist, the whole document is treated as one section titled
    `"Document"`.

    Each returned item is a tuple of `(chunk_text, section_metadata)` where
    `section_metadata` contains stable `section_*` fields.

    Args:
        document_text: Full document text.
        chunk_size_chars: Target chunk size in characters.
        chunk_overlap_chars: Overlap in characters.
        max_chunks_per_document: Optional cap for total chunks across the whole document.

    Returns:
        List of `(chunk_text, section_metadata)` tuples.

    Raises:
        ValueError: If chunking parameters are invalid.
    """
    sections = extract_markdown_sections(document_text)
    chunk_pairs: list[tuple[str, dict[str, Any]]] = []
    chunks_emitted = 0

    for section in sections:
        section_text = section.slice_text(document_text).strip()
        if not section_text:
            continue

        section_chunks = chunk_text_deterministic(
            section_text,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
            max_chunks_per_document=None,
        )

        section_metadata: dict[str, Any] = {
            "section_index": section.section_index,
            "section_title": section.title,
            "section_level": section.level,
            "section_path": section.path,
            "section_start_char": section.start_char,
            "section_end_char": section.end_char,
        }

        for section_chunk in section_chunks:
            if not section_chunk:
                continue

            chunk_pairs.append((section_chunk, section_metadata))
            chunks_emitted += 1

            if (
                max_chunks_per_document is not None
                and chunks_emitted >= max_chunks_per_document
            ):
                return chunk_pairs

    return chunk_pairs


class IngestChunkingOptions(BaseModel):
    """Configuration options for deterministic chunking."""

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
    """One custom document to ingest."""

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
        # Pydantic enforces value types, but we also enforce key constraints.
        for key, value in metadata.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("metadata keys must be non-empty strings")
            if not isinstance(value, str):
                raise ValueError("metadata values must be strings")
        return metadata


class IngestDocumentsInput(BaseModel):
    """Validated input for ingesting custom documents."""

    tenant_id: str = Field(min_length=1, max_length=200)
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    documents: list[IngestDocumentItem] = Field(min_length=1, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=50)
    chunking: IngestChunkingOptions | None = None


@dataclass(frozen=True)
class DocumentIngestSummary:
    """Per-document summary for ingestion results."""

    document_id: str
    source_name: str
    chunks_created: int
    chunks_added: int
    errors: list[str]


def _safe_error(message: str) -> str:
    """Return an error string that does not include sensitive content.

    This is a central place to ensure we never accidentally attach document text.
    """
    return message.strip()[:500]


def ingest_custom_documents(
    *,
    tenant_id: str,
    documents: list[dict[str, Any]] | list[IngestDocumentItem],
    case_id: str | None = None,
    tags: list[str] | None = None,
    chunking: dict[str, Any] | IngestChunkingOptions | None = None,
    store: CustomDocumentEmbeddingStore | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Ingest custom documents into the custom document vector store.

    Args:
        tenant_id: Required tenant ID; used for isolation.
        documents: A list of documents. Each must contain:
            - `source_name`: filename/label
            - `text`: extracted plain text
            Optional:
            - `document_id`: stable ID; if not provided, generated deterministically
            - `metadata`: shallow string map
        case_id: Optional case ID for scoping.
        tags: Optional list of tags applied to all documents.
        chunking: Optional chunking options.
        store: Optional injected store (useful for tests).
        replace: If True, best-effort delete of existing chunks for each document
            (scoped by tenant and optional case) before upserting new vectors.

    Returns:
        A dict with status, counts, and per-document summaries. This function
        returns data safe to expose in tool responses (no raw text).

    Notes:
        This function is synchronous. If used in MCP, wrap it in async job / cache
        semantics so it does not block long-running requests.
    """
    validated_chunking: IngestChunkingOptions
    if chunking is None:
        validated_chunking = IngestChunkingOptions()
    elif isinstance(chunking, IngestChunkingOptions):
        validated_chunking = chunking
    else:
        validated_chunking = IngestChunkingOptions(**chunking)

    validated_input = IngestDocumentsInput(
        tenant_id=tenant_id,
        case_id=case_id,
        documents=documents,  # Pydantic will coerce dicts into models
        tags=tags,
        chunking=validated_chunking,
    )

    embedding_store = store or CustomDocumentEmbeddingStore()
    ingested_at = _iso_timestamp_seconds()

    normalized_tags_csv = CustomDocumentEmbeddingStore.normalize_tags_csv(
        validated_input.tags
    )
    # For v1 tag equality filtering, we store a single `tag` only when exactly one tag exists.
    single_tag: str | None = None
    if validated_input.tags:
        normalized_single_tokens = sorted(
            {tag.strip().lower() for tag in validated_input.tags if tag.strip()}
        )
        if len(normalized_single_tokens) == 1:
            single_tag = normalized_single_tokens[0]

    totals = {
        "documents_received": len(validated_input.documents),
        "documents_ingested": 0,
        "chunks_created": 0,
        "chunks_added": 0,
        "errors": 0,
    }

    per_document: list[DocumentIngestSummary] = []
    all_chunks: list[TextChunk] = []

    for document in validated_input.documents:
        document_errors: list[str] = []

        document_text = document.text
        if not document_text or not document_text.strip():
            per_document.append(
                DocumentIngestSummary(
                    document_id=document.document_id or "unknown",
                    source_name=document.source_name,
                    chunks_created=0,
                    chunks_added=0,
                    errors=[_safe_error("Empty document text")],
                )
            )
            totals["errors"] += 1
            continue

        document_id = document.document_id or _hash_text_to_document_id(
            document_text, source_name=document.source_name
        )

        try:
            chunk_pairs = _chunk_document_with_sections(
                document_text,
                chunk_size_chars=validated_chunking.chunk_size_chars,
                chunk_overlap_chars=validated_chunking.chunk_overlap_chars,
                max_chunks_per_document=validated_chunking.max_chunks_per_document,
            )
        except Exception as error:
            per_document.append(
                DocumentIngestSummary(
                    document_id=document_id,
                    source_name=document.source_name,
                    chunks_created=0,
                    chunks_added=0,
                    errors=[_safe_error(f"Chunking failed: {error}")],
                )
            )
            totals["errors"] += 1
            continue

        totals["chunks_created"] += len(chunk_pairs)

        metadata_base: dict[str, Any] = {
            "tenant_id": validated_input.tenant_id,
            "case_id": validated_input.case_id,
            "document_id": document_id,
            "source_name": document.source_name,
            "ingested_at": ingested_at,
            "tags_csv": normalized_tags_csv,
            "tag": single_tag,
        }
        if document.metadata:
            # Merge shallow string metadata
            metadata_base.update(document.metadata)

        for chunk_index, (chunk_text, section_metadata) in enumerate(chunk_pairs):
            chunk_id = f"{document_id}:{chunk_index}"
            all_chunks.append(
                TextChunk(
                    chunk_id=chunk_id,
                    text=chunk_text,
                    metadata={
                        **metadata_base,
                        "chunk_id": chunk_id,
                        "chunk_index": chunk_index,
                        **section_metadata,
                    },
                )
            )

        per_document.append(
            DocumentIngestSummary(
                document_id=document_id,
                source_name=document.source_name,
                chunks_created=len(chunk_pairs),
                chunks_added=0,  # filled after store upsert
                errors=document_errors,
            )
        )

    if not all_chunks:
        return {
            "status": "failed",
            "tenant_id": validated_input.tenant_id,
            "case_id": validated_input.case_id,
            "message": "No chunks to ingest",
            "totals": totals,
            "documents": [
                {
                    "document_id": summary.document_id,
                    "source_name": summary.source_name,
                    "chunks_created": summary.chunks_created,
                    "chunks_added": summary.chunks_added,
                    "errors": summary.errors,
                }
                for summary in per_document
            ],
        }

    try:
        add_result = embedding_store.add_text_chunks(all_chunks, replace=replace)
    except Exception as error:
        # Do not include sensitive text in error output.
        return {
            "status": "failed",
            "tenant_id": validated_input.tenant_id,
            "case_id": validated_input.case_id,
            "message": _safe_error(f"Embedding store write failed: {error}"),
            "totals": {**totals, "errors": totals["errors"] + 1},
            "documents": [
                {
                    "document_id": summary.document_id,
                    "source_name": summary.source_name,
                    "chunks_created": summary.chunks_created,
                    "chunks_added": summary.chunks_added,
                    "errors": summary.errors,
                }
                for summary in per_document
            ],
        }

    totals["chunks_added"] = add_result.vectors_added

    # Update per-document `chunks_added` deterministically by recomputing counts per doc id.
    chunks_added_by_document_id: dict[str, int] = {}
    for chunk_id in add_result.chunk_ids:
        document_id = chunk_id.split(":", 1)[0]
        chunks_added_by_document_id[document_id] = (
            chunks_added_by_document_id.get(document_id, 0) + 1
        )

    updated_documents: list[dict[str, Any]] = []
    for summary in per_document:
        added_for_document = chunks_added_by_document_id.get(summary.document_id, 0)
        updated_documents.append(
            {
                "document_id": summary.document_id,
                "source_name": summary.source_name,
                "chunks_created": summary.chunks_created,
                "chunks_added": added_for_document,
                "errors": summary.errors,
            }
        )
        if added_for_document > 0:
            totals["documents_ingested"] += 1

    totals["documents_received"] = len(per_document)

    return {
        "status": "complete",
        "tenant_id": validated_input.tenant_id,
        "case_id": validated_input.case_id,
        "totals": totals,
        "documents": updated_documents,
        "timing": {
            "ingested_at": ingested_at,
        },
    }
