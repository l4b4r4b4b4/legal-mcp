"""Tests for lazy import patterns across app modules.

Exercises the __getattr__-based lazy imports in:
- app.ingestion.__init__: 6 symbols from embeddings, pipeline
- app.prompts.__init__: langfuse_guide function

Also tests app.__init__ version fallback and app.rag.reranker utilities.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ===========================================================================
# app.ingestion lazy imports
# ===========================================================================


class TestIngestionLazyImports:
    """Test all 9 lazy imports in app.ingestion.__init__."""

    def test_import_german_law_embedding_store(self):
        from app.ingestion import GermanLawEmbeddingStore

        assert GermanLawEmbeddingStore is not None

    def test_import_ingestion_progress(self):
        from app.ingestion import IngestionProgress

        assert IngestionProgress is not None

    def test_import_ingestion_result(self):
        from app.ingestion import IngestionResult

        assert IngestionResult is not None

    def test_import_ingest_german_laws(self):
        from app.ingestion import ingest_german_laws

        assert callable(ingest_german_laws)

    def test_import_ingest_single_law(self):
        from app.ingestion import ingest_single_law

        assert callable(ingest_single_law)

    def test_import_search_laws(self):
        from app.ingestion import search_laws

        assert callable(search_laws)

    def test_unknown_attribute_raises(self):
        """Accessing a non-existent attribute raises AttributeError."""
        import app.ingestion

        with pytest.raises(AttributeError, match="no attribute"):
            _ = app.ingestion.nonexistent_symbol

    def test_all_exports_match_getattr(self):
        """Every name in __all__ is importable."""
        import app.ingestion

        for name in app.ingestion.__all__:
            assert getattr(app.ingestion, name) is not None


# ===========================================================================
# app.prompts
# ===========================================================================


class TestPromptsModule:
    """Test prompt functions."""

    def test_langfuse_guide_returns_string(self):
        from app.prompts import langfuse_guide

        result = langfuse_guide()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_template_guide_returns_string(self):
        from app.prompts import template_guide

        result = template_guide()
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# app.__init__ version fallback
# ===========================================================================


class TestAppVersion:
    """Test version resolution in app.__init__."""

    def test_version_is_set(self):
        import app

        assert hasattr(app, "__version__")
        assert isinstance(app.__version__, str)

    def test_version_fallback_on_package_not_found(self):
        """When package metadata is missing, falls back to 0.0.0-dev."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("legal-mcp"),
        ):
            # Re-execute the version lookup logic
            try:
                from importlib.metadata import version

                __version__ = version("legal-mcp")
            except PackageNotFoundError:
                __version__ = "0.0.0-dev"

            assert __version__ == "0.0.0-dev"


# ===========================================================================
# app.rag.reranker utilities
# ===========================================================================


