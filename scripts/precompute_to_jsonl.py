#!/usr/bin/env python3
"""Pre-compute embeddings and export to portable per-law JSONL files.

Reads the pre-downloaded German federal law HTML corpus, embeds via
GPU-accelerated TEI, and writes per-law ``.jsonl.gz`` files plus a
``manifest.json`` to ``data/embeddings/``.

The JSONL files are the **source of truth** — portable, inspectable,
and storage-agnostic.  They can be imported into ChromaDB, Qdrant,
Weaviate, pgvector, or any other vector store.

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
    Start TEI on the GPU stack before running::

        docker compose -f docker-compose.gpu.yml up -d tei-embeddings

Usage::

    # Full corpus (all ~2,629 laws)
    python scripts/precompute_to_jsonl.py

    # Quick test with 10 laws
    python scripts/precompute_to_jsonl.py --max-laws 10

    # Custom TEI endpoint and output directory
    python scripts/precompute_to_jsonl.py --tei-url http://gpu:9721 --output-dir /tmp/emb

    # Force re-export of all laws (skip resume logic)
    python scripts/precompute_to_jsonl.py --force

    # Show what's already exported
    python scripts/precompute_to_jsonl.py --status
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
logger = logging.getLogger("precompute_to_jsonl")

# ---------------------------------------------------------------------------
# Defaults — match docker-compose.gpu.yml port mappings
# ---------------------------------------------------------------------------
DEFAULT_TEI_URL = "http://localhost:9721"
DEFAULT_HTML_ROOT = str(_PROJECT_ROOT / "data" / "html")
DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "data" / "embeddings")
DEFAULT_BATCH_SIZE = 64
DEFAULT_MAX_WORKERS = 8
DEFAULT_FLOAT_PRECISION = 6

# Health-check parameters
HEALTH_CHECK_TIMEOUT_SECONDS = 120
HEALTH_CHECK_INTERVAL_SECONDS = 3

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
        "Received %s — finishing current law then stopping. "
        "Press Ctrl+C again to force-quit.",
        signal_name,
    )
    _interrupted = True


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def _wait_for_tei(tei_url: str) -> bool:
    """Wait for TEI to become healthy, with exponential backoff.

    Args:
        tei_url: Base URL of the TEI server.

    Returns:
        True if TEI became reachable, False on timeout.
    """
    import httpx

    deadline = time.monotonic() + HEALTH_CHECK_TIMEOUT_SECONDS
    backoff = HEALTH_CHECK_INTERVAL_SECONDS

    while True:
        try:
            response = httpx.get(f"{tei_url}/health", timeout=5.0)
            if response.status_code == 200:
                logger.info("TEI server healthy at %s", tei_url)
                return True
        except Exception as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "TEI not reachable after %ds at %s: %s",
                    HEALTH_CHECK_TIMEOUT_SECONDS,
                    tei_url,
                    error,
                )
                return False

            wait_time = min(backoff, remaining)
            logger.info("TEI not ready, retrying in %.0fs: %s", wait_time, error)
            time.sleep(wait_time)
            backoff = min(backoff * 2, 30)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _get_existing_laws(output_directory: Path) -> set[str]:
    """Scan the output directory for already-exported laws.

    Reads ``manifest.json`` if present and cross-references with actual
    ``.jsonl.gz`` files on disk.

    Args:
        output_directory: Path to the embeddings output directory.

    Returns:
        Set of law abbreviations (uppercase) that already have valid exports.
    """
    manifest_path = output_directory / "manifest.json"
    if not manifest_path.exists():
        return set()

    try:
        with open(manifest_path) as manifest_file:
            manifest = json.load(manifest_file)
    except (json.JSONDecodeError, OSError) as error:
        logger.warning("Could not read manifest: %s — starting fresh", error)
        return set()

    existing_laws: set[str] = set()
    for filename, file_info in manifest.get("files", {}).items():
        filepath = output_directory / filename
        if filepath.exists():
            law_abbreviation = filename.replace(".jsonl.gz", "")
            expected_documents = file_info.get("documents", 0)
            if expected_documents > 0:
                existing_laws.add(law_abbreviation)

    return existing_laws


def _write_law_jsonl(
    output_directory: Path,
    law_abbreviation: str,
    documents: list[dict[str, Any]],
) -> int:
    """Write documents for a single law to a compressed JSONL file.

    Args:
        output_directory: Target directory for output files.
        law_abbreviation: Uppercase law abbreviation (used as filename).
        documents: List of document dicts with id, text, embedding, metadata.

    Returns:
        Number of documents written.
    """
    output_path = output_directory / f"{law_abbreviation}.jsonl.gz"
    count = 0

    with gzip.open(output_path, "wt", encoding="utf-8", compresslevel=6) as gzip_file:
        for document in documents:
            line = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
            gzip_file.write(line + "\n")
            count += 1

    return count


def _run_precompute(
    tei_url: str,
    html_root: Path,
    output_directory: Path,
    max_laws: int | None,
    batch_size: int,
    max_workers: int,
    float_precision: int,
    force: bool,
) -> dict[str, Any]:
    """Run the full pre-compute pipeline: HTML → TEI embed → JSONL.

    Args:
        tei_url: TEI server URL.
        html_root: Path to HTML corpus root.
        output_directory: Path for output JSONL files.
        max_laws: Maximum laws to process (None for all).
        batch_size: Documents per TEI embedding batch.
        max_workers: Concurrent HTML parsing workers.
        float_precision: Decimal places for embedding floats.
        force: Force re-export even if law already exported.

    Returns:
        Result dict with timing, counts, and errors.
    """
    global _interrupted

    from app.ingestion.local_pipeline import (
        _parse_law_directory,
        discover_local_laws,
    )
    from app.ingestion.tei_client import TEIEmbeddingClient

    start_time = time.monotonic()
    result: dict[str, Any] = {
        "state": "running",
        "total_documents": 0,
        "total_laws": 0,
        "error_count": 0,
        "errors": [],
        "files_written": {},
    }

    # Wait for TEI
    if not _wait_for_tei(tei_url):
        result["state"] = "failed"
        result["error_message"] = f"TEI not reachable at {tei_url}"
        return result

    # Initialize TEI client
    tei_client = TEIEmbeddingClient(base_urls=[tei_url])
    logger.info(
        "TEI client initialized: %s (batch_size=%s)",
        tei_url,
        tei_client._server_batch_size,
    )

    # Discover laws
    all_laws = discover_local_laws(html_root)
    if not all_laws:
        result["state"] = "failed"
        result["error_message"] = f"No laws found in {html_root}"
        return result

    # Resume support: skip already-exported laws
    output_directory.mkdir(parents=True, exist_ok=True)
    if not force:
        existing_laws = _get_existing_laws(output_directory)
        if existing_laws:
            before_count = len(all_laws)
            all_laws = [
                (law_abbreviation, files)
                for law_abbreviation, files in all_laws
                if law_abbreviation.upper() not in existing_laws
            ]
            skipped_count = before_count - len(all_laws)
            if skipped_count > 0:
                logger.info(
                    "Resume: skipping %d already-exported laws, %d remaining",
                    skipped_count,
                    len(all_laws),
                )

    if max_laws is not None:
        all_laws = all_laws[:max_laws]

    if not all_laws:
        logger.info("All laws already exported. Use --force to re-export.")
        result["state"] = "skipped"
        result["skip_reason"] = "All laws already exported"
        return result

    total_laws = len(all_laws)
    total_files = sum(len(files) for _, files in all_laws)
    logger.info("Will process %d laws with %d HTML files", total_laws, total_files)

    # Parse all HTML concurrently, then embed and write per-law
    laws_processed = 0
    total_documents_written = 0
    files_written: dict[str, dict[str, Any]] = {}

    # Process laws one at a time: parse → embed → write
    # This keeps memory bounded (only one law's documents in memory at a time).
    # Parsing within each law uses a thread pool for concurrency.
    for law_index, (law_abbreviation, html_files) in enumerate(all_laws):
        if _interrupted:
            logger.warning(
                "Interrupted — stopping after %d/%d laws", law_index, total_laws
            )
            result["state"] = "interrupted"
            break

        law_start = time.monotonic()
        law_abbreviation_upper = law_abbreviation.upper()

        # Phase 1: Parse HTML files for this law
        _, parsed_documents, parse_errors = _parse_law_directory(
            law_abbreviation, html_files
        )

        if parse_errors:
            result["errors"].extend(parse_errors)
            result["error_count"] += len(parse_errors)

        if not parsed_documents:
            logger.debug(
                "No documents parsed for %s — skipping", law_abbreviation_upper
            )
            laws_processed += 1
            continue

        # Phase 2: Prepare texts and metadata, dedup by doc_id
        seen_ids: set[str] = set()
        document_ids: list[str] = []
        document_texts: list[str] = []
        document_metadatas: list[dict[str, Any]] = []

        for document in parsed_documents:
            if not document.page_content:
                continue

            doc_metadata = dict(document.metadata)
            doc_id = doc_metadata.get("doc_id", f"doc_{hash(document.page_content)}")

            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)

            document_ids.append(doc_id)
            document_texts.append(document.page_content)

            # Clean metadata: remove source_file (absolute path, not portable)
            clean_metadata = {
                key: value
                for key, value in doc_metadata.items()
                if key != "source_file" and value is not None
            }
            document_metadatas.append(clean_metadata)

        if not document_texts:
            laws_processed += 1
            continue

        # Phase 3: Embed via TEI (batched, with retry on 413)
        embeddings_array = None
        current_batch_size = batch_size
        while current_batch_size >= 1:
            try:
                embeddings_array = tei_client.encode(
                    document_texts,
                    batch_size=current_batch_size,
                )
                break  # success
            except Exception as embed_error:
                error_string = str(embed_error)
                if "413" in error_string and current_batch_size > 1:
                    current_batch_size = max(1, current_batch_size // 2)
                    logger.warning(
                        "413 Payload Too Large for %s — retrying with batch_size=%d",
                        law_abbreviation_upper,
                        current_batch_size,
                    )
                    continue
                error_message = (
                    f"Embedding failed for {law_abbreviation_upper}: {embed_error}"
                )
                logger.error(error_message)
                result["errors"].append(error_message)
                result["error_count"] += 1
                break

        if embeddings_array is None or len(embeddings_array) == 0:
            laws_processed += 1
            continue

        # Phase 4: Build JSONL records and write
        jsonl_records: list[dict[str, Any]] = []
        for record_index in range(len(document_ids)):
            embedding_list = embeddings_array[record_index].tolist()
            if float_precision is not None:
                embedding_list = [
                    round(value, float_precision) for value in embedding_list
                ]

            jsonl_records.append(
                {
                    "id": document_ids[record_index],
                    "text": document_texts[record_index],
                    "embedding": embedding_list,
                    "metadata": document_metadatas[record_index],
                }
            )

        documents_written = _write_law_jsonl(
            output_directory, law_abbreviation_upper, jsonl_records
        )

        total_documents_written += documents_written
        laws_processed += 1

        file_path = output_directory / f"{law_abbreviation_upper}.jsonl.gz"
        files_written[f"{law_abbreviation_upper}.jsonl.gz"] = {
            "documents": documents_written,
            "size_bytes": file_path.stat().st_size,
        }

        time.monotonic() - law_start
        if (laws_processed % 100 == 0) or (law_index == total_laws - 1):
            overall_elapsed = time.monotonic() - start_time
            throughput = total_documents_written / max(0.1, overall_elapsed)
            logger.info(
                "Progress: %d/%d laws, %d docs written (%.0f docs/sec, %.1fs)",
                laws_processed,
                total_laws,
                total_documents_written,
                throughput,
                overall_elapsed,
            )

    # Build result
    elapsed = time.monotonic() - start_time
    result["total_documents"] = total_documents_written
    result["total_laws"] = laws_processed
    result["elapsed_seconds"] = round(elapsed, 1)
    result["throughput_docs_per_sec"] = round(
        total_documents_written / max(0.1, elapsed), 1
    )
    result["files_written"] = files_written

    if result["state"] == "running":
        result["state"] = "completed"

    # Write / update manifest
    _write_manifest(output_directory, float_precision, files_written, result)

    return result


def _write_manifest(
    output_directory: Path,
    float_precision: int,
    new_files: dict[str, dict[str, Any]],
    run_result: dict[str, Any],
) -> None:
    """Write or update the manifest.json file.

    Merges newly written files with any existing manifest entries (from
    previous runs / resume).

    Args:
        output_directory: Path to the embeddings output directory.
        float_precision: Decimal places used for float rounding.
        new_files: Dict of filename → {documents, size_bytes} for this run.
        run_result: The result dict from the current run.
    """
    manifest_path = output_directory / "manifest.json"

    # Load existing manifest for merge (resume case)
    existing_files: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            with open(manifest_path) as manifest_file:
                existing_manifest = json.load(manifest_file)
                existing_files = existing_manifest.get("files", {})
        except (json.JSONDecodeError, OSError):
            pass

    # Merge: new files overwrite existing entries for the same law
    merged_files = {**existing_files, **new_files}

    # Compute totals from merged files
    total_documents = sum(
        file_info.get("documents", 0) for file_info in merged_files.values()
    )
    total_size = sum(
        file_info.get("size_bytes", 0) for file_info in merged_files.values()
    )

    manifest = {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "embedding_model": "jinaai/jina-embeddings-v2-base-de",
        "embedding_dimension": 768,
        "float_precision": float_precision,
        "total_documents": total_documents,
        "total_laws": len(merged_files),
        "total_size_bytes": total_size,
        "levels": ["norm", "paragraph"],
        "jurisdiction": "de-federal",
        "source": "gesetze-im-internet.de HTML corpus",
        "last_run": {
            "state": run_result.get("state", "unknown"),
            "documents_added": run_result.get("total_documents", 0),
            "laws_processed": run_result.get("total_laws", 0),
            "elapsed_seconds": run_result.get("elapsed_seconds", 0),
            "error_count": run_result.get("error_count", 0),
        },
        "files": dict(sorted(merged_files.items())),
    }

    with open(manifest_path, "w") as manifest_file:
        json.dump(manifest, manifest_file, indent=2, ensure_ascii=False)

    logger.info(
        "Manifest written: %d laws, %d documents, %.1f MB compressed",
        len(merged_files),
        total_documents,
        total_size / 1024 / 1024,
    )


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------


def _print_status(output_directory: Path) -> None:
    """Print current export status from the output directory.

    Args:
        output_directory: Path to the embeddings output directory.
    """
    manifest_path = output_directory / "manifest.json"

    if not manifest_path.exists():
        print(f"No manifest found at {manifest_path}")
        print("Run the export first: python scripts/precompute_to_jsonl.py")
        return

    with open(manifest_path) as manifest_file:
        manifest = json.load(manifest_file)

    files = manifest.get("files", {})
    total_documents = manifest.get("total_documents", 0)
    total_size = manifest.get("total_size_bytes", 0)
    total_laws = manifest.get("total_laws", 0)

    # Verify files on disk
    missing_files: list[str] = []
    for filename in files:
        if not (output_directory / filename).exists():
            missing_files.append(filename)

    actual_files_on_disk = list(output_directory.glob("*.jsonl.gz"))

    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    print()

    status_icon = "✅" if not missing_files else "⚠️"
    print(
        f"{status_icon} {total_laws} laws, {total_documents:,} documents, "
        f"{total_size / 1024 / 1024:.1f} MB compressed"
    )
    print(f"   Files on disk: {len(actual_files_on_disk)} .jsonl.gz files")

    if missing_files:
        print(f"   ⚠️  Missing files: {len(missing_files)}")
        for missing in missing_files[:10]:
            print(f"      - {missing}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Pre-compute embeddings and export to portable JSONL files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/precompute_to_jsonl.py                 # Full corpus\n"
            "  python scripts/precompute_to_jsonl.py --max-laws 10   # Quick test\n"
            "  python scripts/precompute_to_jsonl.py --status        # Check status\n"
            "  python scripts/precompute_to_jsonl.py --force         # Re-export all\n"
        ),
    )
    parser.add_argument(
        "--tei-url",
        default=os.environ.get("TEI_URL", DEFAULT_TEI_URL),
        help=f"TEI server URL (default: {DEFAULT_TEI_URL})",
    )
    parser.add_argument(
        "--html-root",
        default=os.environ.get("WARMUP_HTML_ROOT", DEFAULT_HTML_ROOT),
        help=f"HTML corpus root directory (default: {DEFAULT_HTML_ROOT})",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for JSONL files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--max-laws",
        type=int,
        default=None,
        help="Maximum number of laws to process (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Documents per TEI embedding batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Concurrent HTML parsing workers (default: {DEFAULT_MAX_WORKERS})",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=DEFAULT_FLOAT_PRECISION,
        help=f"Decimal places for embedding floats (default: {DEFAULT_FLOAT_PRECISION})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-export even if laws already exported",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show export status and exit",
    )
    return parser


def main() -> None:
    """Entry point for the pre-compute to JSONL script."""
    parser = _build_argument_parser()
    arguments = parser.parse_args()

    output_directory = Path(arguments.output_dir)

    if arguments.status:
        _print_status(output_directory)
        return

    # Set environment for TEI usage
    os.environ.setdefault("USE_TEI", "true")
    os.environ.setdefault("TEI_URL", arguments.tei_url)

    # Install signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    html_root = Path(arguments.html_root)
    if not html_root.is_dir():
        logger.error("HTML root not found: %s", html_root)
        sys.exit(1)

    separator = "=" * 70
    logger.info(separator)
    logger.info("Pre-compute embeddings → JSONL")
    logger.info(separator)
    logger.info("TEI URL:       %s", arguments.tei_url)
    logger.info("HTML root:     %s", html_root)
    logger.info("Output dir:    %s", output_directory)
    logger.info("Max laws:      %s", arguments.max_laws or "all")
    logger.info("Batch size:    %d", arguments.batch_size)
    logger.info("Float prec:    %d decimal places", arguments.precision)
    logger.info("Force:         %s", arguments.force)
    logger.info(separator)

    result = _run_precompute(
        tei_url=arguments.tei_url,
        html_root=html_root,
        output_directory=output_directory,
        max_laws=arguments.max_laws,
        batch_size=arguments.batch_size,
        max_workers=arguments.max_workers,
        float_precision=arguments.precision,
        force=arguments.force,
    )

    # Print summary
    logger.info(separator)
    logger.info("State:            %s", result["state"])
    logger.info("Documents:        %d", result.get("total_documents", 0))
    logger.info("Laws:             %d", result.get("total_laws", 0))
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
