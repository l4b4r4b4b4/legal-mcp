"""SQLite-backed document catalog store (offline metadata lookup).

This module implements a small catalog layer used by MCP tools to list
available documents (IDs + canonical URLs + derived type prefix) without any
network IO.

Design goals:
- Deterministic, bounded queries (offset/limit)
- Fast pagination via SQLite indexes (no full table scans for typical queries)
- Multiple sources via a simple registry (e.g., de-state-berlin-bsbe)
- Safe, structured errors suitable for MCP tool returns

The catalog database is intended to be *bundled and committed* as part of the
repository/package. Document *content* retrieval remains on-demand elsewhere.

Database schema (required):
- table: `documents`
    - source TEXT NOT NULL
    - document_id TEXT NOT NULL
    - canonical_url TEXT NOT NULL
    - document_type_prefix TEXT NOT NULL  ("jlr" | "NJRE" | "other" or other source-specific)
- constraints:
    - PRIMARY KEY (source, document_id)

Recommended indexes (for performance):
- CREATE INDEX idx_documents_source_prefix_id ON documents(source, document_type_prefix, document_id);
- CREATE INDEX idx_documents_source_id ON documents(source, document_id);

The store does not enforce a particular document_type_prefix vocabulary beyond
being a string; tools may validate expected prefixes per source.

Public API:
- `CatalogStore`: open/validate/query
- `CatalogRegistry`: map source -> sqlite path + version
- `CatalogQueryResult`: structured result payload (counts + items list)
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class CatalogError(RuntimeError):
    """Base error for catalog operations."""


class CatalogNotFoundError(CatalogError):
    """Raised when a configured catalog database file is missing."""


class CatalogCorruptError(CatalogError):
    """Raised when a catalog database cannot be opened or validated."""


class UnknownCatalogSourceError(CatalogError):
    """Raised when a requested catalog source is not registered."""


@dataclass(frozen=True, slots=True)
class CatalogSource:
    """Describes a catalog source and its backing SQLite DB.

    Attributes:
        source: Stable source identifier; used as tool input.
        sqlite_path: Path to the SQLite database file.
        catalog_version: Version marker for the DB contents (build timestamp, git SHA, etc.).
    """

    source: str
    sqlite_path: Path
    catalog_version: str


@dataclass(frozen=True, slots=True)
class CatalogDocumentItem:
    """One catalog entry returned by a query."""

    document_id: str
    canonical_url: str
    document_type_prefix: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        return {
            "document_id": self.document_id,
            "canonical_url": self.canonical_url,
            "document_type_prefix": self.document_type_prefix,
        }


@dataclass(frozen=True, slots=True)
class CatalogQueryResult:
    """Structured query result for MCP tool responses."""

    source: str
    catalog_version: str
    prefix: str | None
    offset: int
    limit: int
    count_total: int
    count_filtered: int
    prefix_counts: dict[str, int]
    items: list[CatalogDocumentItem]

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        return {
            "source": self.source,
            "catalog_version": self.catalog_version,
            "prefix": self.prefix,
            "offset": self.offset,
            "limit": self.limit,
            "count_total": self.count_total,
            "count_filtered": self.count_filtered,
            "prefix_counts": dict(self.prefix_counts),
            "items": [item.to_dict() for item in self.items],
        }


class CatalogRegistry:
    """Registry of catalog sources.

    This is intended to be configured at server startup, mapping stable source
    IDs to bundled SQLite databases.

    The registry does not open databases; it only stores configuration.
    """

    def __init__(self) -> None:
        self._sources_by_name: dict[str, CatalogSource] = {}

    def register(self, catalog_source: CatalogSource) -> None:
        """Register a catalog source.

        Args:
            catalog_source: CatalogSource to register.

        Raises:
            ValueError: If the source identifier is empty or already registered.
        """
        if not catalog_source.source or not catalog_source.source.strip():
            raise ValueError("catalog source must be a non-empty string")
        normalized_source = catalog_source.source.strip()
        if normalized_source in self._sources_by_name:
            raise ValueError(f"catalog source already registered: {normalized_source}")
        self._sources_by_name[normalized_source] = CatalogSource(
            source=normalized_source,
            sqlite_path=catalog_source.sqlite_path,
            catalog_version=catalog_source.catalog_version,
        )

    def get(self, source: str) -> CatalogSource:
        """Get a registered source.

        Args:
            source: Source identifier.

        Returns:
            CatalogSource configuration.

        Raises:
            UnknownCatalogSourceError: If not registered.
        """
        normalized_source = source.strip()
        if normalized_source not in self._sources_by_name:
            raise UnknownCatalogSourceError(
                f"Unknown catalog source: {normalized_source}"
            )
        return self._sources_by_name[normalized_source]

    def list_sources(self) -> list[str]:
        """List registered source identifiers (sorted)."""
        return sorted(self._sources_by_name.keys())


class CatalogStore:
    """SQLite-backed catalog store.

    This store is a thin wrapper around SQLite reads and schema validation.
    Each CatalogStore instance is configured for a single SQLite DB file and
    provides query methods for one or more sources housed within that DB.

    The database is opened per operation to keep behavior simple and robust.
    For higher throughput, this can be extended to use a connection pool.
    """

    def __init__(self, sqlite_path: Path) -> None:
        self._sqlite_path = sqlite_path

    @property
    def sqlite_path(self) -> Path:
        """Path to the SQLite database file."""
        return self._sqlite_path

    def validate(self) -> None:
        """Validate that the database exists and has the expected schema.

        Raises:
            CatalogNotFoundError: If the file does not exist.
            CatalogCorruptError: If the file cannot be opened or schema is invalid.
        """
        if not self._sqlite_path.exists():
            raise CatalogNotFoundError(
                f"Catalog database not found: {self._sqlite_path}"
            )

        try:
            with self._connect() as connection:
                self._validate_schema(connection)
        except CatalogError:
            raise
        except (sqlite3.DatabaseError, OSError) as exception:
            raise CatalogCorruptError(
                f"Catalog database could not be opened/validated: {self._sqlite_path}: {exception}"
            ) from exception

    def query_documents(
        self,
        *,
        source: str,
        prefix: str | None,
        offset: int,
        limit: int,
        catalog_version: str,
    ) -> CatalogQueryResult:
        """Query documents for a given source with optional prefix filtering.

        Args:
            source: Catalog source identifier stored in `documents.source`.
            prefix: Optional prefix filter (exact match in `document_type_prefix`).
            offset: 0-indexed offset.
            limit: Max rows to return (bounded).
            catalog_version: Version marker included in the response.

        Returns:
            CatalogQueryResult containing counts and paginated `items`.

        Raises:
            ValueError: For invalid offset/limit.
            CatalogNotFoundError: If DB is missing.
            CatalogCorruptError: If DB is corrupt or schema missing.
        """
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit <= 0:
            raise ValueError("limit must be > 0")
        if limit > MAX_LIMIT:
            raise ValueError(f"limit must be <= {MAX_LIMIT}")

        self.validate()

        normalized_source = source.strip()
        normalized_prefix = prefix.strip() if prefix is not None else None
        if normalized_prefix == "":
            normalized_prefix = None

        try:
            with self._connect() as connection:
                self._validate_schema(connection)

                count_total = self._count_total(connection, normalized_source)
                prefix_counts = self._count_prefixes(connection, normalized_source)

                if normalized_prefix is None:
                    count_filtered = count_total
                else:
                    count_filtered = self._count_filtered(
                        connection, normalized_source, normalized_prefix
                    )

                items = self._fetch_items(
                    connection,
                    source=normalized_source,
                    prefix=normalized_prefix,
                    offset=offset,
                    limit=limit,
                )

                return CatalogQueryResult(
                    source=normalized_source,
                    catalog_version=catalog_version,
                    prefix=normalized_prefix,
                    offset=offset,
                    limit=limit,
                    count_total=count_total,
                    count_filtered=count_filtered,
                    prefix_counts=prefix_counts,
                    items=items,
                )
        except CatalogError:
            raise
        except (sqlite3.DatabaseError, OSError) as exception:
            raise CatalogCorruptError(
                f"Catalog query failed for {self._sqlite_path}: {exception}"
            ) from exception

    def _connect(self) -> sqlite3.Connection:
        # Use immutable query mode where supported to protect against accidental writes.
        # If the local SQLite build doesn't support URI mode, fall back to normal open.
        try:
            uri = f"file:{self._sqlite_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except Exception:
            connection = sqlite3.connect(self._sqlite_path, check_same_thread=False)

        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
        )
        row = cursor.fetchone()
        if row is None:
            raise CatalogCorruptError(
                "Catalog database missing required table: documents"
            )

        cursor = connection.execute("PRAGMA table_info(documents)")
        columns = {r["name"] for r in cursor.fetchall()}

        required_columns = {
            "source",
            "document_id",
            "canonical_url",
            "document_type_prefix",
        }
        missing = required_columns - columns
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise CatalogCorruptError(
                f"Catalog database 'documents' table missing column(s): {missing_list}"
            )

    @staticmethod
    def _count_total(connection: sqlite3.Connection, source: str) -> int:
        cursor = connection.execute(
            "SELECT COUNT(*) AS count_total FROM documents WHERE source = ?",
            (source,),
        )
        row = cursor.fetchone()
        return int(row["count_total"]) if row is not None else 0

    @staticmethod
    def _count_filtered(
        connection: sqlite3.Connection, source: str, prefix: str
    ) -> int:
        cursor = connection.execute(
            "SELECT COUNT(*) AS count_filtered FROM documents WHERE source = ? AND document_type_prefix = ?",
            (source, prefix),
        )
        row = cursor.fetchone()
        return int(row["count_filtered"]) if row is not None else 0

    @staticmethod
    def _count_prefixes(connection: sqlite3.Connection, source: str) -> dict[str, int]:
        cursor = connection.execute(
            "SELECT document_type_prefix, COUNT(*) AS count_prefix "
            "FROM documents WHERE source = ? GROUP BY document_type_prefix",
            (source,),
        )
        counts: dict[str, int] = {}
        for row in cursor.fetchall():
            prefix_value = str(row["document_type_prefix"])
            counts[prefix_value] = int(row["count_prefix"])
        # Ensure stable keys for known Berlin prefixes to reduce surprise in callers.
        # This does not prevent other sources from using different prefixes.
        for expected in ("jlr", "NJRE", "other"):
            counts.setdefault(expected, 0)
        return counts

    @staticmethod
    def _fetch_items(
        connection: sqlite3.Connection,
        *,
        source: str,
        prefix: str | None,
        offset: int,
        limit: int,
    ) -> list[CatalogDocumentItem]:
        parameters: tuple[Any, ...]
        if prefix is None:
            sql = (
                "SELECT document_id, canonical_url, document_type_prefix "
                "FROM documents WHERE source = ? "
                "ORDER BY document_id ASC "
                "LIMIT ? OFFSET ?"
            )
            parameters = (source, limit, offset)
        else:
            sql = (
                "SELECT document_id, canonical_url, document_type_prefix "
                "FROM documents WHERE source = ? AND document_type_prefix = ? "
                "ORDER BY document_id ASC "
                "LIMIT ? OFFSET ?"
            )
            parameters = (source, prefix, limit, offset)

        cursor = connection.execute(sql, parameters)
        items: list[CatalogDocumentItem] = []
        for row in cursor.fetchall():
            items.append(
                CatalogDocumentItem(
                    document_id=str(row["document_id"]),
                    canonical_url=str(row["canonical_url"]),
                    document_type_prefix=str(row["document_type_prefix"]),
                )
            )
        return items


def get_default_catalog_data_directory() -> Path:
    """Return the default directory for bundled catalog data files.

    This is intentionally a simple helper. In packaged builds, callers should
    prefer `importlib.resources` to locate data files reliably.

    Returns:
        Path to `app/catalog_data` (relative to current working directory).
    """
    return Path("app") / "catalog_data"


def get_file_size_bytes(path: Path) -> int:
    """Return the file size in bytes, or 0 if missing/unreadable.

    Args:
        path: Path to inspect.

    Returns:
        File size in bytes.
    """
    try:
        stat_result = path.stat()
        return int(stat_result.st_size)
    except OSError:
        return 0


def is_git_lfs_pointer_file(path: Path) -> bool:
    """Detect whether a file looks like a Git LFS pointer file.

    Git LFS pointer files are small text files with a standard header, e.g.:
        version https://git-lfs.github.com/spec/v1

    This helps produce clearer startup errors if a large SQLite is tracked via
    LFS but the binary object wasn't pulled.

    Args:
        path: File to check.

    Returns:
        True if it appears to be an LFS pointer file.
    """
    try:
        if not path.is_file():
            return False
        if get_file_size_bytes(path) > 2048:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        return "git-lfs.github.com/spec" in text and "oid sha256:" in text
    except OSError:
        return False


def require_catalog_file_is_present(path: Path) -> None:
    """Fail fast if a catalog file is missing or looks like an LFS pointer.

    Args:
        path: SQLite file path.

    Raises:
        CatalogNotFoundError: If missing.
        CatalogCorruptError: If it appears to be an LFS pointer.
    """
    if not path.exists():
        raise CatalogNotFoundError(f"Catalog database not found: {path}")

    if is_git_lfs_pointer_file(path):
        raise CatalogCorruptError(
            "Catalog database appears to be a Git LFS pointer file. "
            "Fetch LFS objects to obtain the actual SQLite database."
        )


def resolve_catalog_path(relative_or_absolute_path: str) -> Path:
    """Resolve a catalog path string to a Path.

    Args:
        relative_or_absolute_path: Path string.

    Returns:
        Path object.
    """
    candidate = Path(relative_or_absolute_path)
    if candidate.is_absolute():
        return candidate
    # Resolve relative to current working directory.
    return Path(os.getcwd()) / candidate


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "CatalogCorruptError",
    "CatalogDocumentItem",
    "CatalogError",
    "CatalogNotFoundError",
    "CatalogQueryResult",
    "CatalogRegistry",
    "CatalogSource",
    "CatalogStore",
    "UnknownCatalogSourceError",
    "get_default_catalog_data_directory",
    "get_file_size_bytes",
    "is_git_lfs_pointer_file",
    "require_catalog_file_is_present",
    "resolve_catalog_path",
]
