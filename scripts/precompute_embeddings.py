#!/usr/bin/env python3
"""Pre-compute embeddings for the entire German federal law corpus.

Orchestrates GPU-accelerated embedding of all 2,631 laws (58,255 HTML files)
using HF Text Embeddings Inference (TEI) on CUDA, storing results in ChromaDB.

This script is a thin orchestration layer over the existing ``warmup`` pipeline.
It adds infrastructure health checks, GPU-tuned configuration, detailed timing
reports, and graceful signal handling — but delegates all actual ingestion logic
to ``app.warmup.run_warmup_sync`` and ``app.ingestion.local_pipeline``.

Prerequisites:
    Start the GPU stack before running this script::

        docker compose -f docker-compose.gpu.yml up -d chromadb tei-embeddings

    Required environment (set automatically by the script when not present):
        USE_TEI=true
        TEI_URL=http://localhost:8013
        CHROMA_HOST=localhost
        CHROMA_PORT=8001

Usage::

    # Full corpus (all 2,631 laws)
    python scripts/precompute_embeddings.py

    # Quick test with 10 laws
    python scripts/precompute_embeddings.py --max-laws 10

    # Custom TEI endpoint
    python scripts/precompute_embeddings.py --tei-url http://gpu-server:8013

    # Force re-ingestion even if corpus exists
    python scripts/precompute_embeddings.py --force

    # Show corpus status only
    python scripts/precompute_embeddings.py --status
"""

from __future__ import annotations

import argparse
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
# running the script directly (not via ``uv run``).
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("precompute_embeddings")

# ---------------------------------------------------------------------------
# Defaults — match docker-compose.gpu.yml port mappings
# ---------------------------------------------------------------------------
DEFAULT_TEI_URL = "http://localhost:9721"
DEFAULT_CHROMA_HOST = "localhost"
DEFAULT_CHROMA_PORT = "9720"
DEFAULT_HTML_ROOT = str(_PROJECT_ROOT / "data" / "html")
DEFAULT_BATCH_SIZE = 256
DEFAULT_MAX_WORKERS = 8

# Health-check retry parameters
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
        "Received %s — finishing current batch then stopping. "
        "Press Ctrl+C again to force-quit.",
        signal_name,
    )
    _interrupted = True


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


def _check_tei_health(tei_url: str) -> bool:
    """Check if TEI server is healthy and return model info.

    Args:
        tei_url: Base URL of the TEI server.

    Returns:
        True if healthy, False otherwise.
    """
    import httpx

    health_endpoint = f"{tei_url}/health"
    info_endpoint = f"{tei_url}/info"

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(health_endpoint)
            response.raise_for_status()

            info_response = client.get(info_endpoint)
            if info_response.status_code == 200:
                info = info_response.json()
                model_id = info.get("model_id", "unknown")
                model_dtype = info.get("model_dtype", "unknown")
                max_batch_size = info.get("max_client_batch_size", "unknown")
                max_batch_tokens = info.get("max_batch_tokens", "unknown")
                max_concurrent = info.get("max_concurrent_requests", "unknown")
                logger.info(
                    "TEI server info: model=%s dtype=%s "
                    "max_batch_size=%s max_batch_tokens=%s max_concurrent=%s",
                    model_id,
                    model_dtype,
                    max_batch_size,
                    max_batch_tokens,
                    max_concurrent,
                )
            return True
    except Exception as error:
        logger.debug("TEI health check failed: %s", error)
        return False


def _check_chromadb_health(chroma_host: str, chroma_port: int) -> bool:
    """Check if ChromaDB server is healthy.

    Args:
        chroma_host: ChromaDB hostname.
        chroma_port: ChromaDB port.

    Returns:
        True if healthy, False otherwise.
    """
    import httpx

    heartbeat_url = f"http://{chroma_host}:{chroma_port}/api/v2/heartbeat"

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(heartbeat_url)
            response.raise_for_status()
            return True
    except Exception as error:
        logger.debug("ChromaDB health check failed: %s", error)
        return False


def _wait_for_service(
    name: str,
    check_function: Any,
    timeout_seconds: int = HEALTH_CHECK_TIMEOUT_SECONDS,
) -> bool:
    """Wait for a service to become healthy with retries.

    Args:
        name: Human-readable service name for logging.
        check_function: Zero-arg callable returning True when healthy.
        timeout_seconds: Maximum seconds to wait.

    Returns:
        True if the service became healthy within the timeout.
    """
    logger.info("Waiting for %s to be healthy (timeout %ds)...", name, timeout_seconds)
    start_time = time.monotonic()

    while time.monotonic() - start_time < timeout_seconds:
        if check_function():
            elapsed = time.monotonic() - start_time
            logger.info("%s is healthy (took %.1fs)", name, elapsed)
            return True
        if _interrupted:
            logger.warning("Interrupted while waiting for %s", name)
            return False
        time.sleep(HEALTH_CHECK_INTERVAL_SECONDS)

    logger.error("%s did not become healthy within %ds", name, timeout_seconds)
    return False


