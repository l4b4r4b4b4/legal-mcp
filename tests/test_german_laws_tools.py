"""Unit tests for German law MCP tool factories.

These tests focus on tool behavior (input validation, error handling, and
return shape) while mocking heavy/external dependencies:
- Ingestion pipeline functions (`app.ingestion.pipeline.search_laws`,
  `app.ingestion.pipeline.ingest_german_laws`)
- Embedding store (`app.ingestion.embeddings.GermanLawEmbeddingStore`)
- Settings (`app.config.get_settings`)

Important: tool factories wrap functions with `@cache.cached(...)`. The callable
returns a *cache response dict* (typically containing `value`, `preview`, and/or
`ref_id`) rather than the tool's raw payload.

These tests assert on the cache-wrapped response shape and then inspect the
inner payload under `response["value"]` when present.

Note on validation:
- The tools in `app.tools.german_laws` validate input *before* their `try/except`
  blocks. As a result, invalid inputs raise `pydantic.ValidationError` rather
  than returning a structured `{"error": ...}` payload. Tests reflect that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypedDict

import pytest
from pydantic import ValidationError

from app.tools.german_laws import (
    create_get_law_by_id,
    create_get_law_stats,
    create_ingest_german_laws,
    create_search_laws,
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


class LawDocMetadata(TypedDict, total=False):
    """Metadata attached to a law document."""

    law_abbrev: str
    norm_id: str
    level: str
    source_url: str


@dataclass(frozen=True)
class _FakeSettings:
    embedding_model: str = "fake-embedding-model"
    chroma_persist_path: str = "/tmp/fake-chroma-path"


def _unwrap_tool_function(tool_function: CachedToolWrapper | Any) -> Any:
    """Return the underlying callable for a cached tool function."""
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
class _FakeLawDoc:
    """Test double for law document objects returned by the embedding store."""

    doc_id: str
    content: str
    metadata: LawDocMetadata


class _FakeGermanLawEmbeddingStore:
    """Test double for GermanLawEmbeddingStore."""

    init_calls: ClassVar[list[dict[str, Any]]] = []
    stats_calls: ClassVar[int] = 0
    get_by_law_calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, model_name: str, persist_path: Any | None = None) -> None:
        type(self).init_calls.append(
            {"model_name": model_name, "persist_path": persist_path}
        )

    def stats(self) -> dict[str, Any]:
        type(self).stats_calls += 1
        return {
            "collection_name": "german_laws",
            "documents": 123,
            "embedding_model": "fake-embedding-model",
        }

    def get_by_law(
        self, law_abbrev: str, norm_id: str | None = None
    ) -> list[_FakeLawDoc]:
        type(self).get_by_law_calls.append(
            {"law_abbrev": law_abbrev, "norm_id": norm_id}
        )

        if law_abbrev == "BGB" and norm_id == "§ 433":
            return [
                _FakeLawDoc(
                    doc_id="doc_1",
                    content="(fake) § 433 BGB content",
                    metadata={
                        "law_abbrev": "BGB",
                        "norm_id": "§ 433",
                        "level": "norm",
                        "source_url": "https://example.invalid/bgb/433",
                    },
                )
            ]

        if law_abbrev == "BGB" and norm_id is None:
            return [
                _FakeLawDoc(
                    doc_id="doc_1",
                    content="(fake) § 433 BGB content",
                    metadata={"law_abbrev": "BGB", "norm_id": "§ 433", "level": "norm"},
                ),
                _FakeLawDoc(
                    doc_id="doc_2",
                    content="(fake) § 434 BGB content",
                    metadata={"law_abbrev": "BGB", "norm_id": "§ 434", "level": "norm"},
                ),
            ]

        return []


@pytest.mark.asyncio
async def test_create_search_laws_success(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """search_laws returns expected shape on success."""
    captured: dict[str, Any] = {}

    def fake_search_laws_impl(
        query: str,
        n_results: int,
        law_abbrev: str | None,
        level: str | None,
    ) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["n_results"] = n_results
        captured["law_abbrev"] = law_abbrev
        captured["level"] = level
        return [
            {
                "doc_id": "doc_1",
                "content": "fake",
                "similarity": 0.123,
                "law_abbrev": "BGB",
                "norm_id": "§ 433",
            }
        ]

    monkeypatch.setattr(
        "app.ingestion.pipeline.search_laws",
        fake_search_laws_impl,
        raising=True,
    )

    tool_function = create_search_laws(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(
        query="Kaufvertrag Pflichten",
        n_results=5,
        law_abbrev="BGB",
        level="norm",
    )

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["query"] == "Kaufvertrag Pflichten"
    assert inner_value["count"] == 1
    assert inner_value["filters"] == {"law_abbrev": "BGB", "level": "norm"}
    assert inner_value["results"][0]["doc_id"] == "doc_1"
    assert captured == {
        "query": "Kaufvertrag Pflichten",
        "n_results": 5,
        "law_abbrev": "BGB",
        "level": "norm",
    }


@pytest.mark.asyncio
async def test_create_search_laws_validation_error(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """search_laws validation currently raises ValidationError (not structured)."""
    monkeypatch.setattr(
        "app.ingestion.pipeline.search_laws",
        lambda **_kwargs: [],
        raising=True,
    )

    tool_function = create_search_laws(cache)
    tool_callable = _unwrap_tool_function(tool_function)

    with pytest.raises(ValidationError) as exc_info:
        await tool_callable(query="a")  # too short (min_length=2)

    assert "query" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_search_laws_handles_pipeline_exception(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """search_laws converts exceptions into structured error dict."""

    def fake_search_laws_impl(**_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.ingestion.pipeline.search_laws",
        fake_search_laws_impl,
        raising=True,
    )

    tool_function = create_search_laws(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(query="tenant rights", n_results=3)

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["error"] == "Search failed"
    assert inner_value["query"] == "tenant rights"
    assert "boom" in inner_value["message"]


@pytest.mark.asyncio
async def test_create_ingest_german_laws_success(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """ingest_german_laws returns dict converted via result.to_dict()."""

    class _FakeIngestionResult:
        def to_dict(self) -> dict[str, Any]:
            return {
                "status": "complete",
                "documents_added": 10,
                "laws_processed": 2,
                "norms_processed": 20,
                "error_count": 0,
                "errors": [],
                "elapsed_seconds": 1.2,
            }

    def fake_ingest_impl(max_laws: int, max_norms_per_law: int | None) -> Any:
        assert isinstance(max_laws, int)
        return _FakeIngestionResult()

    monkeypatch.setattr(
        "app.ingestion.pipeline.ingest_german_laws",
        fake_ingest_impl,
        raising=True,
    )

    tool_function = create_ingest_german_laws(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(max_laws=5, max_norms_per_law=3)

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["status"] == "complete"
    assert inner_value["documents_added"] == 10
    assert inner_value["laws_processed"] == 2


@pytest.mark.asyncio
async def test_create_ingest_german_laws_validation_error(cache: RefCache) -> None:
    """ingest_german_laws validation currently raises ValidationError (not structured)."""
    tool_function = create_ingest_german_laws(cache)
    tool_callable = _unwrap_tool_function(tool_function)

    with pytest.raises(ValidationError) as exc_info:
        await tool_callable(max_laws=0)  # ge=1

    assert "max_laws" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_ingest_german_laws_handles_exception(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """ingest_german_laws converts exceptions to structured error dict."""

    def fake_ingest_impl(**_kwargs: Any) -> Any:
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(
        "app.ingestion.pipeline.ingest_german_laws",
        fake_ingest_impl,
        raising=True,
    )

    tool_function = create_ingest_german_laws(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(max_laws=1)

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["error"] == "Ingestion failed"
    assert inner_value["status"] == "failed"
    assert "ingest exploded" in inner_value["message"]


@pytest.mark.asyncio
async def test_create_get_law_stats_success(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """get_law_stats returns status ok and merges store stats."""
    _FakeGermanLawEmbeddingStore.init_calls = []
    _FakeGermanLawEmbeddingStore.stats_calls = 0

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )
    monkeypatch.setattr(
        "app.ingestion.embeddings.GermanLawEmbeddingStore",
        _FakeGermanLawEmbeddingStore,
        raising=True,
    )

    tool_function = create_get_law_stats(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable()

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["status"] == "ok"
    assert inner_value["documents"] == 123
    assert _FakeGermanLawEmbeddingStore.stats_calls == 1
    assert (
        _FakeGermanLawEmbeddingStore.init_calls[0]["model_name"]
        == "fake-embedding-model"
    )


@pytest.mark.asyncio
async def test_create_get_law_stats_handles_exception(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """get_law_stats returns partial info on store failure."""
    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )

    class _ExplodingStore:
        def __init__(self, model_name: str) -> None:
            raise RuntimeError("store init failed")

    monkeypatch.setattr(
        "app.ingestion.embeddings.GermanLawEmbeddingStore",
        _ExplodingStore,
        raising=True,
    )

    tool_function = create_get_law_stats(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable()

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["status"] == "error"
    assert "store init failed" in inner_value["message"]
    assert inner_value["embedding_model"] == "fake-embedding-model"
    assert inner_value["chroma_persist_path"] == "/tmp/fake-chroma-path"


@pytest.mark.asyncio
async def test_create_get_law_by_id_success_returns_results(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """get_law_by_id returns results list for existing doc(s)."""
    _FakeGermanLawEmbeddingStore.get_by_law_calls = []

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )
    monkeypatch.setattr(
        "app.ingestion.embeddings.GermanLawEmbeddingStore",
        _FakeGermanLawEmbeddingStore,
        raising=True,
    )

    tool_function = create_get_law_by_id(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(law_abbrev="bgb", norm_id="§ 433")

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["law_abbrev"] == "BGB"
    assert inner_value["norm_id"] == "§ 433"
    assert inner_value["count"] == 1
    assert inner_value["results"][0]["doc_id"] == "doc_1"
    assert "content" in inner_value["results"][0]
    assert _FakeGermanLawEmbeddingStore.get_by_law_calls == [
        {"law_abbrev": "BGB", "norm_id": "§ 433"}
    ]


@pytest.mark.asyncio
async def test_create_get_law_by_id_not_found(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """get_law_by_id returns structured not-found response."""
    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )
    monkeypatch.setattr(
        "app.ingestion.embeddings.GermanLawEmbeddingStore",
        _FakeGermanLawEmbeddingStore,
        raising=True,
    )

    tool_function = create_get_law_by_id(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(law_abbrev="StGB", norm_id="§ 999")

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["error"] == "Not found"
    assert inner_value["law_abbrev"] == "StGB"
    assert inner_value["norm_id"] == "§ 999"
    assert "No documents found" in inner_value["message"]


@pytest.mark.asyncio
async def test_create_get_law_by_id_handles_exception(
    monkeypatch: pytest.MonkeyPatch, cache: RefCache
) -> None:
    """get_law_by_id converts exceptions into structured error dict."""
    monkeypatch.setattr(
        "app.config.get_settings", lambda: _FakeSettings(), raising=True
    )

    class _ExplodingStore:
        def __init__(self, model_name: str) -> None:
            self._model_name = model_name

        def get_by_law(self, law_abbrev: str, norm_id: str | None = None) -> list[Any]:
            raise RuntimeError("lookup exploded")

    monkeypatch.setattr(
        "app.ingestion.embeddings.GermanLawEmbeddingStore",
        _ExplodingStore,
        raising=True,
    )

    tool_function = create_get_law_by_id(cache)
    tool_callable = _unwrap_tool_function(tool_function)
    result = await tool_callable(law_abbrev="BGB", norm_id="§ 433")

    assert isinstance(result, dict)
    assert "value" in result or "preview" in result or "ref_id" in result

    inner_value = _extract_cached_value(result)
    assert inner_value is not None

    assert inner_value["error"] == "Lookup failed"
    assert inner_value["law_abbrev"] == "BGB"
    assert inner_value["norm_id"] == "§ 433"
    assert "lookup exploded" in inner_value["message"]
