#!/usr/bin/env python3
"""Download German federal law corpus from gesetze-im-internet.de.

This script downloads all German federal laws and regulations from the official
government portal. It fetches the table of contents XML, then downloads each
law's XML file in parallel with rate limiting.

Usage:
    uv run python scripts/download_corpus.py [OPTIONS]

Options:
    --output-dir PATH    Output directory (default: data/raw/de-federal)
    --concurrency N      Number of concurrent downloads (default: 10)
    --delay MS           Delay between batches in ms (default: 100)
    --limit N            Limit number of laws to download (for testing)
    --resume             Skip already downloaded files
    --dry-run            Show what would be downloaded without downloading
"""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

import aiofiles
import aiohttp

if TYPE_CHECKING:
    from collections.abc import Sequence

# Constants
TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"
BASE_URL = "https://www.gesetze-im-internet.de"
DEFAULT_OUTPUT_DIR = Path("data/raw/de-federal")
DEFAULT_CONCURRENCY = 10
DEFAULT_DELAY_MS = 100


@dataclass
class LawEntry:
    """A law entry from the table of contents."""

    title: str
    zip_url: str
    abbreviation: str

    @property
    def filename(self) -> str:
        """Get the output filename for this law."""
        return f"{self.abbreviation}.xml"


@dataclass
class DownloadResult:
    """Result of a download attempt."""

    law: LawEntry
    success: bool
    error: str | None = None
    size_bytes: int = 0


@dataclass
class CorpusStats:
    """Statistics about the downloaded corpus."""

    total_laws: int
    downloaded: int
    skipped: int
    failed: int
    total_bytes: int

    def __str__(self) -> str:
        """Format stats as a human-readable string."""
        size_mb = self.total_bytes / (1024 * 1024)
        return (
            f"Corpus Statistics:\n"
            f"  Total laws:  {self.total_laws:,}\n"
            f"  Downloaded:  {self.downloaded:,}\n"
            f"  Skipped:     {self.skipped:,}\n"
            f"  Failed:      {self.failed:,}\n"
            f"  Total size:  {size_mb:.2f} MB"
        )


async def fetch_toc(session: aiohttp.ClientSession) -> list[LawEntry]:
    """Fetch and parse the table of contents XML.

    Args:
        session: aiohttp client session

    Returns:
        List of law entries with titles and download URLs
    """
    print(f"Fetching table of contents from {TOC_URL}...")

    async with session.get(TOC_URL) as response:
        response.raise_for_status()
        content = await response.text()

    # Parse XML
    root = ElementTree.fromstring(content)
    laws: list[LawEntry] = []

    for item in root.findall("item"):
        title_elem = item.find("title")
        link_elem = item.find("link")

        if title_elem is None or link_elem is None:
            continue

        title = title_elem.text or ""
        zip_url = link_elem.text or ""

        # Extract abbreviation from URL
        # URL format: http://www.gesetze-im-internet.de/{abbrev}/xml.zip
        parts = zip_url.rstrip("/").split("/")
        if len(parts) >= 2:
            abbreviation = parts[-2]
        else:
            abbreviation = zip_url.replace("/", "_").replace(".", "_")

        laws.append(LawEntry(title=title, zip_url=zip_url, abbreviation=abbreviation))

    print(f"Found {len(laws):,} laws in table of contents")
    return laws


async def download_law(
    session: aiohttp.ClientSession,
    law: LawEntry,
    output_dir: Path,
    resume: bool = False,
) -> DownloadResult:
    """Download a single law's XML file.

    Args:
        session: aiohttp client session
        law: Law entry to download
        output_dir: Directory to save the XML file
        resume: Skip if file already exists

    Returns:
        DownloadResult with success status and any errors
    """
    output_path = output_dir / law.filename

    # Skip if already exists and resume mode is on
    if resume and output_path.exists():
        size = output_path.stat().st_size
        return DownloadResult(law=law, success=True, size_bytes=size)

    try:
        async with session.get(law.zip_url) as response:
            response.raise_for_status()
            zip_data = await response.read()

        # Extract XML from zip
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Each zip contains exactly one XML file
            xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
            if not xml_files:
                return DownloadResult(
                    law=law, success=False, error="No XML file in zip"
                )

            xml_content = zf.read(xml_files[0])

        # Save to disk
        async with aiofiles.open(output_path, "wb") as f:
            await f.write(xml_content)

        return DownloadResult(law=law, success=True, size_bytes=len(xml_content))

    except aiohttp.ClientError as e:
        return DownloadResult(law=law, success=False, error=f"HTTP error: {e}")
    except zipfile.BadZipFile as e:
        return DownloadResult(law=law, success=False, error=f"Invalid zip: {e}")
    except OSError as e:
        return DownloadResult(law=law, success=False, error=f"IO error: {e}")