# ---------------------------------------------------------------------------
# Corpus status
# ---------------------------------------------------------------------------


def _print_corpus_status() -> None:
    """Print current corpus status from ChromaDB and exit."""
    from app.ingestion.local_pipeline import get_corpus_status

    corpus_status = get_corpus_status()
    print(json.dumps(corpus_status, indent=2))

    total_documents = corpus_status.get("total_documents", 0)
    populated = corpus_status.get("populated", False)
    sampled_laws = corpus_status.get("sampled_laws", [])

    if populated:
        print(
            f"\n✅ Corpus populated: {total_documents} documents, "
            f"{len(sampled_laws)} sampled laws"
        )
    else:
        error_message = corpus_status.get("error")
        if error_message:
            print(f"\n❌ Corpus check failed: {error_message}")
        else:
            print("\n⚠️  Corpus empty — run pre-compute to populate")


# ---------------------------------------------------------------------------
# Main pre-compute logic
# ---------------------------------------------------------------------------


def _run_precompute(
    tei_url: str,
    chroma_host: str,
    chroma_port: int,
    html_root: str,
    max_laws: int | None,
    batch_size: int,
    max_workers: int,
    force: bool,
) -> dict[str, Any]:
    """Run the pre-compute embedding pipeline.

    Delegates to ``app.warmup.run_warmup_sync`` after verifying
    infrastructure health.

    Args:
        tei_url: TEI server URL.
        chroma_host: ChromaDB hostname.
        chroma_port: ChromaDB port.
        html_root: Path to HTML corpus root.
        max_laws: Maximum laws to process (None for all).
        batch_size: Documents per embedding batch.
        max_workers: Concurrent HTML parsing workers.
        force: Force re-ingestion even if corpus populated.

    Returns:
        Result dict from warmup with timing information.
    """
    # ── Step 1: Health checks ────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("Pre-compute Embeddings — GPU-Accelerated Pipeline")
    logger.info("=" * 70)
    logger.info("TEI URL:      %s", tei_url)
    logger.info("ChromaDB:     %s:%d", chroma_host, chroma_port)
    logger.info("HTML root:    %s", html_root)
    logger.info("Max laws:     %s", max_laws or "all")
    logger.info("Batch size:   %d", batch_size)
    logger.info("Max workers:  %d", max_workers)
    logger.info("Force:        %s", force)
    logger.info("-" * 70)

    if not _wait_for_service(
        "ChromaDB",
        lambda: _check_chromadb_health(chroma_host, chroma_port),
    ):
        return {
            "state": "failed",
            "error_message": (
                f"ChromaDB not reachable at {chroma_host}:{chroma_port}. "
                "Start it with: docker compose -f docker-compose.gpu.yml up -d chromadb"
            ),
        }

    if not _wait_for_service(
        "TEI embeddings",
        lambda: _check_tei_health(tei_url),
    ):
        return {
            "state": "failed",
            "error_message": (
                f"TEI server not reachable at {tei_url}. "
                "Start it with: docker compose -f docker-compose.gpu.yml up -d tei-embeddings"
            ),
        }

    if _interrupted:
        return {"state": "failed", "error_message": "Interrupted before ingestion"}

    # ── Step 2: Count available laws ─────────────────────────────────────
    html_root_path = Path(html_root)
    if not html_root_path.is_dir():
        return {
            "state": "failed",
            "error_message": f"HTML root directory not found: {html_root}",
        }

    law_directories = sorted(
        directory for directory in html_root_path.iterdir() if directory.is_dir()
    )
    html_file_count = sum(
        1
        for law_directory in law_directories
        for html_file in law_directory.iterdir()
        if html_file.suffix == ".html"
    )
    logger.info(
        "Corpus: %d law directories, %d HTML files",
        len(law_directories),
        html_file_count,
    )

    # ── Step 3: Run warmup (the actual embedding pipeline) ───────────────
    logger.info("-" * 70)
    logger.info("Starting embedding pipeline via warmup...")
    logger.info("-" * 70)

    overall_start = time.monotonic()

    from app.warmup import run_warmup_sync

    result = run_warmup_sync(
        html_root=html_root,
        max_laws=max_laws,
        batch_size=batch_size,
        max_workers=max_workers,
        force=force,
    )

    overall_elapsed = time.monotonic() - overall_start

    # ── Step 4: Report results ───────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("Pre-compute Results")
    logger.info("=" * 70)

    state = result.get("state", "unknown")
    documents_added = result.get("documents_added", 0)
    laws_processed = result.get("laws_processed", 0)
    warmup_elapsed = result.get("elapsed_seconds", 0)
    error_count = result.get("error_count", 0)
    skip_reason = result.get("skip_reason")

    if state == "completed":
        throughput = documents_added / max(0.1, warmup_elapsed)
        logger.info("State:            ✅ completed")
        logger.info("Documents added:  %d", documents_added)
        logger.info("Laws processed:   %d", laws_processed)
        logger.info("Errors:           %d", error_count)
        logger.info("Warmup time:      %.1fs", warmup_elapsed)
        logger.info("Overall time:     %.1fs", overall_elapsed)
        logger.info("Throughput:       %.1f docs/sec", throughput)
        if laws_processed > 0:
            logger.info("Avg per law:      %.2fs", warmup_elapsed / laws_processed)
    elif state == "skipped":
        logger.info("State:            ⏭️  skipped")
        logger.info("Reason:           %s", skip_reason)
    elif state == "failed":
        error_message = result.get("error_message", "unknown error")
        logger.error("State:            ❌ failed")
        logger.error("Error:            %s", error_message)
    else:
        logger.warning("State:            ⚠️  %s", state)

    logger.info("=" * 70)

    # Add overall timing to the result
    result["overall_elapsed_seconds"] = round(overall_elapsed, 1)
    if documents_added > 0 and warmup_elapsed > 0:
        result["throughput_docs_per_sec"] = round(documents_added / warmup_elapsed, 1)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Pre-compute embeddings for the German federal law corpus "
            "using GPU-accelerated HF-TEI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Infrastructure (start before running):\n"
            "  docker compose -f docker-compose.gpu.yml up -d chromadb tei-embeddings\n"
            "\n"
            "Examples:\n"
            "  %(prog)s                          # Full corpus\n"
            "  %(prog)s --max-laws 10            # Quick test\n"
            "  %(prog)s --status                 # Check corpus status\n"
            "  %(prog)s --force                  # Re-ingest everything\n"
        ),
    )
    parser.add_argument(
        "--max-laws",
        type=int,
        default=None,
        help="Maximum number of laws to process (default: all ~2,631).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Documents per embedding batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Concurrent HTML parsing workers (default: {DEFAULT_MAX_WORKERS}).",
    )
    parser.add_argument(
        "--html-root",
        type=str,
        default=DEFAULT_HTML_ROOT,
        help=f"Path to HTML corpus root (default: {DEFAULT_HTML_ROOT}).",
    )
    parser.add_argument(
        "--tei-url",
        type=str,
        default=None,
        help=f"TEI server URL (default: {DEFAULT_TEI_URL} or TEI_URL env).",
    )
    parser.add_argument(
        "--chroma-host",
        type=str,
        default=None,
        help=f"ChromaDB hostname (default: {DEFAULT_CHROMA_HOST} or CHROMA_HOST env).",
    )
    parser.add_argument(
        "--chroma-port",
        type=int,
        default=None,
        help=f"ChromaDB port (default: {DEFAULT_CHROMA_PORT} or CHROMA_PORT env).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-ingestion even if corpus is already populated.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Only check corpus status, don't ingest.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=HEALTH_CHECK_TIMEOUT_SECONDS,
        help=(
            "Seconds to wait for services to become healthy "
            f"(default: {HEALTH_CHECK_TIMEOUT_SECONDS})."
        ),
    )
    return parser


