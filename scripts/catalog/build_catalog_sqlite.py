#!/usr/bin/env python3
"""Build a SQLite catalog database from Berlin sitemap discovery snapshots.

This script is an **offline build step** for the Legal-MCP repository/package.

Workflow:
1) (Manual, network IO) Generate a discovery snapshot JSON:
   - `uv run python scripts/berlin_portal_discovery.py`

2) (Offline, this script) Convert snapshot JSON -> SQLite catalog database:
   - `uv run python scripts/catalog/build_catalog_sqlite.py`

The resulting SQLite database contains *metadata only*:
- `document_id`
- `canonical_url`
- `document_type_prefix` (derived: "jlr" | "NJRE" | "other")
- `source` ("de-state-berlin-bsbe")

No document content is fetched by this script.

The produced DB is intended to be **committed** to the repository (optionally via
Git LFS if it becomes large).

Schema (as required by `app.catalog.store`):

- table: `documents`
    - source TEXT NOT NULL
    - document_id TEXT NOT NULL
    - canonical_url TEXT NOT NULL
    - document_type_prefix TEXT NOT NULL
    - PRIMARY KEY (source, document_id)

Indexes:
- documents(source, document_type_prefix, document_id)
- documents(source, document_id)

The script is deterministic:
- It sorts entries by `document_id` before writing (though SQLite indexing makes
  query ordering predictable regardless).

Exit codes:
- 0 on success
- non-zero on failure

Security/safety:
- No secrets/cookies are used.
- No network IO.
- Does not execute untrusted code. Reads local JSON only.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BERLIN_SOURCE = "de-state-berlin-bsbe"
DEFAULT_DISCOVERY_DIRECTORY = Path("data/raw/de-state/berlin/discovery")
DEFAULT_OUTPUT_PATH = Path("app/catalog_data/de_state_berlin_bsbe.sqlite")


class CatalogBuildError(RuntimeError):
    """Raised for failures that should abort the build script."""


@dataclass(frozen=True, slots=True)
class DiscoveryDocument:
    """A single discovery document entry from a snapshot JSON."""

    document_id: str
    canonical_url: str


def _normalize_document_type_prefix(document_id: str) -> str:
    if document_id.lower().startswith("jlr"):
        return "jlr"
    if document_id.startswith("NJRE"):
        return "NJRE"
    return "other"


def _find_latest_snapshot(discovery_directory: Path) -> Path:
    if not discovery_directory.exists():
        raise CatalogBuildError(
            f"Discovery directory not found: {discovery_directory}. "
            "Run scripts/berlin_portal_discovery.py first or pass --snapshot."
        )

    candidates = sorted(
        (path for path in discovery_directory.glob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise CatalogBuildError(
            f"No snapshot JSON files found in {discovery_directory}. "
            "Run scripts/berlin_portal_discovery.py first or pass --snapshot."
        )
    return candidates[0]


def _load_snapshot(snapshot_path: Path) -> list[DiscoveryDocument]:
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as exception:
        raise CatalogBuildError(
            f"Failed to parse snapshot JSON at {snapshot_path}: {exception}"
        ) from exception

    documents_raw = payload.get("documents")
    if not isinstance(documents_raw, list):
        raise CatalogBuildError(f"Snapshot {snapshot_path} missing 'documents' list.")

    discovered: list[DiscoveryDocument] = []
    for item in documents_raw:
        if not isinstance(item, dict):
            continue
        document_id = item.get("document_id")
        canonical_url = item.get("canonical_url")
        if not isinstance(document_id, str) or not document_id.strip():
            continue
        if not isinstance(canonical_url, str) or not canonical_url.strip():
            continue
        discovered.append(
            DiscoveryDocument(
                document_id=document_id.strip(),
                canonical_url=canonical_url.strip(),
            )
        )

    if not discovered:
        raise CatalogBuildError(
            f"Snapshot {snapshot_path} contained no valid document entries."
        )

    # Dedupe by document_id, keeping the first occurrence.
    seen: set[str] = set()
    unique: list[DiscoveryDocument] = []
    for document in discovered:
        if document.document_id in seen:
            continue
        seen.add(document.document_id)
        unique.append(document)

    # Deterministic ordering
    return sorted(unique, key=lambda d: d.document_id)


def _ensure_parent_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _connect(sqlite_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(sqlite_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
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


def _insert_documents(
    connection: sqlite3.Connection, *, source: str, documents: list[DiscoveryDocument]
) -> None:
    rows = [
        (
            source,
            document.document_id,
            document.canonical_url,
            _normalize_document_type_prefix(document.document_id),
        )
        for document in documents
    ]

    connection.executemany(
        """
        INSERT OR REPLACE INTO documents (
            source, document_id, canonical_url, document_type_prefix
        ) VALUES (?, ?, ?, ?);
        """,
        rows,
    )


def _count_rows(connection: sqlite3.Connection, *, source: str) -> int:
    cursor = connection.execute(
        "SELECT COUNT(*) AS count_total FROM documents WHERE source = ?",
        (source,),
    )
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _prefix_counts(connection: sqlite3.Connection, *, source: str) -> dict[str, int]:
    cursor = connection.execute(
        "SELECT document_type_prefix, COUNT(*) FROM documents "
        "WHERE source = ? GROUP BY document_type_prefix",
        (source,),
    )
    counts: dict[str, int] = {}
    for prefix_value, count_value in cursor.fetchall():
        counts[str(prefix_value)] = int(count_value)
    for expected in ("jlr", "NJRE", "other"):
        counts.setdefault(expected, 0)
    return counts


def _format_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size_kib = size_bytes / 1024
    if size_kib < 1024:
        return f"{size_kib:.1f} KiB"
    size_mib = size_kib / 1024
    if size_mib < 1024:
        return f"{size_mib:.1f} MiB"
    size_gib = size_mib / 1024
    return f"{size_gib:.2f} GiB"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an offline SQLite catalog from Berlin sitemap discovery snapshots."
    )
    parser.add_argument(
        "--source",
        default=BERLIN_SOURCE,
        help=f"Catalog source identifier (default: {BERLIN_SOURCE})",
    )
    parser.add_argument(
        "--snapshot",
        default="",
        help=(
            "Path to a discovery snapshot JSON file. If omitted, uses latest under "
            f"{DEFAULT_DISCOVERY_DIRECTORY}/"
        ),
    )
    parser.add_argument(
        "--discovery-directory",
        default=str(DEFAULT_DISCOVERY_DIRECTORY),
        help=(
            "Discovery directory to search for latest snapshot when --snapshot is omitted "
            f"(default: {DEFAULT_DISCOVERY_DIRECTORY})"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help=f"Output SQLite path (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="If set: delete existing rows for the given source before inserting new ones.",
    )
    return parser.parse_args()


def build_catalog_sqlite(
    *,
    source: str,
    snapshot_path: Path,
    output_sqlite_path: Path,
    replace: bool,
) -> dict[str, Any]:
    """Build the SQLite catalog database from a discovery snapshot.

    Args:
        source: Catalog source identifier (e.g., de-state-berlin-bsbe).
        snapshot_path: Path to a discovery snapshot JSON file.
        output_sqlite_path: SQLite output path.
        replace: If True, delete rows for the given source before insert.

    Returns:
        Summary dict with counts and file size.
    """
    documents = _load_snapshot(snapshot_path)

    _ensure_parent_directory(output_sqlite_path)

    with _connect(output_sqlite_path) as connection:
        _create_schema(connection)
        if replace:
            connection.execute("DELETE FROM documents WHERE source = ?", (source,))
        _insert_documents(connection, source=source, documents=documents)
        connection.commit()

        row_count = _count_rows(connection, source=source)
        prefix_counts = _prefix_counts(connection, source=source)

    file_size_bytes = output_sqlite_path.stat().st_size
    return {
        "source": source,
        "snapshot_path": str(snapshot_path),
        "output_sqlite_path": str(output_sqlite_path),
        "documents_inserted": len(documents),
        "documents_total_in_db": row_count,
        "prefix_counts": prefix_counts,
        "file_size_bytes": int(file_size_bytes),
    }


def main() -> int:
    """CLI entrypoint."""
    args = _parse_args()

    normalized_source = str(args.source).strip()
    if not normalized_source:
        raise SystemExit("Error: --source must be a non-empty string")

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
    else:
        discovery_directory = Path(args.discovery_directory)
        snapshot_path = _find_latest_snapshot(discovery_directory)

    output_sqlite_path = Path(args.output)

    # Anchor relative paths at current working directory (repo root when run normally).
    if not snapshot_path.is_absolute():
        snapshot_path = Path(os.getcwd()) / snapshot_path
    if not output_sqlite_path.is_absolute():
        output_sqlite_path = Path(os.getcwd()) / output_sqlite_path

    summary = build_catalog_sqlite(
        source=normalized_source,
        snapshot_path=snapshot_path,
        output_sqlite_path=output_sqlite_path,
        replace=bool(args.replace),
    )

    sys.stdout.write(
        "Catalog build complete.\n"
        f"  Source: {summary['source']}\n"
        f"  Snapshot: {summary['snapshot_path']}\n"
        f"  Output: {summary['output_sqlite_path']}\n"
        f"  Documents inserted: {summary['documents_inserted']}\n"
        f"  Documents in DB (source total): {summary['documents_total_in_db']}\n"
        f"  Prefix counts: {summary['prefix_counts']}\n"
        f"  File size: {_format_bytes(int(summary['file_size_bytes']))}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
