#!/usr/bin/env python3
"""Import pre-computed embeddings from portable JSONL files into a vector store.

Reads per-law ``.jsonl.gz`` files from ``data/embeddings/`` and batch-upserts
them into ChromaDB (default) or other vector store backends.

The JSONL files are the **source of truth** — this script is the bridge
between the portable format and any specific vector store.

Each line in a ``.jsonl.gz`` file is a JSON object::

    {
        "id": "bgb_para_433",
        "text": "(1) Durch den Kaufvertrag...",
        "embedding": [0.012345, -0.067891, ...],
        "metadata": {
            "law_abbrev": "BGB",
            "norm_id": "§ 433",
            "level": "norm",
            ...
        }
    }

Prerequisites:
    ChromaDB must be running::

        docker compose -f docker-compose.gpu.yml up -d chromadb
        # or
        docker compose up -d chromadb

Usage::

    # Import full corpus into ChromaDB
    python scripts/import_embeddings.py

    # Check status (compare manifest vs ChromaDB)
    python scripts/import_embeddings.py --status

    # Import only specific laws
    python scripts/import_embeddings.py --laws BGB STGB GG

    # Custom ChromaDB endpoint
    python scripts/import_embeddings.py --chroma-host localhost --chroma-port 9720

    # Wipe collection and re-import from scratch
    python scripts/import_embeddings.py --force

    # Dry run (parse files but don't write to store)
    python scripts/import_embeddings.py --dry-run

Extensibility:
    The ``--backend`` flag selects the target vector store.  Currently only
    ``chroma`` is implemented.  Adding a new backend requires implementing
    a single function: ``_import_batch_<backend>(batch, collection_config)``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so ``app.*`` imports work when
# running the script directly.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("import_embeddings")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_INPUT_DIR = str(_PROJECT_ROOT / "data" / "embeddings")
DEFAULT_CHROMA_HOST = "localhost"
DEFAULT_CHROMA_PORT = 9720
DEFAULT_COLLECTION_NAME = "german_laws"
DEFAULT_BATCH_SIZE = 500
DEFAULT_BACKEND = "chroma"

# HNSW configuration for the ChromaDB collection — must match the settings
# used during the original embedding pipeline.
HNSW_CONFIG = {
    "hnsw:M": 32,
    "hnsw:construction_ef": 256,
    "hnsw:search_ef": 128,
    "hnsw:space": "cosine",
}

# Health-check parameters
HEALTH_CHECK_TIMEOUT_SECONDS = 60
HEALTH_CHECK_INTERVAL_SECONDS = 2

# Flag set by SIGINT / SIGTERM handler
_interrupted = False


def _handle_signal(signum: int, _frame: Any) -> None:
    """Handle termination signals gracefully."""
    global _interrupted
    signal_name = signal.Signals(signum).name
    if _interrupted:
        logger.warning("Received %s again — forcing exit", signal_name)
        sys.exit(1)
    logger.warning(
        "Received %s — finishing current batch then stopping. "
        "Press Ctrl+C again to force-quit.",
        signal_name,
    )
    _interrupted = True


# ---------------------------------------------------------------------------
# JSONL reading
# ---------------------------------------------------------------------------


def _read_manifest(input_directory: Path) -> dict[str, Any]:
    """Read and validate the manifest.json file.

    Args:
        input_directory: Path to the embeddings directory.

    Returns:
        Parsed manifest dict.

    Raises:
        FileNotFoundError: If manifest.json does not exist.
        ValueError: If manifest is invalid.
    """
    manifest_path = input_directory / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No manifest.json found at {manifest_path}. "
            "Run precompute_to_jsonl.py first."
        )

    with open(manifest_path) as manifest_file:
        manifest = json.load(manifest_file)

    required_keys = {"version", "embedding_model", "embedding_dimension", "files"}
    missing_keys = required_keys - set(manifest.keys())
    if missing_keys:
        raise ValueError(f"Manifest is missing required keys: {missing_keys}")

    return manifest


def _read_jsonl_file(file_path: Path) -> list[dict[str, Any]]:
    """Read a single compressed JSONL file into a list of document dicts.

    Args:
        file_path: Path to the ``.jsonl.gz`` file.

    Returns:
        List of document dicts, each with id, text, embedding, metadata.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {file_path}")

    documents: list[dict[str, Any]] = []

    with gzip.open(file_path, "rt", encoding="utf-8") as gzip_file:
        for line_number, line in enumerate(gzip_file, 1):
            line = line.strip()
            if not line:
                continue
            try:
                document = json.loads(line)
                # Validate required fields
                if not all(
                    key in document for key in ("id", "text", "embedding", "metadata")
                ):
                    logger.warning(
                        "%s line %d: missing required fields — skipping",
                        file_path.name,
                        line_number,
                    )
                    continue
                documents.append(document)
            except json.JSONDecodeError as error:
                logger.warning(
                    "%s line %d: invalid JSON — %s",
                    file_path.name,
                    line_number,
                    error,
                )

    return documents