def main() -> int:
    """Entry point for the pre-compute embeddings script.

    Returns:
        Exit code: 0 = success, 1 = failure, 2 = skipped/partial.
    """
    # Install signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    parser = _build_argument_parser()
    arguments = parser.parse_args()

    # Resolve configuration from args → env → defaults
    tei_url = arguments.tei_url or os.environ.get("TEI_URL", DEFAULT_TEI_URL)
    chroma_host = arguments.chroma_host or os.environ.get(
        "CHROMA_HOST", DEFAULT_CHROMA_HOST
    )
    chroma_port = arguments.chroma_port or int(
        os.environ.get("CHROMA_PORT", DEFAULT_CHROMA_PORT)
    )

    # Ensure environment variables are set so that ``app.config.get_settings()``
    # picks up the correct values when warmup imports the config module.
    os.environ.setdefault("USE_TEI", "true")
    os.environ.setdefault("TEI_URL", tei_url)
    os.environ.setdefault("CHROMA_HOST", chroma_host)
    os.environ.setdefault("CHROMA_PORT", str(chroma_port))

    # Override the global health-check timeout if requested
    global HEALTH_CHECK_TIMEOUT_SECONDS
    HEALTH_CHECK_TIMEOUT_SECONDS = arguments.timeout

    # Status-only mode
    if arguments.status:
        _print_corpus_status()
        return 0

    # Run pre-compute
    result = _run_precompute(
        tei_url=tei_url,
        chroma_host=chroma_host,
        chroma_port=chroma_port,
        html_root=arguments.html_root,
        max_laws=arguments.max_laws,
        batch_size=arguments.batch_size,
        max_workers=arguments.max_workers,
        force=arguments.force,
    )

    # Print machine-readable result
    print(json.dumps(result, indent=2))

    # Map state to exit code
    state = result.get("state", "unknown")
    if state == "completed":
        return 0
    elif state == "skipped":
        return 2
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
