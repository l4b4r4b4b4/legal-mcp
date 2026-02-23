"""Local HTML ingestion pipeline for pre-downloaded German federal law corpus.

Reads pre-downloaded HTML files from the local filesystem (data/html/) and
ingests them into ChromaDB via the configured embedding backend (TEI or local).

This avoids network IO to gesetze-im-internet.de at runtime — all HTML is
already on disk. The pipeline:

1. Discovery: Walk data/html/ directories to find law dirs + HTML files
2. Parsing: Extract structured content using selectolax (same logic as
   GermanLawHTMLLoader but reads from disk)
3. Embedding: Batch-embed via TEI server or local sentence-transformers
4. Storage: Upsert into ChromaDB with rich metadata

Usage:
    >>> from app.ingestion.local_pipeline import ingest_from_local_html
    >>> result = ingest_from_local_html(Path("data/html"), max_laws=10)
    >>> print(f"Ingested {result.documents_added} documents")

    >>> # Full corpus (~50K norms, takes 30-60 min with TEI)
    >>> result = ingest_from_local_html(Path("data/html"))
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

from langchain_core.documents import Document
from selectolax.parser import HTMLParser

from app.config import get_settings
from app.ingestion.embeddings import GermanLawEmbeddingStore
from app.ingestion.pipeline import IngestionProgress, IngestionResult

logger = logging.getLogger(__name__)

# Files to skip when scanning law directories
SKIP_PATTERNS = frozenset(
    {
        "index.html",
        "gesamt.html",
    }
)

# Substrings in filenames to skip (PDF links, XML, EPUB, meta pages)
SKIP_SUBSTRINGS = ("bjnr", "gesamt", "xml", "epub", "pdf")


def discover_local_laws(html_root: Path) -> list[tuple[str, list[Path]]]:
    """Discover all law directories and their HTML norm files on disk.

    Walks the html_root directory and returns a sorted list of
    (law_abbreviation, [html_file_paths]) tuples.

    The directory name is used as the lowercase law abbreviation.
    Only .html files that look like norm pages are included (index.html,
    gesamt.html, and meta files are skipped).

    Args:
        html_root: Root directory containing law subdirectories
            (e.g., data/html/).

    Returns:
        Sorted list of (law_abbrev, [Path, ...]) tuples. Each law_abbrev
        is the directory name (lowercase). Paths are sorted alphabetically.

    Raises:
        FileNotFoundError: If html_root does not exist.
    """
    if not html_root.is_dir():
        raise FileNotFoundError(f"HTML root directory not found: {html_root}")

    laws: list[tuple[str, list[Path]]] = []

    for law_directory in sorted(html_root.iterdir()):
        if not law_directory.is_dir():
            continue

        law_abbreviation = law_directory.name  # e.g., "bgb", "stgb", "gg"

        html_files: list[Path] = []
        for html_file in sorted(law_directory.glob("*.html")):
            filename_lower = html_file.name.lower()

            # Skip non-norm files
            if filename_lower in SKIP_PATTERNS:
                continue
            if any(substring in filename_lower for substring in SKIP_SUBSTRINGS):
                continue

            html_files.append(html_file)

        if html_files:
            laws.append((law_abbreviation, html_files))

    logger.info(
        "Discovered %d laws with %d total HTML files in %s",
        len(laws),
        sum(len(files) for _, files in laws),
        html_root,
    )

    return laws


def parse_local_html_file(
    html_file_path: Path,
    law_abbreviation: str,
    jurisdiction: str = "de-federal",
) -> list[Document]:
    """Parse a single local HTML file into LangChain Documents.

    Extracts the same structured content as GermanLawHTMLLoader but reads
    from disk instead of HTTP. Produces one document per norm (full text)
    plus one document per paragraph for multi-paragraph norms.

    HTML structure expected (gesetze-im-internet.de format):
        - <h1>: Law title
        - <span class="jnenbez">: Norm identifier (§ 433, Art 1, etc.)
        - <span class="jnentitel">: Norm title (optional)
        - <div class="jurAbsatz">: Each paragraph (Absatz)

    Args:
        html_file_path: Path to the HTML file on disk.
        law_abbreviation: Law abbreviation (e.g., "bgb", "stgb"). Will be
            uppercased in metadata.
        jurisdiction: Legal jurisdiction identifier.

    Returns:
        List of LangChain Document objects. Empty list if parsing fails
        or the file has no content.
    """
    try:
        # gesetze-im-internet.de uses ISO-8859-1 encoding
        html_content = html_file_path.read_text(encoding="iso-8859-1")
    except (OSError, UnicodeDecodeError) as error:
        logger.warning("Failed to read %s: %s", html_file_path, error)
        return []

    tree = HTMLParser(html_content)

    # Extract law title
    h1_element = tree.css_first("h1")
    law_title = h1_element.text(strip=True) if h1_element else ""

    # Extract norm identifier (§ 433, Art 1, etc.)
    norm_id_element = tree.css_first("span.jnenbez")
    norm_id = norm_id_element.text(strip=True) if norm_id_element else ""

    # Extract norm title
    norm_title_element = tree.css_first("span.jnentitel")
    norm_title = norm_title_element.text(strip=True) if norm_title_element else ""

    # Extract all paragraphs (Absätze)
    paragraph_elements = tree.css("div.jurAbsatz")
    paragraphs = [element.text(strip=True) for element in paragraph_elements]

    # Skip empty norms (e.g., repealed sections)
    full_text = "\n\n".join(paragraphs)
    if not full_text.strip():
        return []

    # Uppercase the abbreviation for metadata consistency
    law_abbreviation_upper = law_abbreviation.upper()

    # Sanitize norm_id for use in doc_id
    sanitized_norm_id = (
        norm_id.replace("§", "para").replace(" ", "_").replace(".", "").lower()
    )

    # Build source URL from file path (for reference/debugging)
    source_url = (
        f"https://www.gesetze-im-internet.de/{law_abbreviation}/{html_file_path.name}"
    )

    # Base metadata
    base_metadata: dict[str, Any] = {
        "jurisdiction": jurisdiction,
        "law_abbrev": law_abbreviation_upper,
        "law_title": law_title,
        "norm_id": norm_id,
        "norm_title": norm_title,
        "source_url": source_url,
        "source_type": "local_html",
        "source_file": str(html_file_path),
    }

    documents: list[Document] = []

    # Document 1: Full norm (all paragraphs combined)
    norm_doc_id = (
        f"{law_abbreviation}_{sanitized_norm_id}"
        if sanitized_norm_id
        else f"{law_abbreviation}_{html_file_path.stem}"
    )
    documents.append(
        Document(
            page_content=full_text,
            metadata={
                **base_metadata,
                "level": "norm",
                "doc_id": norm_doc_id,
                "paragraph_count": len(paragraphs),
            },
        )
    )

    # Documents 2+: Individual paragraphs (for fine-grained retrieval)
    if len(paragraphs) > 1:
        for paragraph_index, paragraph_text in enumerate(paragraphs, 1):
            if not paragraph_text.strip():
                continue
            documents.append(
                Document(
                    page_content=paragraph_text,
                    metadata={
                        **base_metadata,
                        "level": "paragraph",
                        "doc_id": f"{norm_doc_id}_abs_{paragraph_index}",
                        "paragraph_index": paragraph_index,
                        "parent_norm_id": norm_doc_id,
                    },
                )
            )

    return documents


def _parse_law_directory(
    law_abbreviation: str,
    html_files: list[Path],
) -> tuple[str, list[Document], list[str]]:
    """Parse all HTML files for a single law into Documents.

    This is the unit of work for concurrent processing. It runs in a
    thread pool worker and returns results for batch embedding.

    Args:
        law_abbreviation: Law directory name (lowercase).
        html_files: List of HTML file paths for this law.

    Returns:
        Tuple of (law_abbreviation, documents, errors).
    """
    documents: list[Document] = []
    errors: list[str] = []

    for html_file in html_files:
        try:
            file_documents = parse_local_html_file(html_file, law_abbreviation)
            documents.extend(file_documents)
        except Exception as error:
            error_message = f"Error parsing {html_file}: {error}"
            errors.append(error_message)
            logger.debug(error_message)

    return (law_abbreviation, documents, errors)


def ingest_from_local_html(
    html_root: Path,
    persist_path: Path | str | None = None,
    max_laws: int | None = None,
    batch_size: int = 256,
    max_workers: int = 8,
    progress_callback: Any | None = None,
    skip_laws: set[str] | None = None,
) -> IngestionResult:
    """Ingest pre-downloaded German federal law HTML into ChromaDB.

    This is the main entry point for local corpus ingestion. It discovers
    all law directories under html_root, parses HTML files into Documents,
    and batch-embeds them into ChromaDB.

    Supports **resume**: pass ``skip_laws`` with a set of law abbreviations
    already in ChromaDB to skip re-embedding them.

    Processing flow:
        1. Discover law directories and HTML files
        2. Filter out already-ingested laws (if skip_laws provided)
        3. Parse HTML concurrently (I/O bound: disk reads)
        4. Batch-embed documents (CPU/GPU bound: TEI or local model)
        5. Upsert into ChromaDB

    Args:
        html_root: Root directory containing law subdirectories.
        persist_path: Override ChromaDB persistence path (uses config default
            if None).
        max_laws: Maximum number of laws to process (None for all). Useful
            for testing with small values (10-50).
        batch_size: Documents per embedding batch. Larger batches are more
            efficient but use more memory.
        max_workers: Number of concurrent workers for HTML parsing.
        progress_callback: Optional callback(IngestionProgress) for progress
            updates during ingestion.
        skip_laws: Optional set of law abbreviations (directory names) to
            skip. Used for resume — pass the output of
            ``get_ingested_law_abbreviations()`` to avoid re-embedding
            laws that are already in ChromaDB.

    Returns:
        IngestionResult with document counts, timing, and any errors.

    Raises:
        FileNotFoundError: If html_root does not exist.

    Example:
        >>> # Quick test with 5 laws
        >>> result = ingest_from_local_html(Path("data/html"), max_laws=5)
        >>> print(f"Added {result.documents_added} documents")

        >>> # Resume after interruption
        >>> existing = get_ingested_law_abbreviations()
        >>> result = ingest_from_local_html(Path("data/html"), skip_laws=existing)

    Timing estimates (with TEI on CPU):
        - 10 laws: ~1-2 minutes
        - 100 laws: ~10-15 minutes
        - 1000 laws: ~60-90 minutes
        - All (~6400 laws): ~4-8 hours (CPU TEI)
    """
    settings = get_settings()
    progress = IngestionProgress()

    # Resolve ChromaDB path
    store_path = (
        Path(persist_path) if persist_path else Path(settings.chroma_persist_path)
    )

    # Initialize embedding store — pass host/port for HttpClient support
    store = GermanLawEmbeddingStore(
        model_name=settings.embedding_model,
        persist_path=store_path,
        chroma_host=settings.chroma_host,
        chroma_port=settings.chroma_port,
    )

    # Phase 1: Discovery
    logger.info("Discovering local HTML corpus in %s...", html_root)
    all_laws = discover_local_laws(html_root)

    # Filter out already-ingested laws for resume support
    if skip_laws:
        before_count = len(all_laws)
        all_laws = [
            (law_abbrev, files)
            for law_abbrev, files in all_laws
            if law_abbrev not in skip_laws
        ]
        skipped_count = before_count - len(all_laws)
        if skipped_count > 0:
            logger.info(
                "Resume: skipping %d already-ingested laws, %d remaining",
                skipped_count,
                len(all_laws),
            )

    if max_laws is not None:
        all_laws = all_laws[:max_laws]

    progress.total_laws = len(all_laws)
    total_files = sum(len(files) for _, files in all_laws)
    progress.total_norms = total_files

    logger.info(
        "Will process %d laws with %d HTML files (max_laws=%s)",
        progress.total_laws,
        total_files,
        max_laws,
    )

    # Phase 2: Parse HTML concurrently
    # Parsing is I/O-bound (disk reads) so concurrency helps significantly
    document_batch: list[Document] = []
    batch_lock = Lock()
    processed_laws_count = 0

    logger.info("Parsing HTML files with %d workers...", max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _parse_law_directory, law_abbreviation, html_files
            ): law_abbreviation
            for law_abbreviation, html_files in all_laws
        }

        for future in as_completed(futures):
            law_abbreviation = futures[future]

            try:
                _, documents, errors = future.result()
            except Exception as unexpected_error:
                error_message = (
                    f"Unexpected error processing {law_abbreviation}: "
                    f"{unexpected_error}"
                )
                progress.errors.append(error_message)
                logger.error(error_message)
                continue

            # Accumulate errors
            progress.errors.extend(errors)

            if documents:
                with batch_lock:
                    document_batch.extend(documents)
                    progress.processed_norms += len(documents)
                    processed_laws_count += 1
                    progress.processed_laws = processed_laws_count

                    # Batch insert when we have enough documents
                    if len(document_batch) >= batch_size:
                        batch_to_insert = document_batch[:]
                        document_batch.clear()

                        # Release lock during embedding (slow operation)
                        added = _embed_and_store(
                            store, batch_to_insert, show_progress=False
                        )
                        progress.documents_added += added

                        logger.info(
                            "Progress: %d/%d laws, %d docs ingested "
                            "(%.1f docs/sec, %d errors)",
                            progress.processed_laws,
                            progress.total_laws,
                            progress.documents_added,
                            progress.documents_added
                            / max(0.1, progress.elapsed_seconds),
                            len(progress.errors),
                        )
            else:
                with batch_lock:
                    processed_laws_count += 1
                    progress.processed_laws = processed_laws_count

            # Progress callback
            if progress_callback is not None:
                progress_callback(progress)

    # Insert remaining documents
    if document_batch:
        added = _embed_and_store(store, document_batch, show_progress=False)
        progress.documents_added += added

    result = IngestionResult(
        documents_added=progress.documents_added,
        laws_processed=progress.processed_laws,
        norms_processed=progress.processed_norms,
        errors=progress.errors,
        elapsed_seconds=progress.elapsed_seconds,
    )

    logger.info(
        "Local ingestion complete: %d documents from %d laws in %.1f seconds "
        "(%d errors)",
        result.documents_added,
        result.laws_processed,
        result.elapsed_seconds,
        len(result.errors),
    )

    return result


def _embed_and_store(
    store: GermanLawEmbeddingStore,
    documents: list[Document],
    show_progress: bool = False,
) -> int:
    """Embed documents and store in ChromaDB.

    Wraps store.add_documents with error handling. If a batch fails,
    logs the error and returns 0 instead of crashing the pipeline.

    Args:
        store: The GermanLawEmbeddingStore instance.
        documents: Documents to embed and store.
        show_progress: Whether to log per-batch progress.

    Returns:
        Number of documents successfully added.
    """
    try:
        return store.add_documents(documents, show_progress=show_progress)
    except Exception as error:
        logger.error(
            "Failed to embed/store batch of %d documents: %s",
            len(documents),
            error,
        )
        return 0


def get_corpus_status(
    persist_path: Path | str | None = None,
) -> dict[str, Any]:
    """Check the current status of the ChromaDB corpus.

    Returns information about the number of documents, unique laws,
    and whether the corpus appears to be populated.

    This is a lightweight check that does NOT load the embedding model.
    It only queries ChromaDB metadata.

    Supports both HttpClient (when ``CHROMA_URL`` is configured) and
    local PersistentClient (fallback).

    Args:
        persist_path: Override ChromaDB persistence path (ignored when
            CHROMA_URL is set).

    Returns:
        Dictionary with corpus status information:
            - populated: bool — whether any documents exist
            - total_documents: int — number of documents in collection
            - collection_name: str — ChromaDB collection name
            - chroma_backend: str — "http" or "persistent"
            - persist_path: str — path to ChromaDB data (persistent only)
            - chroma_url: str | None — ChromaDB server URL (http only)
            - sampled_laws: list[str] — sample of unique law abbreviations
            - error: str | None — error message if check failed
    """
    settings = get_settings()
    chroma_host = settings.chroma_host
    chroma_port = settings.chroma_port
    chroma_url = settings.chroma_url  # for display only
    store_path = (
        Path(persist_path) if persist_path else Path(settings.chroma_persist_path)
    )

    status: dict[str, Any] = {
        "populated": False,
        "total_documents": 0,
        "collection_name": "german_laws",
        "chroma_backend": "http" if chroma_host else "persistent",
        "chroma_url": chroma_url,
        "persist_path": str(store_path) if chroma_host is None else None,
        "sampled_laws": [],
        "error": None,
    }

    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        # Connect to ChromaDB — HTTP server or local persistent
        if chroma_host is not None:
            client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                ),
            )
        else:
            client = chromadb.PersistentClient(
                path=str(store_path),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )

        # Check if collection exists and has data
        try:
            collection = client.get_collection(name="german_laws")
            document_count = collection.count()
            status["total_documents"] = document_count
            status["populated"] = document_count > 0

            # Sample unique laws if populated
            if document_count > 0:
                sample = collection.get(
                    limit=min(500, document_count),
                    include=["metadatas"],
                )
                if sample["metadatas"]:
                    unique_laws = sorted(
                        {
                            str(metadata.get("law_abbrev", "unknown"))
                            for metadata in sample["metadatas"]
                            if metadata
                        }
                    )
                    status["sampled_laws"] = unique_laws

        except Exception:
            # Collection doesn't exist yet — that's fine, corpus is empty
            status["populated"] = False

    except Exception as error:
        status["error"] = str(error)
        logger.warning("Failed to check corpus status: %s", error)

    return status


def get_ingested_law_abbreviations(
    persist_path: Path | str | None = None,
) -> set[str]:
    """Query ChromaDB for the set of law abbreviations already ingested.

    Performs a paginated scan of the ``german_laws`` collection metadata
    to collect all unique ``law_abbrev`` values.  This is used by the
    warm-up resume logic to skip laws that are already in the store.

    Args:
        persist_path: Override ChromaDB persistence path (ignored when
            ``CHROMA_HOST`` is set).

    Returns:
        Set of lowercase law abbreviation strings (e.g. ``{"bgb", "stgb"}``).
        Returns an empty set if the collection doesn't exist or on error.
    """
    settings = get_settings()
    chroma_host = settings.chroma_host
    chroma_port = settings.chroma_port

    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if chroma_host is not None:
            client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        else:
            store_path = (
                Path(persist_path)
                if persist_path
                else Path(settings.chroma_persist_path)
            )
            client = chromadb.PersistentClient(
                path=str(store_path),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )

        try:
            collection = client.get_collection(name="german_laws")
        except Exception:
            return set()

        document_count = collection.count()
        if document_count == 0:
            return set()

        # Paginate through all documents to collect unique law_abbrev values
        law_abbreviations: set[str] = set()
        page_size = 5000
        offset = 0

        while offset < document_count:
            batch = collection.get(
                limit=page_size,
                offset=offset,
                include=["metadatas"],
            )
            if not batch["metadatas"]:
                break

            for metadata in batch["metadatas"]:
                if metadata and metadata.get("law_abbrev"):
                    law_abbreviations.add(str(metadata["law_abbrev"]))

            fetched = len(batch["ids"])
            if fetched == 0:
                break
            offset += fetched

        logger.info(
            "Found %d unique law abbreviations in ChromaDB (%d documents)",
            len(law_abbreviations),
            document_count,
        )
        return law_abbreviations

    except Exception as error:
        logger.warning("Failed to query ingested law abbreviations: %s", error)
        return set()


__all__ = [
    "discover_local_laws",
    "get_corpus_status",
    "get_ingested_law_abbreviations",
    "ingest_from_local_html",
    "parse_local_html_file",
]
