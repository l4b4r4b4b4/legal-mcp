"""Generic MCP catalog tool (offline metadata lookup via bundled SQLite).

This module provides a generic MCP tool for listing *available* documents from a
bundled SQLite catalog. This is intended for situations where:
- You want to discover what documents exist (IDs + canonical URLs + type prefix)
- You do NOT want network IO at runtime (compliance / determinism)
- Document *content* retrieval is handled separately (on-demand)

The catalog database is expected to be built at dev time (e.g., from sitemap
discovery snapshots) and committed to the repository/package.

Tool:
- `list_available_documents(source, prefix=None, offset=0, limit=50)`

Notes:
- This tool intentionally does not use RefCache caching decorators. The payload
  is already bounded (limit <= 200) and deterministic.
- The tool returns structured error dictionaries rather than raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from app.catalog.store import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    CatalogCorruptError,
    CatalogNotFoundError,
    CatalogRegistry,
    CatalogSource,
    CatalogStore,
    UnknownCatalogSourceError,
    get_default_catalog_data_directory,
    require_catalog_file_is_present,
)

if TYPE_CHECKING:
    from mcp_refcache import RefCache


DEFAULT_BERLIN_SOURCE = "de-state-berlin-bsbe"
DEFAULT_BERLIN_CATALOG_FILENAME = "de_state_berlin_bsbe.sqlite"
DEFAULT_BERLIN_CATALOG_VERSION = "dev"


class ListAvailableDocumentsInput(BaseModel):
    """Input validation for list_available_documents."""

    source: str = Field(
        description="Catalog source identifier (e.g., 'de-state-berlin-bsbe').",
        min_length=1,
    )
    prefix: str | None = Field(
        default=None,
        description=(
            "Optional prefix filter. Semantics are source-specific "
            "(e.g., Berlin: 'jlr'/'jlr-' or 'NJRE')."
        ),
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Pagination offset (0-indexed).",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Pagination limit (max {MAX_LIMIT}).",
    )


def _normalize_prefix_for_source(source: str, prefix: str | None) -> str | None:
    """Normalize a prefix filter value based on the source semantics.

    For Berlin, accept:
    - "jlr" or "jlr-" (case-insensitive) -> "jlr"
    - "NJRE" (case-insensitive) -> "NJRE"

    For unknown sources, pass-through (trim) without further validation.

    Args:
        source: Catalog source identifier.
        prefix: Raw prefix filter.

    Returns:
        Normalized prefix string or None.

    Raises:
        ValueError: If a known source receives an unsupported prefix.
    """
    if prefix is None:
        return None

    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return None

    if source == DEFAULT_BERLIN_SOURCE:
        if normalized_prefix.lower().startswith("jlr"):
            return "jlr"
        if normalized_prefix.upper().startswith("NJRE"):
            return "NJRE"
        raise ValueError("prefix must be one of: 'jlr'/'jlr-' or 'NJRE'")

    # Generic pass-through for other sources (trim only).
    return normalized_prefix


def _create_default_registry() -> CatalogRegistry:
    """Create a default catalog registry.

    This registers known sources with the expected bundled SQLite filenames.
    """
    registry = CatalogRegistry()

    catalog_data_directory = get_default_catalog_data_directory()
    berlin_sqlite_path = catalog_data_directory / DEFAULT_BERLIN_CATALOG_FILENAME

    registry.register(
        CatalogSource(
            source=DEFAULT_BERLIN_SOURCE,
            sqlite_path=berlin_sqlite_path,
            catalog_version=DEFAULT_BERLIN_CATALOG_VERSION,
        )
    )

    return registry


def create_list_available_documents(cache: RefCache) -> Any:
    """Create the generic `list_available_documents` MCP tool.

    Args:
        cache: RefCache instance (kept for signature consistency with other
            tool factories; not used by this tool).

    Returns:
        A FastMCP-compatible tool function.

    Tool behavior:
        - No network IO.
        - Reads from a bundled, repo-committed SQLite catalog.
        - Returns a structured dict (or structured error dict).
    """
    registry = _create_default_registry()

    async def list_available_documents(
        source: str,
        prefix: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List available documents from the bundled catalog (offline).

        Args:
            source: Catalog source identifier (e.g., 'de-state-berlin-bsbe').
            prefix: Optional prefix filter (source-specific).
            offset: Pagination offset (0-indexed).
            limit: Pagination limit (default 50, max 200).

        Returns:
            Dict containing counts and paginated items.

        Example:
            >>> list_available_documents(
            ...     source="de-state-berlin-bsbe",
            ...     prefix="jlr",
            ...     offset=0,
            ...     limit=25,
            ... )
        """
        try:
            validated = ListAvailableDocumentsInput(
                source=source,
                prefix=prefix,
                offset=offset,
                limit=limit,
            )
        except ValidationError as exception:
            return {
                "error": "Invalid input",
                "message": str(exception)[:500],
                "source": source,
            }

        try:
            normalized_prefix = _normalize_prefix_for_source(
                validated.source, validated.prefix
            )
        except ValueError as exception:
            return {
                "error": "Invalid prefix",
                "message": str(exception)[:500],
                "source": validated.source,
                "prefix": validated.prefix,
            }

        try:
            catalog_source = registry.get(validated.source)
        except UnknownCatalogSourceError as exception:
            return {
                "error": "Unknown source",
                "message": str(exception)[:500],
                "source": validated.source,
                "available_sources": registry.list_sources(),
            }

        sqlite_path = Path(catalog_source.sqlite_path)
        try:
            require_catalog_file_is_present(sqlite_path)
            store = CatalogStore(sqlite_path=sqlite_path)
            query_result = store.query_documents(
                source=catalog_source.source,
                prefix=normalized_prefix,
                offset=validated.offset,
                limit=validated.limit,
                catalog_version=catalog_source.catalog_version,
            )
            return query_result.to_dict()
        except CatalogNotFoundError as exception:
            return {
                "error": "Catalog not found",
                "message": str(exception)[:500],
                "source": catalog_source.source,
                "sqlite_path": str(sqlite_path),
            }
        except CatalogCorruptError as exception:
            return {
                "error": "Catalog invalid",
                "message": str(exception)[:500],
                "source": catalog_source.source,
                "sqlite_path": str(sqlite_path),
            }
        except Exception as exception:
            return {
                "error": "Catalog query failed",
                "message": str(exception)[:500],
                "source": catalog_source.source,
                "sqlite_path": str(sqlite_path),
            }

    return list_available_documents


__all__ = [
    "ListAvailableDocumentsInput",
    "create_list_available_documents",
]