async def download_batch(
    session: aiohttp.ClientSession,
    laws: Sequence[LawEntry],
    output_dir: Path,
    resume: bool = False,
) -> list[DownloadResult]:
    """Download a batch of laws concurrently.

    Args:
        session: aiohttp client session
        laws: List of laws to download
        output_dir: Directory to save XML files
        resume: Skip already downloaded files

    Returns:
        List of download results
    """
    tasks = [download_law(session, law, output_dir, resume) for law in laws]
    return await asyncio.gather(*tasks)


def print_progress(
    completed: int,
    total: int,
    current_batch_results: list[DownloadResult],
) -> None:
    """Print download progress."""
    percent = (completed / total) * 100 if total > 0 else 0
    successes = sum(1 for r in current_batch_results if r.success)
    failures = len(current_batch_results) - successes

    # Simple progress bar
    bar_width = 40
    filled = int(bar_width * completed / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)

    status = f"\r[{bar}] {percent:5.1f}% ({completed:,}/{total:,})"
    if failures > 0:
        status += f" | {failures} failed in batch"

    print(status, end="", flush=True)


async def download_corpus(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    concurrency: int = DEFAULT_CONCURRENCY,
    delay_ms: int = DEFAULT_DELAY_MS,
    limit: int | None = None,
    resume: bool = False,
    dry_run: bool = False,
) -> CorpusStats:
    """Download the entire German federal law corpus.

    Args:
        output_dir: Directory to save XML files
        concurrency: Number of concurrent downloads
        delay_ms: Delay between batches in milliseconds
        limit: Maximum number of laws to download (for testing)
        resume: Skip already downloaded files
        dry_run: Show what would be downloaded without downloading

    Returns:
        Statistics about the download
    """
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Configure connection pooling
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency)
    timeout = aiohttp.ClientTimeout(total=60, connect=10)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Fetch table of contents
        laws = await fetch_toc(session)

        # Apply limit if specified
        if limit is not None:
            laws = laws[:limit]
            print(f"Limited to first {limit} laws")

        total = len(laws)

        if dry_run:
            print(f"\nDry run - would download {total:,} laws to {output_dir}")
            for law in laws[:10]:
                print(f"  - {law.abbreviation}: {law.title[:60]}...")
            if total > 10:
                print(f"  ... and {total - 10:,} more")
            return CorpusStats(
                total_laws=total,
                downloaded=0,
                skipped=0,
                failed=0,
                total_bytes=0,
            )

        print(f"\nDownloading {total:,} laws to {output_dir}")
        print(f"Concurrency: {concurrency}, Delay: {delay_ms}ms between batches")
        if resume:
            print("Resume mode: skipping existing files")
        print()

        # Download in batches
        all_results: list[DownloadResult] = []
        completed = 0

        for i in range(0, total, concurrency):
            batch = laws[i : i + concurrency]
            results = await download_batch(session, batch, output_dir, resume)
            all_results.extend(results)
            completed += len(batch)

            print_progress(completed, total, results)

            # Rate limiting delay between batches
            if i + concurrency < total:
                await asyncio.sleep(delay_ms / 1000)

        print()  # Newline after progress bar

    # Calculate statistics
    downloaded = sum(1 for r in all_results if r.success and r.size_bytes > 0)
    skipped = sum(
        1
        for r in all_results
        if r.success and output_dir.joinpath(r.law.filename).exists()
    )
    failed = sum(1 for r in all_results if not r.success)
    total_bytes = sum(r.size_bytes for r in all_results)

    # Report failures
    failures = [r for r in all_results if not r.success]
    if failures:
        print(f"\n{len(failures)} downloads failed:")
        for result in failures[:10]:
            print(f"  - {result.law.abbreviation}: {result.error}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more failures")

    stats = CorpusStats(
        total_laws=total,
        downloaded=downloaded,
        skipped=skipped if resume else 0,
        failed=failed,
        total_bytes=total_bytes,
    )

    print(f"\n{stats}")
    return stats


def main() -> int:
    """Main entry point with CLI argument parsing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Download German federal law corpus from gesetze-im-internet.de",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Number of concurrent downloads (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=DEFAULT_DELAY_MS,
        help=f"Delay between batches in ms (default: {DEFAULT_DELAY_MS})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of laws to download (for testing)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip already downloaded files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            download_corpus(
                output_dir=args.output_dir,
                concurrency=args.concurrency,
                delay_ms=args.delay,
                limit=args.limit,
                resume=args.resume,
                dry_run=args.dry_run,
            )
        )
        return 0
    except KeyboardInterrupt:
        print("\n\nDownload interrupted by user")
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
