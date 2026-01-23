"""Tools module for Legal-MCP.

This module re-exports all tools from submodules for convenient access.

Tool Modules:
- secrets: Private computation with secrets
- cache: Cache query and retrieval
- health: Health check functionality
"""

from __future__ import annotations

from app.tools.cache import CacheQueryInput, create_get_cached_result
from app.tools.health import create_health_check
from app.tools.secrets import (
    SecretComputeInput,
    SecretInput,
    create_compute_with_secret,
    create_store_secret,
)

__all__ = [
    "CacheQueryInput",
    "SecretComputeInput",
    "SecretInput",
    "create_compute_with_secret",
    "create_get_cached_result",
    "create_health_check",
    "create_store_secret",
]
