"""Legal-MCP - FastMCP Server with RefCache.

This module creates and configures the FastMCP server, wiring together
tools from the modular tools package.

Features:
- Reference-based caching for large results
- Preview generation (sample, truncate, paginate strategies)
- Pagination for accessing large datasets
- Access control (user vs agent permissions)
- Private computation (EXECUTE without READ)


Usage:
    # Run with typer CLI
    uvx legal-mcp stdio           # Local CLI mode
    uvx legal-mcp streamable-http # Remote/Docker mode

    # Or with uv
    uv run legal-mcp stdio
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from mcp_refcache import PreviewConfig, PreviewStrategy, RefCache
from mcp_refcache.fastmcp import cache_instructions, register_admin_tools

from app.prompts import template_guide
from app.tools import (
    create_compute_with_secret,
    create_get_cached_result,
    create_health_check,
    create_store_secret,
)

# =============================================================================
# Initialize FastMCP Server
# =============================================================================

mcp = FastMCP(
    name="Legal-MCP",
    instructions=f"""A comprehensive legal research MCP server built with FastMCP and mcp-refcache, providing AI assistants with structured access to legal information across multiple jurisdictions.


Available tools:


- store_secret: Store a secret value for private computation
- compute_with_secret: Use a secret in computation without revealing it

- get_cached_result: Retrieve or paginate through cached results


{cache_instructions()}
""",
)

# =============================================================================
# Initialize RefCache
# =============================================================================

# Create the base RefCache instance
_cache = RefCache(
    name="legal-mcp",
    default_ttl=3600,  # 1 hour TTL
    preview_config=PreviewConfig(
        max_size=2048,  # Max 2048 tokens in previews
        default_strategy=PreviewStrategy.SAMPLE,  # Sample large collections
    ),
)

# Use RefCache directly (no tracing)
cache = _cache

# =============================================================================
# Create Bound Tool Functions
# =============================================================================

# These are created with factory functions and bound to the cache instance.
# We keep references for testing and re-export them as module attributes.
store_secret = create_store_secret(cache)
compute_with_secret = create_compute_with_secret(cache)
get_cached_result = create_get_cached_result(cache)
health_check = create_health_check(_cache)

# =============================================================================
# Register Tools
# =============================================================================

# Cache-bound tools (using pre-created module-level functions)
mcp.tool(store_secret)
mcp.tool(compute_with_secret)
mcp.tool(get_cached_result)
mcp.tool(health_check)

# =============================================================================
# Admin Tools (Permission-Gated)
# =============================================================================


async def is_admin(ctx: Any) -> bool:
    """Check if the current context has admin privileges.

    Override this in your own server with proper auth logic.
    """
    # Demo: No admin access by default
    return False


# Register admin tools with the underlying cache (not the traced wrapper)
_admin_tools = register_admin_tools(
    mcp,
    _cache,
    admin_check=is_admin,
    prefix="admin_",
    include_dangerous=False,
)

# =============================================================================
# Register Prompts
# =============================================================================


@mcp.prompt
def _template_guide() -> str:
    """Guide for using this MCP server template."""
    return template_guide()
