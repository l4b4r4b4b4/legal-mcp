"""Berlin state law portal: compatibility catalog tool (delegates to generic catalog).

This module provides the MCP tool factory `create_berlin_list_available_documents`,
kept for backwards compatibility. It **delegates** to the generic catalog tool
(`list_available_documents`) so callers can continue using the Berlin-specific
tool name while the underlying implementation becomes source-agnostic.

Compliance / safety:
- This tool performs **no network IO**.
- It reads from the bundled/committed offline catalog backing store.

Notes:
- Prefer the generic tool for new integrations.
- Document content retrieval remains on-demand elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.tools.catalog import DEFAULT_BERLIN_SOURCE, create_list_available_documents

if TYPE_CHECKING:
    from mcp_refcache import RefCache


# This module is intentionally minimal. The implementation lives in the generic
# catalog tool, and this file provides a backwards-compatible alias.


def create_berlin_list_available_documents(cache: RefCache) -> Any:
    """Create the Berlin compatibility alias tool.

    This delegates to the generic `list_available_documents` tool with the Berlin
    source pre-selected.

    Args:
        cache: RefCache instance passed through to the generic tool factory.

    Returns:
        A FastMCP-compatible tool function.
    """
    list_available_documents = create_list_available_documents(cache)

    async def berlin_list_available_documents(
        snapshot_path: str | None = None,
        prefix: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Compatibility alias for listing Berlin documents.

        Notes:
            `snapshot_path` is ignored in the new architecture because the catalog
            is served from a bundled SQLite database, not a JSON snapshot.

        Args:
            snapshot_path: Ignored (kept for backwards compatibility).
            prefix: Optional filter ("jlr"/"jlr-" or "NJRE").
            offset: Pagination offset (0-indexed).
            limit: Pagination limit.

        Returns:
            Same payload as `list_available_documents(...)`.
        """
        _ = snapshot_path
        return await list_available_documents(
            source=DEFAULT_BERLIN_SOURCE,
            prefix=prefix,
            offset=offset,
            limit=limit,
        )

    return berlin_list_available_documents


__all__ = [
    "create_berlin_list_available_documents",
]
