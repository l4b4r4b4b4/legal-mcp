"""Corpus warm-up module for Legal-MCP.

Provides background ingestion of the pre-downloaded German federal law corpus
into ChromaDB on server startup. This ensures the German law tools (search_laws,
get_law_by_id, get_law_stats) have data to query immediately.

The warm-up runs in a background thread so the MCP server starts accepting
connections immediately. Non-law tools (custom documents, catalog, secrets)
work while the corpus is loading.

Configuration (environment variables):
    WARMUP_ON_STARTUP: Enable automatic warm-up (default: false)
    WARMUP_MAX_LAWS: Max laws to ingest (default: all). Use 10-50 for testing.
    WARMUP_HTML_ROOT: Path to pre-downloaded HTML corpus (default: data/html)
    WARMUP_BATCH_SIZE: Documents per embedding batch (default: 256)
    WARMUP_MAX_WORKERS: Concurrent HTML parsing workers (default: 8)

Usage:
    # Automatic (via env var)
    WARMUP_ON_STARTUP=true legal-mcp streamable-http

    # Manual (CLI)
    legal-mcp warmup --max-laws 10

    # Programmatic
    from app.warmup import start_background_warmup, get_warmup_status
    start_background_warmup()
    status = get_warmup_status()
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum time (seconds) to wait for ChromaDB / TEI to become reachable
STARTUP_WAIT_TIMEOUT_SECONDS = 120
STARTUP_INITIAL_BACKOFF_SECONDS = 2

# ---------------------------------------------------------------------------
# Warmup state tracking
# ---------------------------------------------------------------------------


class WarmupState(str, Enum):
    """State of the corpus warm-up process."""

    IDLE = "idle"
    CHECKING = "checking"
    SKIPPED = "skipped"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class _WarmupStatus:
    """Thread-safe container for warm-up status information.

    This is a module-level singleton that tracks the current state of the
    warm-up process. It is read by the health/status endpoints and written
    by the background warm-up thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: WarmupState = WarmupState.IDLE
        self.started_at: float | None = None
        self.completed_at: float | None = None
        self.documents_added: int = 0
        self.laws_processed: int = 0
        self.total_laws: int = 0
        self.errors: list[str] = []
        self.error_message: str | None = None
        self.skip_reason: str | None = None

    def set_checking(self) -> None:
        """Mark the warm-up as checking corpus status."""
        with self._lock:
            self.state = WarmupState.CHECKING
            self.started_at = time.time()

    def set_skipped(self, reason: str) -> None:
        """Mark the warm-up as skipped (corpus already populated, etc.)."""
        with self._lock:
            self.state = WarmupState.SKIPPED
            self.skip_reason = reason
            self.completed_at = time.time()

    def set_running(self, total_laws: int) -> None:
        """Mark the warm-up as actively running."""
        with self._lock:
            self.state = WarmupState.RUNNING
            self.total_laws = total_laws

    def update_progress(
        self,
        laws_processed: int,
        documents_added: int,
    ) -> None:
        """Update progress counters (called periodically during ingestion)."""
        with self._lock:
            self.laws_processed = laws_processed
            self.documents_added = documents_added

    def set_completed(
        self,
        documents_added: int,
        laws_processed: int,
        errors: list[str],
    ) -> None:
        """Mark the warm-up as successfully completed."""
        with self._lock:
            self.state = WarmupState.COMPLETED
            self.documents_added = documents_added
            self.laws_processed = laws_processed
            self.errors = errors[:50]  # Cap stored errors
            self.completed_at = time.time()

    def set_failed(self, error_message: str) -> None:
        """Mark the warm-up as failed."""
        with self._lock:
            self.state = WarmupState.FAILED
            self.error_message = error_message
            self.completed_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Return a snapshot of the current status as a dictionary."""
        with self._lock:
            elapsed_seconds: float | None = None
            if self.started_at is not None:
                end_time = self.completed_at or time.time()
                elapsed_seconds = round(end_time - self.started_at, 1)

            return {
                "state": self.state.value,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "elapsed_seconds": elapsed_seconds,
                "documents_added": self.documents_added,
                "laws_processed": self.laws_processed,
                "total_laws": self.total_laws,
                "error_count": len(self.errors),
                "error_message": self.error_message,
                "skip_reason": self.skip_reason,
            }


# Module-level singleton
_warmup_status = _WarmupStatus()
_warmup_thread: threading.Thread | None = None
_warmup_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_warmup_status() -> dict[str, Any]:
    """Get the current warm-up status.

    Returns:
        Dictionary with warm-up state, progress, and timing information.
        Safe to call from any thread at any time.

    Example:
        >>> status = get_warmup_status()
        >>> if status["state"] == "running":
        ...     print(f"Ingesting: {status['laws_processed']}/{status['total_laws']}")
    """
    return _warmup_status.to_dict()


def is_warmup_running() -> bool:
    """Check if a warm-up is currently in progress.

    Returns:
        True if the warm-up background thread is alive and running.
    """
    with _warmup_lock:
        return _warmup_thread is not None and _warmup_thread.is_alive()


def is_corpus_ready() -> bool:
    """Check if the corpus is ready for queries.

    Returns True if the corpus was already populated (warmup skipped) or
    if warm-up completed successfully. Returns False if warm-up is still
    running, failed, or hasn't started.

    Returns:
        True if German law tools should have data to query.
    """
    state = _warmup_status.state
    return state in (WarmupState.COMPLETED, WarmupState.SKIPPED)


def start_background_warmup(
    html_root: Path | str | None = None,
    max_laws: int | None = None,
    batch_size: int = 256,
    max_workers: int = 8,
    force: bool = False,
) -> bool:
    """Start corpus warm-up in a background daemon thread.

    If the corpus is already populated in ChromaDB, the warm-up is skipped
    (unless force=True). If a warm-up is already running, this is a no-op.

    The background thread is a daemon thread so it won't prevent the
    process from exiting on shutdown.

    Args:
        html_root: Path to pre-downloaded HTML corpus. Defaults to
            config's warmup_html_root.
        max_laws: Maximum laws to ingest (None for all). Use 10-50 for
            quick testing.
        batch_size: Documents per embedding batch.
        max_workers: Concurrent workers for HTML parsing.
        force: Force re-ingestion even if corpus is already populated.

    Returns:
        True if a new warm-up thread was started, False if skipped or
        already running.
    """
    global _warmup_thread

    with _warmup_lock:
        if _warmup_thread is not None and _warmup_thread.is_alive():
            logger.info("Warm-up already running, skipping")
            return False

        _warmup_thread = threading.Thread(
            target=_warmup_worker,
            args=(html_root, max_laws, batch_size, max_workers, force),
            name="legal-mcp-warmup",
            daemon=True,
        )
        _warmup_thread.start()
        logger.info("Started background warm-up thread")
        return True


def run_warmup_sync(
    html_root: Path | str | None = None,
    max_laws: int | None = None,
    batch_size: int = 256,
    max_workers: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    """Run corpus warm-up synchronously (blocking).

    This is used by the CLI ``legal-mcp warmup`` command. It blocks until
    ingestion is complete and returns the final status.

    Args:
        html_root: Path to pre-downloaded HTML corpus.
        max_laws: Maximum laws to ingest (None for all).
        batch_size: Documents per embedding batch.
        max_workers: Concurrent workers for HTML parsing.
        force: Force re-ingestion even if corpus is already populated.

    Returns:
        Dictionary with final warm-up status (same format as
        get_warmup_status).
    """
    _warmup_worker(html_root, max_laws, batch_size, max_workers, force)
    return get_warmup_status()


# ---------------------------------------------------------------------------
# Internal worker
# ---------------------------------------------------------------------------


def _resolve_html_root(html_root: Path | str | None) -> Path:
    """Resolve the HTML root directory from argument or config.

    Args:
        html_root: Explicit path, or None to use config default.

    Returns:
        Resolved Path to the HTML root directory.
    """
    if html_root is not None:
        return Path(html_root)

    from app.config import get_settings

    settings = get_settings()
    return Path(settings.warmup_html_root)


def _warmup_worker(
    html_root: Path | str | None,
    max_laws: int | None,
    batch_size: int,
    max_workers: int,
    force: bool,
) -> None:
    """Internal worker function that runs in the warm-up thread.

    This orchestrates the full warm-up lifecycle:
    1. Check if corpus is already populated
    2. Discover and parse local HTML files
    3. Embed and store in ChromaDB
    4. Update status throughout

    All exceptions are caught and recorded in the status — this function
    never raises.

    Args:
        html_root: Path to HTML corpus root.
        max_laws: Maximum laws to ingest.
        batch_size: Documents per embedding batch.
        max_workers: Concurrent parsing workers.
        force: Force re-ingestion.
    """
    try:
        _warmup_status.set_checking()
        resolved_html_root = _resolve_html_root(html_root)

        logger.info("Warm-up: waiting for ChromaDB to be reachable...")

        # Wait for ChromaDB to be reachable before checking corpus status.
        # Without this, a stack restart where legal-mcp starts before
        # chromadb causes get_corpus_status() to fail, returning
        # populated=False, and the warmup re-embeds everything from scratch.
        corpus_status = _wait_for_chromadb()

        if corpus_status is None:
            reason = (
                "ChromaDB not reachable after "
                f"{STARTUP_WAIT_TIMEOUT_SECONDS}s — cannot check corpus. "
                "Proceeding with full ingestion."
            )
            logger.warning("Warm-up: %s", reason)
            corpus_status = {}

        # Check what's already ingested for resume support
        ingested_laws: set[str] = set()
        if not force and corpus_status.get("populated", False):
            from app.ingestion.local_pipeline import get_ingested_law_abbreviations

            ingested_laws = get_ingested_law_abbreviations()
            logger.info(
                "Found %d laws already in ChromaDB (%d documents)",
                len(ingested_laws),
                corpus_status.get("total_documents", 0),
            )

        # Check that HTML root exists and has content (continued after resume check)
        if not resolved_html_root.is_dir():
            reason = f"HTML root directory not found: {resolved_html_root}"
            logger.warning("Warm-up skipped: %s", reason)
            _warmup_status.set_skipped(reason)
            return

        # Count available laws and compare with already-ingested for resume
        from app.ingestion.local_pipeline import discover_local_laws

        available_laws = discover_local_laws(resolved_html_root)
        if not available_laws:
            reason = f"No law directories found in {resolved_html_root}"
            logger.warning("Warm-up skipped: %s", reason)
            _warmup_status.set_skipped(reason)
            return

        # Determine which laws still need ingesting
        available_law_names = {law_abbrev for law_abbrev, _ in available_laws}
        missing_laws = available_law_names - ingested_laws

        if not missing_laws and not force:
            reason = (
                f"Corpus complete: all {len(available_law_names)} laws "
                f"already ingested ({corpus_status.get('total_documents', 0)} documents)"
            )
            logger.info("Warm-up skipped: %s", reason)
            _warmup_status.set_skipped(reason)
            return

        effective_law_count = len(missing_laws) if not force else len(available_laws)
        if max_laws is not None:
            effective_law_count = min(effective_law_count, max_laws)

        if ingested_laws and not force:
            logger.info(
                "Warm-up: resuming — %d/%d laws remaining",
                len(missing_laws),
                len(available_law_names),
            )

        _warmup_status.set_running(total_laws=effective_law_count)
        logger.info(
            "Warm-up: %s ingestion of %d laws from %s",
            "resuming" if ingested_laws and not force else "starting",
            effective_law_count,
            resolved_html_root,
        )

        # Wait for TEI embedding server before starting ingestion
        if not _wait_for_tei():
            _warmup_status.set_failed(
                f"TEI server not reachable after {STARTUP_WAIT_TIMEOUT_SECONDS}s"
            )
            return

        # Run the actual ingestion
        from app.ingestion.local_pipeline import ingest_from_local_html

        def progress_callback(progress: Any) -> None:
            """Forward progress to the status tracker."""
            _warmup_status.update_progress(
                laws_processed=progress.processed_laws,
                documents_added=progress.documents_added,
            )

        # Pass already-ingested laws so ingestion skips them (resume)
        skip_existing = ingested_laws if ingested_laws and not force else None

        result = ingest_from_local_html(
            html_root=resolved_html_root,
            max_laws=max_laws,
            batch_size=batch_size,
            max_workers=max_workers,
            progress_callback=progress_callback,
            skip_laws=skip_existing,
        )

        _warmup_status.set_completed(
            documents_added=result.documents_added,
            laws_processed=result.laws_processed,
            errors=result.errors,
        )

        logger.info(
            "Warm-up completed: %d documents from %d laws in %.1f seconds (%d errors)",
            result.documents_added,
            result.laws_processed,
            result.elapsed_seconds,
            len(result.errors),
        )

    except Exception as error:
        error_message = f"Warm-up failed: {error}"
        logger.error(error_message, exc_info=True)
        _warmup_status.set_failed(error_message)


# ---------------------------------------------------------------------------
# Startup retry helpers
# ---------------------------------------------------------------------------


def _wait_for_chromadb() -> dict[str, Any] | None:
    """Wait for ChromaDB to become reachable, with exponential backoff.

    Retries ``get_corpus_status()`` until it succeeds (no ``error`` key)
    or the timeout is exceeded.

    Returns:
        The corpus status dict on success, or ``None`` if ChromaDB was
        never reachable within the timeout.
    """
    from app.ingestion.local_pipeline import get_corpus_status

    deadline = time.monotonic() + STARTUP_WAIT_TIMEOUT_SECONDS
    backoff = STARTUP_INITIAL_BACKOFF_SECONDS

    while True:
        corpus_status = get_corpus_status()

        if corpus_status.get("error") is None:
            logger.info("ChromaDB is reachable")
            return corpus_status

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "ChromaDB not reachable after %ds: %s",
                STARTUP_WAIT_TIMEOUT_SECONDS,
                corpus_status.get("error"),
            )
            return None

        wait_time = min(backoff, remaining)
        logger.info(
            "ChromaDB not ready, retrying in %ds: %s",
            wait_time,
            corpus_status.get("error"),
        )
        time.sleep(wait_time)
        backoff = min(backoff * 2, 30)


def _wait_for_tei() -> bool:
    """Wait for the TEI embedding server to become reachable.

    Retries a health check against the configured TEI URL until it
    succeeds or the timeout is exceeded.  This prevents the warmup from
    crashing immediately when TEI starts slower than legal-mcp.

    Returns:
        ``True`` if TEI became reachable, ``False`` on timeout.
    """
    from app.config import get_settings

    settings = get_settings()
    if not settings.use_tei:
        return True  # Not using TEI — nothing to wait for

    import httpx

    tei_url = settings.tei_url
    deadline = time.monotonic() + STARTUP_WAIT_TIMEOUT_SECONDS
    backoff = STARTUP_INITIAL_BACKOFF_SECONDS

    while True:
        try:
            response = httpx.get(f"{tei_url}/health", timeout=5.0)
            if response.status_code == 200:
                logger.info("TEI server is reachable at %s", tei_url)
                return True
        except Exception as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "TEI server not reachable after %ds at %s: %s",
                    STARTUP_WAIT_TIMEOUT_SECONDS,
                    tei_url,
                    error,
                )
                return False

            wait_time = min(backoff, remaining)
            logger.info(
                "TEI not ready, retrying in %ds: %s",
                wait_time,
                error,
            )
            time.sleep(wait_time)
            backoff = min(backoff * 2, 30)


__all__ = [
    "WarmupState",
    "get_warmup_status",
    "is_corpus_ready",
    "is_warmup_running",
    "run_warmup_sync",
    "start_background_warmup",
]
