"""
Document loaders for Legal-MCP.

This module provides specialized loaders for legal documents from various sources,
designed to integrate with LangChain's document processing pipeline.
"""

from legal_mcp.loaders.discovery import (
    DiscoveryResult,
    GermanLawDiscovery,
    LawInfo,
    NormInfo,
    discover_laws_sync,
)
from legal_mcp.loaders.german_law_html import (
    GermanLawBulkHTMLLoader,
    GermanLawHTMLLoader,
    GermanLawNorm,
)

__all__ = [
    # Discovery
    "DiscoveryResult",
    # Loaders
    "GermanLawBulkHTMLLoader",
    "GermanLawDiscovery",
    "GermanLawHTMLLoader",
    "GermanLawNorm",
    "LawInfo",
    "NormInfo",
    "discover_laws_sync",
]