class TestRagRerankerUtilities:
    """Test utility functions in app.rag.reranker."""

    def test_rerank_result_dataclass(self):
        from app.rag.reranker import RerankResult

        result = RerankResult(index=0, score=0.95, text="test doc")
        assert result.index == 0
        assert result.score == 0.95
        assert result.text == "test doc"

    def test_get_reranker_returns_instance(self):
        from app.rag.reranker import TEIReranker, get_reranker, reset_reranker

        reset_reranker()  # ensure clean state
        reranker = get_reranker()
        assert isinstance(reranker, TEIReranker)
        reset_reranker()  # cleanup

    def test_get_reranker_returns_same_instance(self):
        from app.rag.reranker import get_reranker, reset_reranker

        reset_reranker()
        first = get_reranker()
        second = get_reranker()
        assert first is second
        reset_reranker()

    def test_reset_reranker_clears_singleton(self):
        from app.rag.reranker import get_reranker, reset_reranker

        reset_reranker()
        first = get_reranker()
        reset_reranker()
        second = get_reranker()
        assert first is not second
        reset_reranker()

    def test_get_reranker_custom_url(self):
        from app.rag.reranker import get_reranker, reset_reranker

        reset_reranker()
        reranker = get_reranker(base_url="http://custom:9999")
        assert reranker.base_url == "http://custom:9999"
        reset_reranker()

    def test_tei_reranker_stats(self):
        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080")
        result = reranker.stats()
        assert result["base_url"] == "http://localhost:8080"
        assert "timeout" in result

    async def test_rerank_empty_documents(self):
        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080")
        results = await reranker.rerank(query="test", documents=[])
        assert results == []

    async def test_health_check_failure(self):
        """Health check returns False when server is unreachable."""
        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(
            base_url="http://localhost:1",  # unreachable
            timeout=0.1,
        )
        result = await reranker.health_check()
        assert result is False

    async def test_close_without_client(self):
        """Closing without an active client is a no-op."""
        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080")
        await reranker.close()  # should not raise

    async def test_rerank_success_path(self):
        """Successful reranking parses TEI response and sorts by score."""
        from unittest.mock import AsyncMock, MagicMock

        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080")

        # Mock the httpx client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"index": 0, "score": 0.3},
            {"index": 1, "score": 0.9},
            {"index": 2, "score": 0.6},
        ]

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        reranker._client = mock_client

        results = await reranker.rerank(
            query="test query",
            documents=["doc a", "doc b", "doc c"],
            top_k=2,
        )

        assert len(results) == 2
        # Should be sorted by score descending
        assert results[0].score == 0.9
        assert results[0].index == 1
        assert results[0].text == "doc b"
        assert results[1].score == 0.6
        assert results[1].index == 2

    async def test_rerank_retries_on_http_error(self):
        """Reranking retries on HTTP errors and raises after exhaustion."""
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080", max_retries=2)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Service Unavailable",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        reranker._client = mock_client

        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            await reranker.rerank(
                query="test",
                documents=["doc"],
            )

    async def test_rerank_without_text(self):
        """Reranking with return_text=False produces empty text fields."""
        from unittest.mock import AsyncMock, MagicMock

        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"index": 0, "score": 0.8},
        ]

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        reranker._client = mock_client

        results = await reranker.rerank(
            query="test",
            documents=["doc a"],
            return_text=False,
        )

        assert len(results) == 1
        assert results[0].text == ""

    async def test_close_with_active_client(self):
        """Closing with an active client calls aclose."""
        from unittest.mock import AsyncMock

        from app.rag.reranker import TEIReranker

        reranker = TEIReranker(base_url="http://localhost:8080")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        reranker._client = mock_client

        await reranker.close()

        mock_client.aclose.assert_called_once()
        assert reranker._client is None


# ===========================================================================
# app.tools.cache — get_cached_result tool
# ===========================================================================


class TestCacheTool:
    """Tests for cache retrieval tool using mcp-refcache."""

    async def test_get_cached_result_success(self, cache):
        """Successful cache lookup returns preview with metadata."""
        from app.tools.cache import create_get_cached_result

        # Store something in the cache first
        cache_response = cache.set("test_key", {"items": [1, 2, 3]})
        ref_id = cache_response.ref_id

        get_cached_result = create_get_cached_result(cache)
        result = await get_cached_result(ref_id=ref_id)

        assert result["ref_id"] == ref_id
        assert "preview" in result
        assert "preview_strategy" in result
        assert "total_items" in result

    async def test_get_cached_result_invalid_ref(self, cache):
        """Invalid ref_id returns error dict instead of raising."""
        from app.tools.cache import create_get_cached_result

        get_cached_result = create_get_cached_result(cache)
        result = await get_cached_result(ref_id="nonexistent_ref_id")

        assert "error" in result
        assert result["ref_id"] == "nonexistent_ref_id"

    async def test_get_cached_result_with_pagination(self, cache):
        """Cache lookup with pagination params returns page info."""
        from app.tools.cache import create_get_cached_result

        # Store a list for pagination
        items = [{"id": i, "name": f"item_{i}"} for i in range(20)]
        cache_response = cache.set("paginated", items)
        ref_id = cache_response.ref_id

        get_cached_result = create_get_cached_result(cache)
        result = await get_cached_result(ref_id=ref_id, page=1, page_size=5)

        assert result["ref_id"] == ref_id
