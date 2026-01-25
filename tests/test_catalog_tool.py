"""Tests for the generic SQLite-backed catalog tool and Berlin alias behavior."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from mcp_refcache import PreviewConfig, PreviewStrategy, RefCache, SizeMode

from app.tools.catalog import DEFAULT_BERLIN_SOURCE, create_list_available_documents
from app.tools.de_state.berlin.catalog import create_berlin_list_available_documents

if TYPE_CHECKING:
    from pathlib import Path


def _create_sqlite_catalog(sqlite_path: Path) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(sqlite_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                source TEXT NOT NULL,
                document_id TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                document_type_prefix TEXT NOT NULL,
                PRIMARY KEY (source, document_id)
            );

            CREATE INDEX IF NOT EXISTS idx_documents_source_prefix_id
                ON documents(source, document_type_prefix, document_id);

            CREATE INDEX IF NOT EXISTS idx_documents_source_id
                ON documents(source, document_id);
            """
        )

        rows = [
            (
                DEFAULT_BERLIN_SOURCE,
                "NJRE000000001",
                "https://gesetze.berlin.de/bsbe/document/NJRE000000001",
                "NJRE",
            ),
            (
                DEFAULT_BERLIN_SOURCE,
                "NJRE000000002",
                "https://gesetze.berlin.de/bsbe/document/NJRE000000002",
                "NJRE",
            ),
            (
                DEFAULT_BERLIN_SOURCE,
                "jlr-TestNormVBEpP1",
                "https://gesetze.berlin.de/bsbe/document/jlr-TestNormVBEpP1",
                "jlr",
            ),
            (
                DEFAULT_BERLIN_SOURCE,
                "jlr-TestNormVBEpP2",
                "https://gesetze.berlin.de/bsbe/document/jlr-TestNormVBEpP2",
                "jlr",
            ),
        ]
        connection.executemany(
            """
            INSERT OR REPLACE INTO documents (
                source, document_id, canonical_url, document_type_prefix
            ) VALUES (?, ?, ?, ?);
            """,
            rows,
        )
        connection.commit()


@pytest.fixture
def cache() -> RefCache:
    """Create a fresh RefCache for tool binding (keeps tests isolated)."""
    test_cache = RefCache(
        name="test_catalog_tool",
        default_ttl=3600,
        preview_config=PreviewConfig(
            size_mode=SizeMode.CHARACTER,
            max_size=500,
            default_strategy=PreviewStrategy.SAMPLE,
        ),
    )
    yield test_cache
    test_cache.clear()


@pytest.fixture
def sqlite_catalog(tmp_path: Path) -> Path:
    """Create a temporary SQLite catalog DB at the expected default runtime path.

    The tool uses `app/catalog_data/de_state_berlin_bsbe.sqlite` by default.
    For tests, we create that path under a temporary CWD and chdir into it.
    """
    sqlite_path = tmp_path / "app" / "catalog_data" / "de_state_berlin_bsbe.sqlite"
    _create_sqlite_catalog(sqlite_path)
    return sqlite_path


@pytest.fixture
def chdir_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Change working directory to tmp_path for deterministic relative path resolution."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def list_tool(cache: RefCache):
    """Create the generic list_available_documents tool function."""
    tool = create_list_available_documents(cache)
    return tool.fn if hasattr(tool, "fn") else tool


@pytest.fixture
def berlin_alias_tool(cache: RefCache):
    """Create the berlin_list_available_documents alias tool function."""
    tool = create_berlin_list_available_documents(cache)
    return tool.fn if hasattr(tool, "fn") else tool


@pytest.mark.asyncio
async def test_generic_tool_lists_documents_with_counts(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    list_tool,
) -> None:
    result = await list_tool(source=DEFAULT_BERLIN_SOURCE, offset=0, limit=50)

    assert "error" not in result
    assert result["source"] == DEFAULT_BERLIN_SOURCE
    assert result["count_total"] == 4
    assert result["count_filtered"] == 4
    assert result["prefix_counts"]["NJRE"] == 2
    assert result["prefix_counts"]["jlr"] == 2
    assert result["prefix_counts"]["other"] == 0

    items = result["items"]
    assert len(items) == 4
    document_ids = [item["document_id"] for item in items]
    assert document_ids == sorted(document_ids)


