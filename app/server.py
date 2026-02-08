"""Legal-MCP - FastMCP Server with RefCache.

This module creates and configures the FastMCP server, wiring together
tools from the modular tools package.

Includes a /health HTTP endpoint for Kubernetes liveness/readiness probes.

Features:
- Reference-based caching for large results
- Preview generation (sample, truncate, paginate strategies)
- Pagination for accessing large datasets
- Access control (user vs agent permissions)
- Private computation (EXECUTE without READ)
- Berlin portal (gesetze.berlin.de) local snapshot catalog (no network IO)


Usage:
    # Run with typer CLI
    uvx legal-mcp stdio           # Local CLI mode
    uvx legal-mcp streamable-http # Remote/Docker mode

    # Or with uv
    uv run legal-mcp stdio
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from mcp_refcache import PreviewConfig, PreviewStrategy, RefCache
from mcp_refcache.fastmcp import cache_instructions, register_admin_tools
from starlette.responses import JSONResponse, Response

from app.prompts import template_guide
from app.tools import (
    create_berlin_list_available_documents,
    create_compute_with_secret,
    create_convert_files_to_markdown,
    create_get_cached_result,
    create_get_law_by_id,
    create_get_law_stats,
    create_health_check,
    create_ingest_documents,
    create_ingest_markdown_files,
    create_ingest_pdf_files,
    create_list_available_documents,
    create_search_documents,
    create_search_laws,
    create_store_secret,
)

if TYPE_CHECKING:
    from starlette.requests import Request

# =============================================================================
# Initialize FastMCP Server
# =============================================================================

mcp = FastMCP(
    name="Legal-MCP",
    instructions=f"""A comprehensive legal research MCP server built with FastMCP and mcp-refcache, providing AI assistants with structured access to legal information across multiple jurisdictions.

## German Law Tools

- search_laws: Semantic search across German federal laws (BGB, StGB, GG, etc.)
- get_law_by_id: Lookup specific law sections by abbreviation and norm ID
- get_law_stats: Get collection statistics and model status

## Custom Document Tools

These tools support ingesting and searching your own case files / documents.
Isolation is enforced via `tenant_id` (required). Optionally scope further with `case_id`.

- ingest_documents: Ingest custom plain-text documents
- ingest_markdown_files: Ingest Markdown files from disk under an allowlisted root (set LEGAL_MCP_INGEST_ROOT)
- convert_files_to_markdown: Convert allowlisted files (e.g., PDFs) on disk to Markdown/text
- ingest_pdf_files: Ingest PDFs from disk under an allowlisted root (convert → ingest)
- search_documents: Semantic search over ingested custom documents with filters (tenant_id required)

## Cache Tools

- get_cached_result: Retrieve or paginate through cached results (also for polling async jobs)

## Secret Tools

- store_secret: Store a secret value for private computation
- compute_with_secret: Use a secret in computation without revealing it

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

# German law tools
search_laws = create_search_laws(cache)
get_law_by_id = create_get_law_by_id(cache)
get_law_stats = create_get_law_stats(cache)

# Catalog tools (offline; no network IO)
list_available_documents = create_list_available_documents(cache)

# Berlin tools (compatibility alias; delegates to generic catalog)
berlin_list_available_documents = create_berlin_list_available_documents(cache)


# Custom document tools
ingest_documents = create_ingest_documents(cache)
ingest_markdown_files = create_ingest_markdown_files(cache)
convert_files_to_markdown = create_convert_files_to_markdown(cache)
ingest_pdf_files = create_ingest_pdf_files(cache)
search_documents = create_search_documents(cache)

# =============================================================================
# Register Tools
# =============================================================================

# Cache-bound tools (using pre-created module-level functions)
mcp.tool(store_secret)
mcp.tool(compute_with_secret)
mcp.tool(get_cached_result)
mcp.tool(health_check)

# German law tools
mcp.tool(search_laws)
mcp.tool(get_law_by_id)
mcp.tool(get_law_stats)

# Catalog tools (offline; no network IO)
mcp.tool(list_available_documents)

# Berlin tools (compatibility alias; delegates to generic catalog)
mcp.tool(berlin_list_available_documents)


# Custom document tools
mcp.tool(ingest_documents)
mcp.tool(ingest_markdown_files)
mcp.tool(convert_files_to_markdown)
mcp.tool(ingest_pdf_files)
mcp.tool(search_documents)

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


# =============================================================================
# Health Check HTTP Endpoint (for Kubernetes probes)
# =============================================================================


@mcp.custom_route("/health", methods=["GET"])
async def http_health_check(request: Request) -> Response:
    """HTTP health check endpoint for Kubernetes liveness/readiness probes.

    Returns:
        JSON response with health status and basic server info.
    """
    return JSONResponse(
        {
            "status": "healthy",
            "server": "Legal-MCP",
            "cache": {
                "name": _cache.name,
            },
        },
        status_code=200,
    )
