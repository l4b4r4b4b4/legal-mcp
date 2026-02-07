"""Tools module for Legal-MCP.

This module re-exports MCP tools from submodules for convenient access.

Tool Modules:
- secrets: Private computation with secrets
- cache: Cache query and retrieval
- health: Health check functionality
- german_laws: German law semantic search and lookup tools
- custom_documents: Custom document ingestion and semantic search tools
- catalog: Offline, SQLite-backed document availability catalog
"""

from __future__ import annotations

from app.tools.cache import CacheQueryInput, create_get_cached_result
from app.tools.catalog import (
    ListAvailableDocumentsInput,
    create_list_available_documents,
)
from app.tools.custom_documents import (
    ConvertFilesToMarkdownInput,
    IngestChunkingOptions,
    IngestDocumentItem,
    IngestDocumentsInput,
    IngestMarkdownFilesInput,
    IngestPdfFilesInput,
    SearchDocumentsInput,
    create_convert_files_to_markdown,
    create_ingest_documents,
    create_ingest_markdown_files,
    create_ingest_pdf_files,
    create_search_documents,
)
from app.tools.de_state.berlin.catalog import create_berlin_list_available_documents
from app.tools.german_laws import (
    IngestGermanLawsInput,
    SearchLawsInput,
    create_get_law_by_id,
    create_get_law_stats,
    create_search_laws,
)
from app.tools.health import create_health_check
from app.tools.secrets import (
    SecretComputeInput,
    SecretInput,
    create_compute_with_secret,
    create_store_secret,
)

__all__ = [
    "CacheQueryInput",
    "ConvertFilesToMarkdownInput",
    "IngestChunkingOptions",
    "IngestDocumentItem",
    "IngestDocumentsInput",
    "IngestGermanLawsInput",
    "IngestMarkdownFilesInput",
    "IngestPdfFilesInput",
    "ListAvailableDocumentsInput",
    "SearchDocumentsInput",
    "SearchLawsInput",
    "SecretComputeInput",
    "SecretInput",
    "create_berlin_list_available_documents",
    "create_compute_with_secret",
    "create_convert_files_to_markdown",
    "create_get_cached_result",
    "create_get_law_by_id",
    "create_get_law_stats",
    "create_health_check",
    "create_ingest_documents",
    "create_ingest_markdown_files",
    "create_ingest_pdf_files",
    "create_list_available_documents",
    "create_search_documents",
    "create_search_laws",
    "create_store_secret",
]
