#!/usr/bin/env python3
"""Robust ingestion script for priority German laws.

This script ingests the most important German laws (BGB, GG, StGB, etc.)
with proper rate limiting to avoid overwhelming gesetze-im-internet.de.

Features:
- Sequential processing within each law (avoids rate limiting)
- Configurable delay between requests
- Progress logging with ETA
- Graceful error handling and recovery
- Resume capability (skips already-ingested laws)

Usage:
    # From project root with TEI backend
    USE_TEI=true python scripts/ingest_priority_laws.py

    # With custom settings
    USE_TEI=true python scripts/ingest_priority_laws.py --delay 0.2 --workers 4
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.config import get_settings  # noqa: E402
from app.ingestion.embeddings import GermanLawEmbeddingStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Priority laws - most important German federal laws
PRIORITY_LAWS = [
    "GG",  # Grundgesetz (Constitution) - ~200 norms
    "BGB",  # Bürgerliches Gesetzbuch (Civil Code) - ~2500 norms
    "StGB",  # Strafgesetzbuch (Criminal Code) - ~400 norms
    "ZPO",  # Zivilprozessordnung (Civil Procedure) - ~1100 norms
    "StPO",  # Strafprozessordnung (Criminal Procedure) - ~500 norms
    "HGB",  # Handelsgesetzbuch (Commercial Code) - ~500 norms
]


@dataclass
class IngestionStats:
    """Track ingestion statistics."""

    total_laws: int = 0
    completed_laws: int = 0
    total_norms: int = 0
    processed_norms: int = 0
    total_documents: int = 0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    lock: Lock = field(default_factory=Lock)

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds since start."""
        return time.time() - self.start_time

    @property
    def rate(self) -> float:
        """Processing rate in norms per second."""
        if self.elapsed == 0:
            return 0.0
        return self.processed_norms / self.elapsed

    def log_progress(self, law: str) -> None:
        """Log current progress."""
        pct = (
            (self.processed_norms / self.total_norms * 100)
            if self.total_norms > 0
            else 0
        )
        remaining = self.total_norms - self.processed_norms
        eta = remaining / self.rate if self.rate > 0 else 0

        logger.info(
            "[%s] %d/%d norms (%.1f%%) | %d docs | %.1f norms/sec | ETA: %.0fs",
            law,
            self.processed_norms,
            self.total_norms,
            pct,
            self.total_documents,
            self.rate,
            eta,
        )


def load_norm_with_retry(
    law_abbrev: str,
    norm_url: str,
    max_retries: int = 5,
    base_delay: float = 1.0,
) -> tuple[list, str | None]:
    """Load a single norm with aggressive retry logic.

    Args:
        law_abbrev: Law abbreviation
        norm_url: URL to fetch
        max_retries: Maximum retry attempts
        base_delay: Base delay between retries (doubles each time)

    Returns:
        Tuple of (documents, error_message)
    """
    from legal_mcp.loaders import GermanLawHTMLLoader

    last_error = None

    for attempt in range(max_retries):
        try:
            loader = GermanLawHTMLLoader(url=norm_url, law_abbrev=law_abbrev)
            docs = loader.load()
            return (docs, None)
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                time.sleep(delay)

    return ([], f"Failed {norm_url}: {last_error}")


def ingest_law(
    law_abbrev: str,
    store: GermanLawEmbeddingStore,
    stats: IngestionStats,
    request_delay: float = 0.15,
    max_workers: int = 4,
    batch_size: int = 128,
) -> dict:
    """Ingest a single law with controlled concurrency.

    Args:
        law_abbrev: Law abbreviation (e.g., "BGB")
        store: Embedding store instance
        stats: Shared statistics tracker
        request_delay: Delay between requests in seconds
        max_workers: Number of concurrent workers
        batch_size: Documents per embedding batch

    Returns:
        Dictionary with ingestion results
    """
    from legal_mcp.loaders.discovery import GermanLawDiscovery, LawInfo

    logger.info("=" * 60)
    logger.info("Starting %s ingestion", law_abbrev)
    logger.info("=" * 60)

    discovery = GermanLawDiscovery()
    law_url = f"https://www.gesetze-im-internet.de/{law_abbrev.lower()}/"
    law = LawInfo(abbreviation=law_abbrev, title="", url=law_url)

    # Discover norms
    try:
        norms = list(discovery.discover_norms(law))
        logger.info("[%s] Found %d norms", law_abbrev, len(norms))
    except Exception as e:
        error_msg = f"Failed to discover norms for {law_abbrev}: {e}"
        logger.error(error_msg)
        return {"law": law_abbrev, "documents": 0, "norms": 0, "errors": [error_msg]}

    with stats.lock:
        stats.total_norms += len(norms)

    # Prepare tasks
    tasks = [(law_abbrev, norm.url) for norm in norms]

    documents_batch: list = []
    law_docs = 0
    law_norms = 0
    law_errors: list[str] = []
    batch_lock = Lock()
    request_lock = Lock()
    last_request_time = [0.0]  # Mutable container for closure

    def process_norm(task: tuple[str, str]) -> tuple[list, str | None]:
        """Process a single norm with rate limiting."""
        abbrev, url = task

        # Rate limiting - ensure minimum delay between requests
        with request_lock:
            elapsed = time.time() - last_request_time[0]
            if elapsed < request_delay:
                time.sleep(request_delay - elapsed)
            last_request_time[0] = time.time()

        return load_norm_with_retry(abbrev, url)

    # Process with controlled concurrency
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_norm, task): task for task in tasks}

        for future in as_completed(futures):
            docs, error = future.result()

            if error:
                law_errors.append(error)
                with stats.lock:
                    stats.errors.append(error)
            else:
                with batch_lock:
                    documents_batch.extend(docs)
                    law_norms += 1

                    with stats.lock:
                        stats.processed_norms += 1

                        # Log progress every 50 norms
                        if stats.processed_norms % 50 == 0:
                            stats.log_progress(law_abbrev)

                    # Batch insert
                    if len(documents_batch) >= batch_size:
                        added = store.add_documents(
                            documents_batch, show_progress=False
                        )
                        law_docs += added
                        with stats.lock:
                            stats.total_documents += added
                        logger.info(
                            "[%s] Batch: +%d docs (total: %d)",
                            law_abbrev,
                            added,
                            law_docs,
                        )
                        documents_batch = []

    # Final batch
    if documents_batch:
        added = store.add_documents(documents_batch, show_progress=False)
        law_docs += added
        with stats.lock:
            stats.total_documents += added

    with stats.lock:
        stats.completed_laws += 1

    logger.info(
        "[%s] Complete: %d documents, %d/%d norms (%d errors)",
        law_abbrev,
        law_docs,
        law_norms,
        len(norms),
        len(law_errors),
    )

    return {
        "law": law_abbrev,
        "documents": law_docs,
        "norms": law_norms,
        "total_norms": len(norms),
        "errors": law_errors,
    }


