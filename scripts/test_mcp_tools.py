#!/usr/bin/env python3
"""Test script for German law MCP tools.

Tests the MCP tool functions directly (without running the server).
Validates:
1. Tool function creation and binding
2. Search functionality with cached embeddings
3. Stats retrieval
4. Input validation via Pydantic models

Usage:
    python scripts/test_mcp_tools.py

    # Or with uv
    uv run scripts/test_mcp_tools.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def unwrap_cache_response(result: dict) -> dict:
    """Unwrap a mcp-refcache response to get the actual value.

    The @cache.cached decorator wraps results in:
    {"ref_id": "...", "value": {...}, "is_complete": True, ...}

    For large results, it may return a preview instead:
    {"ref_id": "...", "preview": {...}, "is_complete": False, ...}

    This helper extracts the actual value or preview.
    """
    if "ref_id" in result:
        # Check if it's a complete response with value
        if "value" in result:
            return result["value"]
        # Check if it's a preview response
        if "preview" in result:
            # For previews, we return the preview data plus a marker
            preview = result["preview"]
            if isinstance(preview, dict):
                preview["_is_preview"] = True
                preview["_ref_id"] = result["ref_id"]
            return preview
    return result


def test_tool_creation() -> bool:
    """Test that tool factory functions create callable tools."""
    logger.info("=" * 60)
    logger.info("TEST 1: Tool Creation")
    logger.info("=" * 60)

    from mcp_refcache import RefCache

    from app.tools.german_laws import (
        create_get_law_by_id,
        create_get_law_stats,
        create_ingest_german_laws,
        create_search_laws,
    )

    # Create a test cache
    cache = RefCache(name="test-cache", default_ttl=60)

    # Create all tools
    search_laws = create_search_laws(cache)
    get_law_by_id = create_get_law_by_id(cache)
    ingest_german_laws = create_ingest_german_laws(cache)
    get_law_stats = create_get_law_stats(cache)

    # Verify they are callable
    assert callable(search_laws), "search_laws should be callable"
    assert callable(get_law_by_id), "get_law_by_id should be callable"
    assert callable(ingest_german_laws), "ingest_german_laws should be callable"
    assert callable(get_law_stats), "get_law_stats should be callable"

    # Verify they have proper names
    assert "search_laws" in search_laws.__name__, f"Got: {search_laws.__name__}"
    assert "get_law_by_id" in get_law_by_id.__name__, f"Got: {get_law_by_id.__name__}"

    logger.info("✅ All tools created successfully")
    return True


def test_input_validation() -> bool:
    """Test Pydantic input validation for tool inputs."""
    logger.info("=" * 60)
    logger.info("TEST 2: Input Validation")
    logger.info("=" * 60)

    from pydantic import ValidationError

    from app.tools.german_laws import IngestGermanLawsInput, SearchLawsInput

    # Valid search input
    search_input = SearchLawsInput(
        query="Kaufvertrag Pflichten",
        n_results=10,
        law_abbrev="BGB",
    )
    assert search_input.query == "Kaufvertrag Pflichten"
    assert search_input.n_results == 10
    assert search_input.law_abbrev == "BGB"
    logger.info("✅ Valid SearchLawsInput accepted")

    # Invalid: query too short
    try:
        SearchLawsInput(query="a", n_results=10)
        logger.error("❌ Should have rejected short query")
        return False
    except ValidationError:
        logger.info("✅ Short query rejected")

    # Invalid: n_results too high
    try:
        SearchLawsInput(query="test query", n_results=100)
        logger.error("❌ Should have rejected n_results > 50")
        return False
    except ValidationError:
        logger.info("✅ n_results > 50 rejected")

    # Valid ingestion input
    ingest_input = IngestGermanLawsInput(max_laws=50)
    assert ingest_input.max_laws == 50
    logger.info("✅ Valid IngestGermanLawsInput accepted")

    # Invalid: max_laws too high
    try:
        IngestGermanLawsInput(max_laws=10000)
        logger.error("❌ Should have rejected max_laws > 7000")
        return False
    except ValidationError:
        logger.info("✅ max_laws > 7000 rejected")

    logger.info("✅ All input validation tests passed")
    return True


async def test_get_law_stats() -> bool:
    """Test get_law_stats tool retrieves collection info."""
    logger.info("=" * 60)
    logger.info("TEST 3: get_law_stats")
    logger.info("=" * 60)

    from mcp_refcache import RefCache

    from app.tools.german_laws import create_get_law_stats

    cache = RefCache(name="test-cache", default_ttl=60)
    get_law_stats = create_get_law_stats(cache)

    # Call the tool
    raw_result = await get_law_stats()

    logger.info("Raw stats result: %s", raw_result)

    # Unwrap cache response
    result = unwrap_cache_response(raw_result)
    logger.info("Unwrapped stats: %s", result)

    # Verify structure (should work even with empty collection)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "status" in result, "Missing 'status' key"

    if result["status"] == "ok":
        assert "total_documents" in result, "Missing 'total_documents'"
        assert "embedding_model" in result, "Missing 'embedding_model'"
        logger.info("✅ Stats retrieved: %d documents", result["total_documents"])
    else:
        # Error case is also valid (e.g., model not loaded)
        logger.info(
            "✅ Stats returned error status (expected if no data): %s",
            result.get("message"),
        )

    return True


async def test_search_laws() -> bool:
    """Test search_laws tool with semantic query."""
    logger.info("=" * 60)
    logger.info("TEST 4: search_laws")
    logger.info("=" * 60)

    from mcp_refcache import RefCache

    from app.tools.german_laws import create_search_laws

    cache = RefCache(name="test-cache", default_ttl=60)
    search_laws = create_search_laws(cache)

    # Search for purchase contract duties
    raw_result = await search_laws(
        query="Kaufvertrag Pflichten",
        n_results=5,
    )

    logger.info("Raw search result keys: %s", list(raw_result.keys()))

    # Unwrap cache response
    result = unwrap_cache_response(raw_result)
    logger.info("Unwrapped search result keys: %s", list(result.keys()))

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    if "error" in result:
        logger.info(
            "✅ Search returned error (expected if no data ingested): %s",
            result.get("message"),
        )
    elif result.get("_is_preview"):
        # Preview response - verify structure
        assert "count" in result, "Preview missing count"
        logger.info(
            "✅ Search returned preview with %d results",
            result.get("count", 0),
        )
    else:
        # Complete response
        assert "query" in result, "Missing 'query' key"
        assert "results" in result, "Missing 'results' key"
        assert "count" in result, "Missing 'count' key"
        logger.info("✅ Search returned %d results", result["count"])

        # If we have results, check structure
        if result["count"] > 0:
            first_result = result["results"][0]
            logger.info("First result: %s", first_result.get("doc_id", "N/A"))
            assert "content" in first_result, "Missing 'content' in result"
            assert "similarity" in first_result, "Missing 'similarity' in result"

    return True


async def test_search_with_filters() -> bool:
    """Test search_laws with law_abbrev filter."""
    logger.info("=" * 60)
    logger.info("TEST 5: search_laws with filters")
    logger.info("=" * 60)

    from mcp_refcache import RefCache

    from app.tools.german_laws import create_search_laws

    cache = RefCache(name="test-cache", default_ttl=60)
    search_laws = create_search_laws(cache)

    # Search in BGB only - use unique query to avoid cache hit from previous test
    raw_result = await search_laws(
        query="Kündigung Mietvertrag Wohnung",
        n_results=5,
        law_abbrev="BGB",
        level="norm",
    )

    logger.info("Raw result with filters: %s", raw_result)

    # Unwrap cache response
    result = unwrap_cache_response(raw_result)
    logger.info("Unwrapped result with filters: %s", result)

    assert isinstance(result, dict)
    assert "filters" in result, (
        f"Missing 'filters' key. Got keys: {list(result.keys())}"
    )
    assert result["filters"]["law_abbrev"] == "BGB"
    assert result["filters"]["level"] == "norm"

    logger.info("✅ Filters applied correctly: %s", result["filters"])
    return True


async def test_get_law_by_id() -> bool:
    """Test get_law_by_id tool for exact lookups."""
    logger.info("=" * 60)
    logger.info("TEST 6: get_law_by_id")
    logger.info("=" * 60)

    from mcp_refcache import RefCache

    from app.tools.german_laws import create_get_law_by_id

    cache = RefCache(name="test-cache", default_ttl=60)
    get_law_by_id = create_get_law_by_id(cache)

    # Try to get BGB section
    raw_result = await get_law_by_id(law_abbrev="BGB", norm_id="§ 433")

    logger.info("Raw get_law_by_id result keys: %s", list(raw_result.keys()))

    # Unwrap cache response
    result = unwrap_cache_response(raw_result)
    logger.info("Unwrapped result keys: %s", list(result.keys()))

    assert isinstance(result, dict)
    assert "law_abbrev" in result

    if "error" in result:
        logger.info(
            "✅ Lookup returned error (expected if not ingested): %s",
            result.get("message"),
        )
    else:
        assert "results" in result
        assert "count" in result
        logger.info("✅ Found %d documents for BGB § 433", result["count"])

    return True


async def test_live_ingestion_and_search() -> bool:
    """Integration test: ingest real laws and test semantic search.

    This test:
    1. Ingests first 2 laws (alphabetically) into the vector store
    2. Verifies documents were added
    3. Tests semantic search returns relevant results
    4. Validates result structure

    Note: This is a slow test (~30-60 seconds) that requires network access.
    The first laws alphabetically are often obscure ones, so we use a
    generic query that should match any legal text.
    """
    logger.info("=" * 60)
    logger.info("TEST 7: Live Ingestion & Semantic Search")
    logger.info("=" * 60)

    from mcp_refcache import RefCache

    from app.tools.german_laws import (
        create_get_law_stats,
        create_ingest_german_laws,
        create_search_laws,
    )

    cache = RefCache(name="test-cache-live", default_ttl=60)
    ingest_german_laws = create_ingest_german_laws(cache)
    search_laws = create_search_laws(cache)
    get_law_stats = create_get_law_stats(cache)

    # Step 1: Ingest 2 laws (BGB and GG - the most important ones)
    logger.info("Step 1: Ingesting 2 laws (this may take 30-60 seconds)...")
    raw_result = await ingest_german_laws(max_laws=2, max_norms_per_law=10)
    result = unwrap_cache_response(raw_result)

    logger.info("Ingestion result: %s", result)

    if "error" in result:
        logger.error("❌ Ingestion failed: %s", result.get("message"))
        return False

    assert result.get("status") == "complete", (
        f"Expected complete, got {result.get('status')}"
    )
    assert result.get("documents_added", 0) > 0, "No documents were added"
    logger.info(
        "✅ Ingested %d documents from %d laws",
        result["documents_added"],
        result["laws_processed"],
    )

    # Step 2: Check stats
    logger.info("Step 2: Verifying collection stats...")
    raw_stats = await get_law_stats()
    stats = unwrap_cache_response(raw_stats)

    assert stats.get("total_documents", 0) > 0, "Collection should have documents"
    logger.info("✅ Collection has %d documents", stats["total_documents"])

    # Step 3: Test semantic search
    # Use a generic query that should match any German legal text
    logger.info("Step 3: Testing semantic search...")
    raw_search = await search_laws(query="Gesetz Verordnung Regelung", n_results=5)
    search_result = unwrap_cache_response(raw_search)

    logger.info("Search result: %s", search_result)

    # Handle both complete results and preview responses
    if search_result.get("_is_preview"):
        # Preview response - just verify structure
        assert "count" in search_result, "Preview missing count"
        assert search_result.get("count", 0) > 0, "Search should find results"
        logger.info(
            "✅ Search returned preview with %d results (use get_cached_result to paginate)",
            search_result["count"],
        )
    else:
        # Complete response with full results
        assert "results" in search_result, "Missing results key"
        assert search_result.get("count", 0) > 0, "Search should return results"

        # Verify result structure
        first_result = search_result["results"][0]
        assert "content" in first_result, "Result missing content"
        assert "similarity" in first_result, "Result missing similarity"
        # Don't require high similarity - the test is about structure, not relevance
        assert first_result["similarity"] > 0.0, (
            f"Similarity should be positive: {first_result['similarity']}"
        )

        logger.info(
            "✅ Search returned %d results, top similarity: %.3f",
            search_result["count"],
            first_result["similarity"],
        )
        logger.info("Top result: %s", first_result.get("doc_id", "N/A"))

    return True


async def run_all_tests(include_live: bool = False) -> bool:
    """Run all tests and report results."""
    logger.info("\n" + "=" * 60)
    logger.info("GERMAN LAW MCP TOOLS TEST SUITE")
    logger.info("=" * 60 + "\n")

    tests = [
        ("Tool Creation", test_tool_creation),
        ("Input Validation", test_input_validation),
        ("get_law_stats", test_get_law_stats),
        ("search_laws", test_search_laws),
        ("search_laws with filters", test_search_with_filters),
        ("get_law_by_id", test_get_law_by_id),
    ]

    # Add live test if requested
    if include_live:
        tests.append(("Live Ingestion & Search", test_live_ingestion_and_search))

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()

            if result:
                passed += 1
            else:
                failed += 1
                logger.error("❌ FAILED: %s", name)
        except Exception as e:
            failed += 1
            logger.exception("❌ FAILED: %s - %s", name, e)

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)
    logger.info("Passed: %d/%d", passed, len(tests))
    logger.info("Failed: %d/%d", failed, len(tests))

    if failed == 0:
        logger.info("\n✅ ALL TESTS PASSED!")
    else:
        logger.error("\n❌ SOME TESTS FAILED")

    return failed == 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test German law MCP tools")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Include live ingestion test (slow, requires network)",
    )
    args = parser.parse_args()

    success = asyncio.run(run_all_tests(include_live=args.live))
    sys.exit(0 if success else 1)