def _discover_jsonl_files(
    input_directory: Path,
    manifest: dict[str, Any],
    law_filter: list[str] | None = None,
) -> list[tuple[str, Path, int]]:
    """Discover JSONL files to import based on manifest and optional filter.

    Args:
        input_directory: Path to the embeddings directory.
        manifest: Parsed manifest dict.
        law_filter: Optional list of law abbreviations to import (uppercase).
            If None, all laws from the manifest are included.

    Returns:
        Sorted list of (law_abbreviation, file_path, expected_documents) tuples.
    """
    files_to_import: list[tuple[str, Path, int]] = []
    manifest_files = manifest.get("files", {})

    for filename, file_info in sorted(manifest_files.items()):
        law_abbreviation = filename.replace(".jsonl.gz", "")
        file_path = input_directory / filename

        # Apply filter if specified
        if law_filter is not None and law_abbreviation not in law_filter:
            continue

        if not file_path.exists():
            logger.warning("File listed in manifest but missing: %s", filename)
            continue

        expected_documents = file_info.get("documents", 0)
        files_to_import.append((law_abbreviation, file_path, expected_documents))

    return files_to_import


# ---------------------------------------------------------------------------
# ChromaDB backend
# ---------------------------------------------------------------------------


def _wait_for_chromadb(chroma_host: str, chroma_port: int) -> bool:
    """Wait for ChromaDB to become healthy with exponential backoff.

    Args:
        chroma_host: ChromaDB server hostname.
        chroma_port: ChromaDB server port.

    Returns:
        True if ChromaDB is reachable, False on timeout.
    """
    import httpx

    chroma_url = f"http://{chroma_host}:{chroma_port}"
    deadline = time.monotonic() + HEALTH_CHECK_TIMEOUT_SECONDS
    backoff = HEALTH_CHECK_INTERVAL_SECONDS

    while True:
        try:
            response = httpx.get(f"{chroma_url}/api/v2/heartbeat", timeout=5.0)
            if response.status_code == 200:
                logger.info("ChromaDB healthy at %s", chroma_url)
                return True
        except Exception as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "ChromaDB not reachable after %ds at %s: %s",
                    HEALTH_CHECK_TIMEOUT_SECONDS,
                    chroma_url,
                    error,
                )
                return False

            wait_time = min(backoff, remaining)
            logger.info("ChromaDB not ready, retrying in %.0fs: %s", wait_time, error)
            time.sleep(wait_time)
            backoff = min(backoff * 2, 15)