def check_existing_laws(store: GermanLawEmbeddingStore) -> set[str]:
    """Check which laws are already ingested.

    Returns:
        Set of law abbreviations already in the store
    """
    try:
        collection = store.collection
        if collection.count() == 0:
            return set()

        # Sample to find existing laws
        result = collection.get(include=["metadatas"], limit=50000)
        existing = {
            m.get("law_abbrev") for m in result["metadatas"] if m.get("law_abbrev")
        }
        return existing
    except Exception:
        return set()


def main() -> None:
    """Main ingestion entry point."""
    parser = argparse.ArgumentParser(description="Ingest priority German laws")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay between requests in seconds (default: 0.15)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent workers per law (default: 4)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Documents per embedding batch (default: 128)",
    )
    parser.add_argument(
        "--laws",
        type=str,
        default=None,
        help="Comma-separated list of laws to ingest (default: all priority)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip laws that are already ingested",
    )
    args = parser.parse_args()

    # Determine which laws to ingest
    if args.laws:
        laws = [law.strip().upper() for law in args.laws.split(",")]
    else:
        laws = PRIORITY_LAWS.copy()

    settings = get_settings()
    store = GermanLawEmbeddingStore(
        model_name=settings.embedding_model,
        persist_path=Path(settings.chroma_persist_path),
    )

    # Check for existing laws
    if args.skip_existing:
        existing = check_existing_laws(store)
        if existing:
            logger.info("Found existing laws: %s", existing)
            laws = [law for law in laws if law not in existing]
            if not laws:
                logger.info("All laws already ingested!")
                return

    logger.info("=" * 60)
    logger.info("Priority Laws Ingestion")
    logger.info("=" * 60)
    logger.info("Laws to ingest: %s", laws)
    logger.info("Request delay: %.2fs", args.delay)
    logger.info("Workers per law: %d", args.workers)
    logger.info("Batch size: %d", args.batch_size)
    logger.info("TEI enabled: %s", os.getenv("USE_TEI", "false"))
    logger.info("=" * 60)

    stats = IngestionStats(total_laws=len(laws))
    results: list[dict] = []

    for law in laws:
        result = ingest_law(
            law_abbrev=law,
            store=store,
            stats=stats,
            request_delay=args.delay,
            max_workers=args.workers,
            batch_size=args.batch_size,
        )
        results.append(result)

        # Delay between laws
        if law != laws[-1]:
            logger.info("Waiting 3s before next law...")
            time.sleep(3)

    # Final summary
    logger.info("=" * 60)
    logger.info("INGESTION COMPLETE")
    logger.info("=" * 60)
    logger.info("Total time: %.1f minutes", stats.elapsed / 60)
    logger.info("Total documents: %d", stats.total_documents)
    logger.info("Total norms: %d", stats.processed_norms)
    logger.info("Total errors: %d", len(stats.errors))
    logger.info("Rate: %.1f norms/sec", stats.rate)
    logger.info("")

    for result in results:
        status = (
            "✅"
            if result["norms"] == result.get("total_norms", result["norms"])
            else "⚠️"
        )
        logger.info(
            "  %s %s: %d docs, %d/%d norms, %d errors",
            status,
            result["law"],
            result["documents"],
            result["norms"],
            result.get("total_norms", result["norms"]),
            len(result["errors"]),
        )

    if stats.errors:
        logger.warning("")
        logger.warning("Errors (first 10):")
        for err in stats.errors[:10]:
            logger.warning("  - %s", err[:100])


if __name__ == "__main__":
    main()