@pytest.mark.asyncio
async def test_generic_tool_prefix_filter_jlr(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    list_tool,
) -> None:
    result = await list_tool(
        source=DEFAULT_BERLIN_SOURCE, prefix="jlr", offset=0, limit=50
    )

    assert "error" not in result
    assert result["prefix"] == "jlr"
    assert result["count_total"] == 4
    assert result["count_filtered"] == 2
    assert len(result["items"]) == 2

    for item in result["items"]:
        assert item["document_type_prefix"] == "jlr"
        assert item["document_id"].lower().startswith("jlr")


@pytest.mark.asyncio
async def test_generic_tool_prefix_filter_njre(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    list_tool,
) -> None:
    result = await list_tool(
        source=DEFAULT_BERLIN_SOURCE, prefix="NJRE", offset=0, limit=50
    )

    assert "error" not in result
    assert result["prefix"] == "NJRE"
    assert result["count_total"] == 4
    assert result["count_filtered"] == 2
    assert len(result["items"]) == 2

    for item in result["items"]:
        assert item["document_type_prefix"] == "NJRE"
        assert item["document_id"].startswith("NJRE")


@pytest.mark.asyncio
async def test_generic_tool_pagination(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    list_tool,
) -> None:
    page_1 = await list_tool(source=DEFAULT_BERLIN_SOURCE, offset=0, limit=2)
    page_2 = await list_tool(source=DEFAULT_BERLIN_SOURCE, offset=2, limit=2)

    assert "error" not in page_1
    assert "error" not in page_2
    assert len(page_1["items"]) == 2
    assert len(page_2["items"]) == 2

    ids_1 = [item["document_id"] for item in page_1["items"]]
    ids_2 = [item["document_id"] for item in page_2["items"]]
    assert set(ids_1).isdisjoint(ids_2)

    assert ids_1 + ids_2 == sorted(ids_1 + ids_2)


@pytest.mark.asyncio
async def test_generic_tool_invalid_prefix_returns_structured_error(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    list_tool,
) -> None:
    result = await list_tool(
        source=DEFAULT_BERLIN_SOURCE, prefix="bogus", offset=0, limit=50
    )

    assert result["error"] == "Invalid prefix"
    assert result["source"] == DEFAULT_BERLIN_SOURCE
    assert result["prefix"] == "bogus"


@pytest.mark.asyncio
async def test_generic_tool_unknown_source_returns_structured_error(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    list_tool,
) -> None:
    result = await list_tool(source="unknown-source", offset=0, limit=50)

    assert result["error"] == "Unknown source"
    assert result["source"] == "unknown-source"
    assert "available_sources" in result
    assert DEFAULT_BERLIN_SOURCE in result["available_sources"]


@pytest.mark.asyncio
async def test_generic_tool_missing_db_returns_structured_error(
    chdir_to_tmp: None,
    list_tool,
) -> None:
    # No sqlite_catalog fixture -> DB not created under app/catalog_data/
    result = await list_tool(source=DEFAULT_BERLIN_SOURCE, offset=0, limit=50)

    assert result["error"] == "Catalog not found"
    assert result["source"] == DEFAULT_BERLIN_SOURCE
    assert "sqlite_path" in result


@pytest.mark.asyncio
async def test_berlin_alias_delegates_to_generic_tool(
    chdir_to_tmp: None,
    sqlite_catalog: Path,
    berlin_alias_tool,
) -> None:
    # snapshot_path is intentionally ignored (kept for backwards compatibility)
    result = await berlin_alias_tool(
        snapshot_path="ignored.json", prefix="jlr", offset=0, limit=50
    )

    assert "error" not in result
    assert result["source"] == DEFAULT_BERLIN_SOURCE
    assert result["prefix"] == "jlr"
    assert result["count_filtered"] == 2
    assert all(item["document_type_prefix"] == "jlr" for item in result["items"])
