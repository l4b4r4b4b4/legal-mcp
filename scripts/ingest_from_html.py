#!/usr/bin/env python3
"""Ingest local HTML files into ChromaDB embeddings.

Processes pre-downloaded HTML files from data/html/ directory
into ChromaDB vector embeddings using TEI backend.

Features:
- Processes local files (no network latency)
- Parallel processing with ThreadPoolExecutor
- TEI backend for fast GPU embeddings
- Progress tracking with ETA
- Resume capability (skips already ingested laws)

Usage:
    # Process all downloaded HTML files
    USE_TEI=true python scripts/ingest_from_html.py

    # Process specific laws
    USE_TEI=true python scripts/ingest_from_html.py --laws BGB,GG,StGB

    # Customize batch size and workers
    USE_TEI=true python scripts/ingest_from_html.py --batch-size 256 --workers 16
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.documents import Document

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


@dataclass
class IngestStats:
    """Track ingestion statistics."""

    total_files: int = 0
    processed_files: int = 0
    total_documents: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    lock: Lock = field(default_factory=Lock)

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds."""
        return time.time() - self.start_time

    @property
    def rate(self) -> float:
        """Processing rate in files per second."""
        if self.elapsed == 0:
            return 0.0
        return self.processed_files / self.elapsed

    def log_progress(self, current_law: str = "") -> None:
        """Log current progress."""
        total_done = self.processed_files + self.skipped_files + self.failed_files
        pct = (total_done / self.total_files * 100) if self.total_files > 0 else 0
        remaining = self.total_files - total_done
        eta = remaining / self.rate if self.rate > 0 else 0

        prefix = f"[{current_law}] " if current_law else ""
        logger.info(
            "%s%d/%d (%.1f%%) | %d docs | %.1f files/sec | ETA: %.0fs",
            prefix,
            total_done,
            self.total_files,
            pct,
            self.total_documents,
            self.rate,
            eta,
        )


def parse_html_file(html_path: Path, law_abbrev: str) -> list[Document]:
    """Parse a single HTML file into LangChain Documents.

    Args:
        html_path: Path to the HTML file
        law_abbrev: Law abbreviation (e.g., "BGB")

    Returns:
        List of Document objects
    """
    from langchain_core.documents import Document
    from selectolax.parser import HTMLParser

    # Read HTML content
    html_content = html_path.read_text(encoding="iso-8859-1")
    tree = HTMLParser(html_content)

    # Extract law title (h1)
    h1 = tree.css_first("h1")
    law_title = h1.text(strip=True) if h1 else ""

    # Extract norm identifier (§ 433, Art 1, etc.)
    norm_id_elem = tree.css_first("span.jnenbez")
    norm_id = norm_id_elem.text(strip=True) if norm_id_elem else ""

    # Extract norm title (optional)
    norm_title_elem = tree.css_first("span.jnentitel")
    norm_title = norm_title_elem.text(strip=True) if norm_title_elem else ""

    # Extract all paragraphs (Absätze)
    paragraph_elements = tree.css("div.jurAbsatz")
    paragraphs = [elem.text(strip=True) for elem in paragraph_elements]

    # Skip empty norms
    if not paragraphs:
        return []

    # Combine all paragraphs into full text
    full_text = "\n\n".join(paragraphs)

    # Base metadata
    base_metadata = {
        "jurisdiction": "de-federal",
        "law_abbrev": law_abbrev.upper(),
        "law_title": law_title,
        "norm_id": norm_id,
        "norm_title": norm_title,
        "source_url": f"https://www.gesetze-im-internet.de/{law_abbrev.lower()}/{html_path.name}",
        "source_type": "html",
        "source_file": str(html_path),
    }

    documents: list[Document] = []

    # Document 1: Full norm
    norm_doc_id = (
        f"{law_abbrev.lower()}_{norm_id.replace('§', 'para').replace(' ', '_').lower()}"
    )
    norm_doc = Document(
        page_content=full_text,
        metadata={
            **base_metadata,
            "level": "norm",
            "doc_id": norm_doc_id,
            "paragraph_count": len(paragraphs),
        },
    )
    documents.append(norm_doc)

    # Documents 2+: Individual paragraphs (for fine-grained retrieval)
    if len(paragraphs) > 1:
        for i, paragraph_text in enumerate(paragraphs, 1):
            if not paragraph_text.strip():
                continue
            para_doc = Document(
                page_content=paragraph_text,
                metadata={
                    **base_metadata,
                    "level": "paragraph",
                    "doc_id": f"{norm_doc_id}_abs_{i}",
                    "paragraph_index": i,
                    "parent_norm_id": norm_doc_id,
                },
            )
            documents.append(para_doc)

    return documents