def _get_or_create_collection(
    chroma_host: str,
    chroma_port: int,
    collection_name: str,
    embedding_dimension: int,
    embedding_model: str,
    force: bool = False,
) -> Any:
    """Get or create the ChromaDB collection with proper HNSW config.

    Args:
        chroma_host: ChromaDB server hostname.
        chroma_port: ChromaDB server port.
        collection_name: Name of the collection.
        embedding_dimension: Dimensionality of embeddings (e.g. 768).
        embedding_model: Model name for collection metadata.
        force: If True, delete and recreate the collection.

    Returns:
        ChromaDB Collection object.
    """
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    client = chromadb.HttpClient(
        host=chroma_host,
        port=chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    if force:
        try:
            client.delete_collection(name=collection_name)
            logger.info("Deleted existing collection '%s' (--force)", collection_name)
        except Exception:
            pass  # Collection didn't exist — that's fine

    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={
            **HNSW_CONFIG,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
        },
    )

    current_count = collection.count()
    logger.info(
        "Collection '%s': %d existing documents",
        collection_name,
        current_count,
    )

    return collection


def _get_imported_laws(collection: Any) -> set[str]:
    """Get the set of law abbreviations already in the collection.

    Samples documents from the collection and extracts unique law_abbrev
    values. This is used for resume support — skip laws that are already
    fully imported.

    Note: This is an approximation. For a law to be considered "imported",
    at least one document with that law_abbrev must exist. To detect
    partial imports, compare document counts with the manifest.

    Args:
        collection: ChromaDB Collection object.

    Returns:
        Set of uppercase law abbreviations found in the collection.
    """
    document_count = collection.count()
    if document_count == 0:
        return set()

    # Sample in batches to collect all unique law abbreviations
    imported_laws: set[str] = set()
    offset = 0
    sample_size = 1000

    while offset < document_count:
        result = collection.get(
            limit=sample_size,
            offset=offset,
            include=["metadatas"],
        )
        if not result["ids"]:
            break

        for metadata in result["metadatas"]:
            law_abbreviation = metadata.get("law_abbrev", "")
            if law_abbreviation:
                imported_laws.add(law_abbreviation)

        offset += sample_size

    return imported_laws


def _import_batch_chroma(
    collection: Any,
    documents: list[dict[str, Any]],
) -> int:
    """Upsert a batch of documents into a ChromaDB collection.

    Args:
        collection: ChromaDB Collection object.
        documents: List of document dicts with id, text, embedding, metadata.

    Returns:
        Number of documents upserted.
    """
    if not documents:
        return 0

    ids: list[str] = []
    texts: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict[str, Any]] = []

    for document in documents:
        ids.append(document["id"])
        texts.append(document["text"])
        embeddings.append(document["embedding"])

        # ChromaDB metadata must be flat (str, int, float, bool)
        clean_metadata: dict[str, Any] = {}
        for key, value in document["metadata"].items():
            if isinstance(value, (str, int, float, bool)):
                clean_metadata[key] = value
            elif value is not None:
                clean_metadata[key] = str(value)
        metadatas.append(clean_metadata)

    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    return len(ids)


# ---------------------------------------------------------------------------
# Main import orchestration
# ---------------------------------------------------------------------------


