"""Unit tests for custom document MCP tool factories.

These tests focus on behavior:
- Pydantic validation (including chunking + metadata validators)
- Excerpt shaping in search results
- Error shaping for dependency failures within tool bodies

Important: tool factories wrap functions with `@cache.cached(...)`, so the
returned callable is frequently a wrapper object rather than the raw coroutine.
To match existing patterns in `tests/test_server.py` and avoid coupling to cache
internals, these tests call the underlying function via `.fn` when present.

Note on validation:
- Some tool functions validate input *before* their `try/except` blocks.
  Those validation errors will raise `pydantic.ValidationError` rather than being
  converted into a structured error dict. Tests reflect that current behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypedDict

import pytest
from pydantic import ValidationError

from app.tools.custom_documents import (
    create_convert_files_to_markdown,
    create_ingest_documents,
    create_ingest_markdown_files,
    create_ingest_pdf_files,
    create_search_documents,
)

if TYPE_CHECKING:
    from mcp_refcache import RefCache

# ---------------------------------------------------------------------------
# Local type definitions for test readability (not shared across modules)
# ---------------------------------------------------------------------------


class CachedToolResponse(TypedDict, total=False):
    """Shape returned by the @cache.cached decorator."""

    ref_id: str
    value: dict[str, Any]
    is_complete: bool
    size: int
    total_items: int


class CachedToolWrapper(Protocol):
    """Protocol for cached tool functions with optional `.fn` attribute."""

    fn: Any  # The underlying async callable

    async def __call__(self, **kwargs: Any) -> CachedToolResponse: ...


class HitMetadata(TypedDict, total=False):
    """Metadata attached to a search hit."""

    tenant_id: str
    case_id: str | None
    source_name: str
    ingested_at: str
    tags_csv: str
    tag: str | None
    document_id: str


def _unwrap_tool_function(tool_function: CachedToolWrapper | Any) -> Any:
    """Return the underlying callable for a cached tool function.

    Args:
        tool_function: The object returned by a tool factory.

    Returns:
        A callable (typically an async function) that can be awaited.
    """
    if hasattr(tool_function, "fn"):
        return tool_function.fn
    return tool_function


def _extract_cached_value(cache_response: CachedToolResponse) -> dict[str, Any] | None:
    """Extract the inner tool payload from a cache-wrapped response.

    The RefCache `cached` decorator returns a dict that may contain:
    - `value`: the full underlying return value (what the tool function returned)
    - `preview`: a truncated/preview representation (for large payloads)
    - `ref_id`: a reference ID for later retrieval

    Args:
        cache_response: The result returned by calling a cached tool.

    Returns:
        The inner payload dict if present under `value`, otherwise None.
    """
    value = cache_response.get("value")
    if isinstance(value, dict):
        return value
    return None


@dataclass(frozen=True)
class _FakeSettings:
    ingest_root_path: str | None = None


@dataclass(frozen=True)
class _FakeHit:
    """Test double for search hit results."""

    chunk_id: str
    content: str
    similarity: float
    metadata: HitMetadata | None = None


class _FakeCustomDocumentEmbeddingStore:
    """Test double for CustomDocumentEmbeddingStore."""

    where_calls: ClassVar[list[dict[str, Any]]] = []
    search_calls: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def build_tenant_where(
        cls,
        tenant_id: str,
        case_id: str | None = None,
        document_id: str | None = None,
        source_name: str | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        where = {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "document_id": document_id,
            "source_name": source_name,
            "tag": tag,
        }
        cls.where_calls.append(where)
        # A realistic "where" shape is not required for the tool tests; only that it is passed through.
        return where

    def search(
        self, query: str, n_results: int, where: dict[str, Any]
    ) -> list[_FakeHit]:
        type(self).search_calls.append(
            {"query": query, "n_results": n_results, "where": where}
        )
        return [
            _FakeHit(
                chunk_id="chunk_1",
                content="0123456789" * 20,  # 200 chars
                similarity=0.98765,
                metadata={
                    "tenant_id": where.get("tenant_id"),
                    "case_id": where.get("case_id"),
                    "source_name": "notes.md",
                    "ingested_at": "2026-02-01T00:00:00Z",
                    "tags_csv": "a,b",
                    "tag": where.get("tag"),
                    "document_id": "doc_1",
                },
            )
        ]


@pytest.mark.asyncio
async def test_create_ingest_documents_success(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """ingest_documents validates and forwards documents to pipeline."""
    captured: dict[str, Any] = {}

    def fake_ingest_custom_documents(
        tenant_id: str,
        case_id: str | None,
        documents: list[dict[str, Any]],
        tags: list[str] | None,
        chunking: dict[str, Any] | None,
        replace: bool | None = None,
    ) -> dict[str, Any]:
        captured["tenant_id"] = tenant_id
        captured["case_id"] = case_id
        captured["documents"] = documents
        captured["tags"] = tags
        captured["chunking"] = chunking
        captured["replace"] = replace
        return {
            "status": "complete",
            "tenant_id": tenant_id,
            "case_id": case_id,
            "totals": {"documents_received": len(documents)},
            "documents": [
                {"document_id": documents[0].get("document_id") or "generated"}
            ],
        }

    monkeypatch.setattr(
        "app.custom_documents.pipeline.ingest_custom_documents",
        fake_ingest_custom_documents,
        raising=True,
    )

    tool_function = create_ingest_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(
        tenant_id="tenant_1",
        case_id="case_1",
        documents=[
            {
                "source_name": "notes.txt",
                "text": "Tenant reports mold in bathroom.",
                "document_id": "doc_abc",
                "metadata": {"document_type": "complaint"},
            }
        ],
        tags=["housing"],
        chunking={"chunk_size_chars": 500, "chunk_overlap_chars": 50},
    )

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["status"] == "complete"

    assert captured["tenant_id"] == "tenant_1"
    assert captured["case_id"] == "case_1"
    assert captured["tags"] == ["housing"]
    assert captured["chunking"] == {
        "chunk_size_chars": 500,
        "chunk_overlap_chars": 50,
        "max_chunks_per_document": None,
    }
    assert captured["documents"][0]["source_name"] == "notes.txt"
    assert captured["documents"][0]["document_id"] == "doc_abc"
    assert captured["documents"][0]["metadata"] == {"document_type": "complaint"}


@pytest.mark.asyncio
async def test_create_ingest_documents_validation_error_raises_validation_error(
    cache: RefCache,
) -> None:
    """ingest_documents validation currently raises ValidationError (not structured)."""
    tool_function = create_ingest_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)

    # Invalid: chunk_overlap_chars >= chunk_size_chars
    with pytest.raises(ValidationError) as exc_info:
        await tool_callable(
            tenant_id="tenant_1",
            documents=[{"source_name": "a.txt", "text": "x"}],
            chunking={"chunk_size_chars": 200, "chunk_overlap_chars": 200},
        )

    assert "chunk_overlap_chars" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_ingest_documents_rejects_non_string_metadata_values_raises_validation_error(
    cache: RefCache,
) -> None:
    """ingest_documents metadata typing errors currently raise ValidationError."""
    tool_function = create_ingest_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)

    with pytest.raises(ValidationError) as exc_info:
        await tool_callable(
            tenant_id="tenant_1",
            documents=[
                {
                    "source_name": "a.txt",
                    "text": "hello",
                    "metadata": {"ok": "yes", "bad": 123},
                }
            ],
        )

    assert "metadata" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_ingest_documents_handles_pipeline_exception(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """ingest_documents converts pipeline exceptions into structured error dict."""

    def fake_ingest_custom_documents(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(
        "app.custom_documents.pipeline.ingest_custom_documents",
        fake_ingest_custom_documents,
        raising=True,
    )

    tool_function = create_ingest_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(
        tenant_id="tenant_1",
        documents=[{"source_name": "a.txt", "text": "hello"}],
    )

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["status"] == "failed"
    assert inner_value["error"] == "Custom document ingestion failed"
    assert inner_value["tenant_id"] == "tenant_1"
    assert "pipeline exploded" in inner_value["message"]


@pytest.mark.asyncio
async def test_create_search_documents_success_excerpts_and_rounds_similarity(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """search_documents returns excerpt (with ellipsis) and rounds similarity to 3 decimals."""
    _FakeCustomDocumentEmbeddingStore.where_calls = []
    _FakeCustomDocumentEmbeddingStore.search_calls = []

    monkeypatch.setattr(
        "app.custom_documents.embeddings.CustomDocumentEmbeddingStore",
        _FakeCustomDocumentEmbeddingStore,
        raising=True,
    )

    tool_function = create_search_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(
        query="mold in bathroom",
        tenant_id="tenant_1",
        case_id="case_1",
        n_results=5,
        excerpt_chars=50,
        tag="housing",
    )

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    if inner_value is None:
        # Cache layer may decide to return only a `preview` and/or `ref_id`
        # (without inlining the full value). In that case, we can only assert
        # the wrapper shape and that the underlying store was called correctly.
        assert "preview" in cache_response or "ref_id" in cache_response

        # Ensure tenant scoping was applied via the store where builder.
        assert _FakeCustomDocumentEmbeddingStore.where_calls == [
            {
                "tenant_id": "tenant_1",
                "case_id": "case_1",
                "document_id": None,
                "source_name": None,
                "tag": "housing",
            }
        ]
        return

    assert inner_value["query"] == "mold in bathroom"
    assert inner_value["count"] == 1
    assert inner_value["filters"]["tenant_id"] == "tenant_1"
    assert inner_value["filters"]["case_id"] == "case_1"
    assert inner_value["filters"]["tag"] == "housing"

    hit = inner_value["results"][0]
    assert hit["chunk_id"] == "chunk_1"
    assert hit["document_id"] == "doc_1"
    assert hit["similarity"] == 0.988  # rounded from 0.98765
    assert hit["excerpt"].endswith("...")

    # Ensure tenant scoping was applied via the store where builder.
    assert _FakeCustomDocumentEmbeddingStore.where_calls == [
        {
            "tenant_id": "tenant_1",
            "case_id": "case_1",
            "document_id": None,
            "source_name": None,
            "tag": "housing",
        }
    ]


@pytest.mark.asyncio
async def test_create_search_documents_success_returns_full_content_when_short(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """If content length <= excerpt_chars, return content without ellipsis."""

    class _ShortContentStore(_FakeCustomDocumentEmbeddingStore):
        def search(
            self, query: str, n_results: int, where: dict[str, Any]
        ) -> list[_FakeHit]:
            return [
                _FakeHit(
                    chunk_id="chunk_1",
                    content="short text",
                    similarity=0.1,
                    metadata={"tenant_id": where["tenant_id"], "document_id": "doc_1"},
                )
            ]

    monkeypatch.setattr(
        "app.custom_documents.embeddings.CustomDocumentEmbeddingStore",
        _ShortContentStore,
        raising=True,
    )

    tool_function = create_search_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(
        query="xkcd",
        tenant_id="tenant_1",
        excerpt_chars=50,
    )

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["results"][0]["excerpt"] == "short text"


@pytest.mark.asyncio
async def test_create_search_documents_validation_error_raises_validation_error(
    cache: RefCache,
) -> None:
    """search_documents validation currently raises ValidationError (not structured)."""
    tool_function = create_search_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)

    with pytest.raises(ValidationError) as exc_info:
        await tool_callable(
            query="a",  # min_length=2
            tenant_id="tenant_1",
        )

    assert "query" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_search_documents_handles_store_exception(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """search_documents converts store exceptions into structured error dict."""

    class _ExplodingStore:
        @classmethod
        def build_tenant_where(cls, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"tenant_id": "tenant_1"}

        def search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
            raise RuntimeError("store exploded")

    monkeypatch.setattr(
        "app.custom_documents.embeddings.CustomDocumentEmbeddingStore",
        _ExplodingStore,
        raising=True,
    )

    tool_function = create_search_documents(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(query="mold", tenant_id="tenant_1")

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["error"] == "Search failed"
    assert inner_value["query"] == "mold"
    assert inner_value["filters"]["tenant_id"] == "tenant_1"
    assert "store exploded" in inner_value["message"]


@pytest.mark.asyncio
async def test_create_ingest_markdown_files_returns_misconfigured_root_error(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """ingest_markdown_files returns structured error if allowlisted root cannot be resolved."""

    class _FakeFileIngestionError(Exception):
        pass

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )

    def fake_require_allowlisted_root(*_args: Any, **_kwargs: Any) -> Any:
        raise _FakeFileIngestionError("ingest root disabled")

    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.FileIngestionError",
        _FakeFileIngestionError,
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.require_allowlisted_root",
        fake_require_allowlisted_root,
        raising=True,
    )

    tool_function = create_ingest_markdown_files(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(
        tenant_id="tenant_1",
        paths=["a.md"],
        case_id="case_1",
    )

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["status"] == "failed"
    assert inner_value["error"] == "File ingestion is disabled or misconfigured"
    assert inner_value["tenant_id"] == "tenant_1"
    assert inner_value["case_id"] == "case_1"
    assert "ingest root disabled" in inner_value["message"]


@pytest.mark.asyncio
async def test_create_convert_files_to_markdown_success_single_file(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """convert_files_to_markdown returns per-file complete summary when conversion + write succeed."""

    class _FakeFileIngestionError(Exception):
        pass

    class _FakeFileConversionError(Exception):
        pass

    @dataclass(frozen=True)
    class _FakeConversionResult:
        source_name: str
        file_suffix: str
        markdown: str
        metadata: dict[str, str]

    @dataclass(frozen=True)
    class _FakeWriteResult:
        relative_path: str
        size_bytes: int
        overwritten: bool

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.FileIngestionError",
        _FakeFileIngestionError,
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.conversion.markitdown_converter.FileConversionError",
        _FakeFileConversionError,
        raising=True,
    )

    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.require_allowlisted_root",
        lambda *_args, **_kwargs: object(),
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.resolve_allowlisted_file",
        lambda _root, relative_path, allowed_suffixes: type(
            "P", (), {"name": relative_path, "suffix": ".pdf"}
        )(),
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.conversion.markitdown_converter.convert_allowlisted_file_to_markdown",
        lambda _path, max_chars: _FakeConversionResult(
            source_name="a.pdf",
            file_suffix=".pdf",
            markdown="# Converted",
            metadata={"converter": "fake"},
        ),
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.write_text_utf8_under_allowlisted_root",
        lambda _root,
        output_relative_path,
        text,
        allowed_suffixes,
        overwrite: _FakeWriteResult(
            relative_path=output_relative_path,
            size_bytes=len(text.encode("utf-8")),
            overwritten=True,
        ),
        raising=True,
    )

    tool_function = create_convert_files_to_markdown(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(paths=["a.pdf"], overwrite=True)

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["status"] == "complete"
    assert inner_value["totals"]["files_received"] == 1
    assert inner_value["totals"]["files_converted"] == 1
    assert inner_value["totals"]["errors"] == 0
    assert inner_value["files"][0]["status"] == "complete"
    assert inner_value["files"][0]["output_path"] == "a.pdf.md"
    assert inner_value["files"][0]["metadata"]["output_size_bytes"] == str(
        len(b"# Converted")
    )


@pytest.mark.asyncio
async def test_create_convert_files_to_markdown_rejects_bad_suffix(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """convert_files_to_markdown reports structured per-file error on suffix rejection."""

    class _FakeFileIngestionError(Exception):
        pass

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.FileIngestionError",
        _FakeFileIngestionError,
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.require_allowlisted_root",
        lambda *_args, **_kwargs: object(),
        raising=True,
    )

    def fake_resolve_allowlisted_file(*_args: Any, **_kwargs: Any) -> Any:
        raise _FakeFileIngestionError("suffix not allowed")

    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.resolve_allowlisted_file",
        fake_resolve_allowlisted_file,
        raising=True,
    )

    tool_function = create_convert_files_to_markdown(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(paths=["a.exe"])

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["status"] == "failed"
    assert inner_value["totals"]["files_received"] == 1
    assert inner_value["totals"]["files_converted"] == 0
    assert inner_value["totals"]["errors"] == 1
    assert inner_value["files"][0]["status"] == "failed"
    assert "suffix not allowed" in inner_value["files"][0]["error"]


@pytest.mark.asyncio
async def test_create_ingest_pdf_files_handles_conversion_error(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """ingest_pdf_files reports per-file error when pdf conversion fails."""

    class _FakeFileIngestionError(Exception):
        pass

    class _FakeFileConversionError(Exception):
        pass

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.FileIngestionError",
        _FakeFileIngestionError,
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.conversion.markitdown_converter.FileConversionError",
        _FakeFileConversionError,
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.require_allowlisted_root",
        lambda *_args, **_kwargs: object(),
        raising=True,
    )
    monkeypatch.setattr(
        "app.custom_documents.file_ingestion.resolve_allowlisted_file",
        lambda _root, relative_path, allowed_suffixes: type(
            "P", (), {"name": relative_path, "suffix": ".pdf"}
        )(),
        raising=True,
    )

    def fake_convert_pdf_to_markdown(*_args: Any, **_kwargs: Any) -> Any:
        raise _FakeFileConversionError("pdf parse failed")

    monkeypatch.setattr(
        "app.custom_documents.conversion.markitdown_converter.convert_pdf_to_markdown",
        fake_convert_pdf_to_markdown,
        raising=True,
    )

    tool_function = create_ingest_pdf_files(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    cache_response = await tool_callable(tenant_id="tenant_1", paths=["a.pdf"])

    assert isinstance(cache_response, dict)
    assert (
        "value" in cache_response
        or "preview" in cache_response
        or "ref_id" in cache_response
    )

    inner_value = _extract_cached_value(cache_response)
    assert inner_value is not None
    assert inner_value["status"] == "failed"
    assert inner_value["tenant_id"] == "tenant_1"
    assert inner_value["totals"]["files_received"] == 1
    assert inner_value["totals"]["files_ingested"] == 0
    assert inner_value["totals"]["errors"] == 1
    assert inner_value["files"][0]["path"] == "a.pdf"
    assert "pdf parse failed" in inner_value["files"][0]["errors"][0]