def process_law_directory(
    law_dir: Path,
    store: GermanLawEmbeddingStore,
    stats: IngestStats,
    max_workers: int = 8,
    batch_size: int = 128,
) -> dict:
    """Process all HTML files in a law directory.

    Args:
        law_dir: Directory containing HTML files for a law
        store: Embedding store
        stats: Statistics tracker
        max_workers: Number of concurrent workers for parsing
        batch_size: Documents per embedding batch

    Returns:
        Dictionary with processing results
    """
    law_abbrev = law_dir.name.upper()
    html_files = list(law_dir.glob("*.html"))

    if not html_files:
        return {"law": law_abbrev, "documents": 0, "files": 0, "errors": []}

    logger.info("[%s] Processing %d HTML files...", law_abbrev, len(html_files))

    with stats.lock:
        stats.total_files += len(html_files)

    documents_batch: list[Document] = []
    law_documents = 0
    law_errors: list[str] = []
    batch_lock = Lock()

    def process_file(html_path: Path) -> tuple[list[Document], str | None]:
        """Process a single HTML file."""
        try:
            docs = parse_html_file(html_path, law_abbrev)
            return (docs, None)
        except Exception as e:
            return ([], f"Error parsing {html_path}: {e}")

    # Process files in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_file, f): f for f in html_files}

        for future in as_completed(futures):
            docs, error = future.result()

            if error:
                with stats.lock:
                    stats.failed_files += 1
                    stats.errors.append(error)
                law_errors.append(error)
            elif docs:
                with batch_lock:
                    documents_batch.extend(docs)

                    with stats.lock:
                        stats.processed_files += 1

                    # Batch insert when we have enough documents
                    if len(documents_batch) >= batch_size:
                        added = store.add_documents(
                            documents_batch, show_progress=False
                        )
                        law_documents += added
                        with stats.lock:
                            stats.total_documents += added
                        logger.info(
                            "[%s] Batch: +%d docs (total: %d)",
                            law_abbrev,
                            added,
                            law_documents,
                        )
                        documents_batch = []
            else:
                # Empty document (no paragraphs)
                with stats.lock:
                    stats.skipped_files += 1

    # Insert remaining documents
    if documents_batch:
        added = store.add_documents(documents_batch, show_progress=False)
        law_documents += added
        with stats.lock:
            stats.total_documents += added

    logger.info(
        "[%s] Complete: %d documents from %d files (%d errors)",
        law_abbrev,
        law_documents,
        len(html_files),
        len(law_errors),
    )

    return {
        "law": law_abbrev,
        "documents": law_documents,
        "files": len(html_files),
        "errors": law_errors,
    }


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Ingest local HTML files into ChromaDB"
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/html",
        help="Input directory with HTML files (default: data/html)",
    )
    parser.add_argument(
        "--laws",
        type=str,
        default=None,
        help="Comma-separated list of laws to process (default: all)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Concurrent workers for parsing (default: 16)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Documents per embedding batch (default: 128)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete existing collection before ingesting",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        logger.error("Input directory does not exist: %s", input_dir)
        sys.exit(1)

    # Find law directories
    law_dirs = sorted([d for d in input_dir.iterdir() if d.is_dir()])

    if args.laws:
        # Filter to specific laws
        requested = {law.strip().lower() for law in args.laws.split(",")}
        law_dirs = [d for d in law_dirs if d.name.lower() in requested]

    if not law_dirs:
        logger.error("No law directories found in %s", input_dir)
        sys.exit(1)

    settings = get_settings()

    # Initialize store
    store = GermanLawEmbeddingStore(
        model_name=settings.embedding_model,
        persist_path=Path(settings.chroma_persist_path),
    )

    # Optionally clean existing collection
    if args.clean:
        logger.warning("Deleting existing collection...")
        try:
            store.client.delete_collection("german_laws")
            # Reinitialize store after deletion
            store = GermanLawEmbeddingStore(
                model_name=settings.embedding_model,
                persist_path=Path(settings.chroma_persist_path),
            )
        except Exception as e:
            logger.warning("Could not delete collection: %s", e)

    logger.info("=" * 60)
    logger.info("German Law HTML Ingestion")
    logger.info("=" * 60)
    logger.info("Input directory: %s", input_dir.absolute())
    logger.info("Laws to process: %d", len(law_dirs))
    logger.info("Workers: %d", args.workers)
    logger.info("Batch size: %d", args.batch_size)
    logger.info("ChromaDB path: %s", settings.chroma_persist_path)
    logger.info("Using TEI: %s", settings.use_tei)
    logger.info("=" * 60)

    stats = IngestStats()
    results = []

    for law_dir in law_dirs:
        result = process_law_directory(
            law_dir=law_dir,
            store=store,
            stats=stats,
            max_workers=args.workers,
            batch_size=args.batch_size,
        )
        results.append(result)
        stats.log_progress()

    # Final summary
    logger.info("=" * 60)
    logger.info("INGESTION COMPLETE")
    logger.info("=" * 60)
    logger.info("Total time: %.1f minutes", stats.elapsed / 60)
    logger.info("Total documents: %d", stats.total_documents)
    logger.info("Files processed: %d", stats.processed_files)
    logger.info("Files skipped: %d (empty)", stats.skipped_files)
    logger.info("Files failed: %d", stats.failed_files)
    logger.info("Rate: %.1f files/sec", stats.rate)
    logger.info("")

    # Sort results
    results.sort(key=lambda x: x["law"])

    for result in results:
        status = "✅" if not result["errors"] else "⚠️"
        logger.info(
            "  %s %s: %d docs from %d files",
            status,
            result["law"],
            result["documents"],
            result["files"],
        )

    if stats.errors:
        logger.warning("")
        logger.warning("Errors (first 20):")
        for err in stats.errors[:20]:
            logger.warning("  - %s", err[:100])

    # Show final collection stats
    logger.info("")
    logger.info("Final collection size: %d documents", store.collection.count())


if __name__ == "__main__":
    main()