def _run_import(
    input_directory: Path,
    backend: str,
    chroma_host: str,
    chroma_port: int,
    collection_name: str,
    batch_size: int,
    law_filter: list[str] | None,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Run the full import pipeline: JSONL files → vector store.

    Args:
        input_directory: Path to the embeddings directory.
        backend: Vector store backend ("chroma").
        chroma_host: ChromaDB server hostname.
        chroma_port: ChromaDB server port.
        collection_name: Target collection name.
        batch_size: Documents per upsert batch.
        law_filter: Optional list of specific law abbreviations to import.
        force: Wipe collection and reimport from scratch.
        dry_run: Parse files but don't write to store.

    Returns:
        Result dict with timing, counts, and errors.
    """
    global _interrupted

    start_time = time.monotonic()
    result: dict[str, Any] = {
        "state": "running",
        "backend": backend,
        "total_documents": 0,
        "total_laws": 0,
        "skipped_laws": 0,
        "error_count": 0,
        "errors": [],
    }

    # Read manifest
    try:
        manifest = _read_manifest(input_directory)
    except (FileNotFoundError, ValueError) as error:
        result["state"] = "failed"
        result["error_message"] = str(error)
        return result

    embedding_model = manifest["embedding_model"]
    embedding_dimension = manifest["embedding_dimension"]

    logger.info(
        "Manifest: %d laws, %d documents, model=%s, dim=%d",
        manifest.get("total_laws", 0),
        manifest.get("total_documents", 0),
        embedding_model,
        embedding_dimension,
    )

    # Discover files to import
    files_to_import = _discover_jsonl_files(input_directory, manifest, law_filter)
    if not files_to_import:
        result["state"] = "failed"
        result["error_message"] = "No JSONL files found to import"
        return result

    logger.info(
        "Found %d law files to import (%d total expected documents)",
        len(files_to_import),
        sum(expected for _, _, expected in files_to_import),
    )

    if dry_run:
        logger.info("Dry run — parsing files without writing to store")

    # Backend-specific setup
    collection = None
    already_imported: set[str] = set()

    if backend == "chroma" and not dry_run:
        if not _wait_for_chromadb(chroma_host, chroma_port):
            result["state"] = "failed"
            result["error_message"] = (
                f"ChromaDB not reachable at {chroma_host}:{chroma_port}"
            )
            return result

        collection = _get_or_create_collection(
            chroma_host=chroma_host,
            chroma_port=chroma_port,
            collection_name=collection_name,
            embedding_dimension=embedding_dimension,
            embedding_model=embedding_model,
            force=force,
        )

        # Resume support: find which laws are already imported
        if not force:
            already_imported = _get_imported_laws(collection)
            if already_imported:
                logger.info(
                    "Found %d laws already in collection — will skip them",
                    len(already_imported),
                )

    # Process each law file
    total_documents_imported = 0
    laws_processed = 0
    laws_skipped = 0

    for law_index, (law_abbreviation, file_path, _expected_count) in enumerate(
        files_to_import
    ):
        if _interrupted:
            logger.warning(
                "Interrupted — stopping after %d/%d laws",
                law_index,
                len(files_to_import),
            )
            result["state"] = "interrupted"
            break

        # Skip already-imported laws (resume support)
        if law_abbreviation in already_imported and not force:
            laws_skipped += 1
            continue

        # Read JSONL file
        try:
            documents = _read_jsonl_file(file_path)
        except Exception as read_error:
            error_message = f"Failed to read {file_path.name}: {read_error}"
            logger.error(error_message)
            result["errors"].append(error_message)
            result["error_count"] += 1
            continue

        if not documents:
            logger.debug("No documents in %s — skipping", file_path.name)
            laws_processed += 1
            continue

        if dry_run:
            total_documents_imported += len(documents)
            laws_processed += 1
            if (laws_processed % 100 == 0) or law_index == len(files_to_import) - 1:
                logger.info(
                    "Dry run progress: %d/%d laws, %d docs parsed",
                    laws_processed,
                    len(files_to_import),
                    total_documents_imported,
                )
            continue

        # Import in batches
        law_documents_imported = 0
        for batch_start in range(0, len(documents), batch_size):
            batch = documents[batch_start : batch_start + batch_size]

            try:
                if backend == "chroma" and collection is not None:
                    imported = _import_batch_chroma(collection, batch)
                else:
                    raise ValueError(f"Unsupported backend: {backend}")

                law_documents_imported += imported
            except Exception as import_error:
                error_message = (
                    f"Batch import failed for {law_abbreviation}: {import_error}"
                )
                logger.error(error_message)
                result["errors"].append(error_message)
                result["error_count"] += 1
                break

        total_documents_imported += law_documents_imported
        laws_processed += 1

        if (laws_processed % 100 == 0) or law_index == len(files_to_import) - 1:
            elapsed = time.monotonic() - start_time
            throughput = total_documents_imported / max(0.1, elapsed)
            logger.info(
                "Progress: %d/%d laws (%d skipped), %d docs imported "
                "(%.0f docs/sec, %.1fs)",
                laws_processed,
                len(files_to_import),
                laws_skipped,
                total_documents_imported,
                throughput,
                elapsed,
            )

    # Build result
    elapsed = time.monotonic() - start_time
    result["total_documents"] = total_documents_imported
    result["total_laws"] = laws_processed
    result["skipped_laws"] = laws_skipped
    result["elapsed_seconds"] = round(elapsed, 1)
    result["throughput_docs_per_sec"] = round(
        total_documents_imported / max(0.1, elapsed), 1
    )

    if result["state"] == "running":
        result["state"] = "completed"

    return result


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------


def _print_status(
    input_directory: Path,
    chroma_host: str,
    chroma_port: int,
    collection_name: str,
) -> None:
    """Compare manifest against ChromaDB to show import status.

    Args:
        input_directory: Path to the embeddings directory.
        chroma_host: ChromaDB server hostname.
        chroma_port: ChromaDB server port.
        collection_name: ChromaDB collection name.
    """
    # Read manifest
    try:
        manifest = _read_manifest(input_directory)
    except (FileNotFoundError, ValueError) as error:
        print(f"❌ {error}")
        return

    manifest_documents = manifest.get("total_documents", 0)
    manifest_laws = manifest.get("total_laws", 0)
    manifest_model = manifest.get("embedding_model", "unknown")

    print(f"📄 Manifest: {manifest_laws} laws, {manifest_documents:,} documents")
    print(f"   Model: {manifest_model}")
    print(f"   Source: {input_directory}")
    print()

    # Check ChromaDB
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.HttpClient(
            host=chroma_host,
            port=chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        try:
            collection = client.get_collection(name=collection_name)
            chroma_count = collection.count()

            # Sample laws from ChromaDB
            chroma_laws = _get_imported_laws(collection)

            print(
                f"🗄️  ChromaDB ({chroma_host}:{chroma_port}): "
                f"{chroma_count:,} documents, {len(chroma_laws)} laws"
            )
            print(f"   Collection: {collection_name}")

            # Compare
            print()
            if chroma_count >= manifest_documents:
                print(
                    f"✅ ChromaDB has {chroma_count:,} documents "
                    f"(manifest expects {manifest_documents:,})"
                )
            elif chroma_count > 0:
                missing = manifest_documents - chroma_count
                percentage = chroma_count / max(1, manifest_documents) * 100
                print(
                    f"⚠️  ChromaDB has {chroma_count:,} / {manifest_documents:,} "
                    f"documents ({percentage:.1f}%) — {missing:,} missing"
                )
                print("   Run: python scripts/import_embeddings.py  (resume)")
            else:
                print(
                    f"⚠️  ChromaDB collection is empty (manifest has {manifest_documents:,})"
                )
                print("   Run: python scripts/import_embeddings.py")

            # Check for laws in manifest but not in ChromaDB
            manifest_law_names = {
                filename.replace(".jsonl.gz", "")
                for filename in manifest.get("files", {})
            }
            missing_laws = manifest_law_names - chroma_laws
            if missing_laws and len(missing_laws) <= 20:
                print(f"   Missing laws: {sorted(missing_laws)}")

        except Exception:
            print(f"⚠️  ChromaDB collection '{collection_name}' does not exist yet")
            print("   Run: python scripts/import_embeddings.py")

    except Exception as error:
        print(f"❌ Cannot connect to ChromaDB at {chroma_host}:{chroma_port}: {error}")
        print("   Start it: docker compose -f docker-compose.gpu.yml up -d chromadb")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Import pre-computed embeddings from JSONL files into a vector store."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/import_embeddings.py                   # Full import\n"
            "  python scripts/import_embeddings.py --status          # Check status\n"
            "  python scripts/import_embeddings.py --laws BGB STGB   # Specific laws\n"
            "  python scripts/import_embeddings.py --force           # Wipe + reimport\n"
            "  python scripts/import_embeddings.py --dry-run         # Parse only\n"
        ),
    )
    parser.add_argument(
        "--input-dir",
        default=os.environ.get("EMBEDDINGS_DIR", DEFAULT_INPUT_DIR),
        help=f"Embeddings directory with JSONL files (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        choices=["chroma"],
        help=f"Vector store backend (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument(
        "--chroma-host",
        default=os.environ.get("CHROMA_HOST", DEFAULT_CHROMA_HOST),
        help=f"ChromaDB server hostname (default: {DEFAULT_CHROMA_HOST})",
    )
    parser.add_argument(
        "--chroma-port",
        type=int,
        default=int(os.environ.get("CHROMA_PORT", str(DEFAULT_CHROMA_PORT))),
        help=f"ChromaDB server port (default: {DEFAULT_CHROMA_PORT})",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help=f"Collection name (default: {DEFAULT_COLLECTION_NAME})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Documents per upsert batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--laws",
        nargs="+",
        default=None,
        metavar="LAW",
        help="Import only these laws (e.g. --laws BGB STGB GG)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe existing collection and reimport from scratch",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse JSONL files but don't write to the vector store",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Compare manifest against ChromaDB and show import status",
    )
    return parser


def main() -> None:
    """Entry point for the import embeddings script."""
    parser = _build_argument_parser()
    arguments = parser.parse_args()

    input_directory = Path(arguments.input_dir)

    if arguments.status:
        _print_status(
            input_directory=input_directory,
            chroma_host=arguments.chroma_host,
            chroma_port=arguments.chroma_port,
            collection_name=arguments.collection,
        )
        return

    # Uppercase law filter for consistent matching
    law_filter = [law.upper() for law in arguments.laws] if arguments.laws else None

    # Install signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if not input_directory.is_dir():
        logger.error("Input directory not found: %s", input_directory)
        sys.exit(1)

    separator = "=" * 70
    logger.info(separator)
    logger.info("Import embeddings → %s", arguments.backend)
    logger.info(separator)
    logger.info("Input dir:     %s", input_directory)
    logger.info("Backend:       %s", arguments.backend)
    if arguments.backend == "chroma":
        logger.info(
            "ChromaDB:      %s:%d", arguments.chroma_host, arguments.chroma_port
        )
        logger.info("Collection:    %s", arguments.collection)
    logger.info("Batch size:    %d", arguments.batch_size)
    logger.info("Laws filter:   %s", law_filter or "all")
    logger.info("Force:         %s", arguments.force)
    logger.info("Dry run:       %s", arguments.dry_run)
    logger.info(separator)

    result = _run_import(
        input_directory=input_directory,
        backend=arguments.backend,
        chroma_host=arguments.chroma_host,
        chroma_port=arguments.chroma_port,
        collection_name=arguments.collection,
        batch_size=arguments.batch_size,
        law_filter=law_filter,
        force=arguments.force,
        dry_run=arguments.dry_run,
    )

    # Print summary
    logger.info(separator)
    logger.info("State:            %s", result["state"])
    logger.info("Documents:        %d", result.get("total_documents", 0))
    logger.info("Laws imported:    %d", result.get("total_laws", 0))
    logger.info("Laws skipped:     %d", result.get("skipped_laws", 0))
    logger.info("Errors:           %d", result.get("error_count", 0))
    logger.info("Time:             %.1fs", result.get("elapsed_seconds", 0))
    logger.info(
        "Throughput:       %.1f docs/sec", result.get("throughput_docs_per_sec", 0)
    )
    logger.info(separator)

    print(json.dumps(result, indent=2, default=str))

    if result["state"] == "failed":
        sys.exit(1)
    elif result["state"] == "interrupted":
        sys.exit(2)


if __name__ == "__main__":
    main()
