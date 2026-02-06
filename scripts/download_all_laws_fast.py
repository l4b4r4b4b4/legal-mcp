#!/usr/bin/env python3
"""Fast bulk download of all German law HTML files using HTTP/2.

Uses httpx with HTTP/2 multiplexing to download thousands of files
over a single connection, avoiding rate limiting.

Features:
- HTTP/2 connection multiplexing (100+ requests/sec)
- Async concurrent downloads
- Progress tracking with ETA
- Resume capability (skips already downloaded files)
- Organized directory structure: data/html/{law_abbrev}/{norm_id}.html

Usage:
    # Download priority laws (fast!)
    python scripts/download_all_laws_fast.py

    # Download specific laws
    python scripts/download_all_laws_fast.py --laws BGB,GG,StGB

    # Download ALL ~6800 laws
    python scripts/download_all_laws_fast.py --all

    # Customize concurrency
    python scripts/download_all_laws_fast.py --workers 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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
    "AO",  # Abgabenordnung (Tax Code)
    "BauGB",  # Baugesetzbuch (Building Code)
    "VwGO",  # Verwaltungsgerichtsordnung (Administrative Court Procedure)
    "GVG",  # Gerichtsverfassungsgesetz (Courts Constitution Act)
    "InsO",  # Insolvenzordnung (Insolvency Code)
    "ArbGG",  # Arbeitsgerichtsgesetz (Labor Court Act)
    "BNotO",  # Bundesnotarordnung (Federal Notary Code)
    "GBO",  # Grundbuchordnung (Land Register Code)
    "UrhG",  # Urheberrechtsgesetz (Copyright Act)
    "MarkenG",  # Markengesetz (Trademark Act)
    "PatG",  # Patentgesetz (Patent Act)
    "GmbHG",  # GmbH-Gesetz (Limited Liability Company Act)
    "AktG",  # Aktiengesetz (Stock Corporation Act)
]

# User agent that looks like a normal browser
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
}


@dataclass
class DownloadStats:
    """Track download statistics."""

    total_norms: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    total_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds."""
        return time.time() - self.start_time

    @property
    def processed(self) -> int:
        """Total processed (downloaded + skipped + failed)."""
        return self.downloaded + self.skipped + self.failed

    @property
    def rate(self) -> float:
        """Download rate in norms per second."""
        if self.elapsed == 0:
            return 0.0
        return self.processed / self.elapsed

    def log_progress(self) -> None:
        """Log current progress."""
        pct = (self.processed / self.total_norms * 100) if self.total_norms > 0 else 0
        remaining = self.total_norms - self.processed
        eta = remaining / self.rate if self.rate > 0 else 0
        mb = self.total_bytes / (1024 * 1024)

        logger.info(
            "%d/%d (%.1f%%) | ✓%d ⊘%d ✗%d | %.1f MB | %.0f/sec | ETA: %.0fs",
            self.processed,
            self.total_norms,
            pct,
            self.downloaded,
            self.skipped,
            self.failed,
            mb,
            self.rate,
            eta,
        )


def norm_url_to_filename(url: str) -> str:
    """Convert norm URL to safe filename."""
    path = url.rstrip("/").split("/")[-1]
    return path.replace("/", "_").replace("\\", "_")


def discover_all_laws() -> list:
    """Discover all available laws."""
    from legal_mcp.loaders.discovery import GermanLawDiscovery

    discovery = GermanLawDiscovery()
    return list(discovery.discover_laws())


def discover_norms_for_law(law_abbrev: str) -> list:
    """Discover all norms for a specific law."""
    from legal_mcp.loaders.discovery import GermanLawDiscovery, LawInfo

    discovery = GermanLawDiscovery()
    law_url = f"https://www.gesetze-im-internet.de/{law_abbrev.lower()}/"
    law = LawInfo(abbreviation=law_abbrev, title="", url=law_url)

    try:
        return list(discovery.discover_norms(law))
    except Exception as e:
        logger.error("Failed to discover norms for %s: %s", law_abbrev, e)
        return []


def collect_all_tasks(
    laws: list[str],
    output_dir: Path,
) -> list[tuple[str, str, Path]]:
    """Collect all download tasks for all laws upfront.

    Returns:
        List of (law_abbrev, url, output_path) tuples
    """
    all_tasks = []

    logger.info("Discovering norms for %d laws...", len(laws))

    for law_abbrev in laws:
        norms = discover_norms_for_law(law_abbrev)
        if not norms:
            logger.warning("No norms found for %s", law_abbrev)
            continue

        logger.info("  %s: %d norms", law_abbrev, len(norms))

        law_dir = output_dir / law_abbrev.lower()
        for norm in norms:
            filename = norm_url_to_filename(norm.url)
            output_path = law_dir / filename
            all_tasks.append((law_abbrev, norm.url, output_path))

    logger.info("Total: %d norms to download", len(all_tasks))
    return all_tasks


async def download_norm(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[int, str | None]:
    """Download a single norm HTML file.

    Returns:
        Tuple of (bytes_downloaded, error_message)
        bytes_downloaded = 0 means skipped (already exists)
        bytes_downloaded = -1 means failed
    """
    # Skip if already exists and has content
    if output_path.exists() and output_path.stat().st_size > 100:
        return (0, None)  # Skipped

    async with semaphore:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                content = resp.content

                # Ensure parent directory exists
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Write to file
                output_path.write_bytes(content)

                return (len(content), None)

            except Exception as e:
                if attempt == max_retries - 1:
                    return (-1, f"Failed {url}: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))

    return (-1, f"Failed {url}: unknown error")


async def download_all_async(
    tasks: list[tuple[str, str, Path]],
    stats: DownloadStats,
    max_concurrent: int = 100,
) -> dict[str, dict]:
    """Download all norms asynchronously with HTTP/2.

    Args:
        tasks: List of (law_abbrev, url, output_path) tuples
        stats: Statistics tracker
        max_concurrent: Maximum concurrent requests

    Returns:
        Dictionary of law -> results
    """
    stats.total_norms = len(tasks)
    law_results: dict[str, dict] = {}

    # Semaphore to limit concurrency
    semaphore = asyncio.Semaphore(max_concurrent)

    # HTTP/2 client with connection pooling
    limits = httpx.Limits(
        max_connections=50,
        max_keepalive_connections=20,
    )

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=30,
        limits=limits,
        http2=True,
        follow_redirects=True,
    ) as client:
        logger.info("=" * 60)
        logger.info(
            "Starting HTTP/2 download: %d norms, %d concurrent",
            len(tasks),
            max_concurrent,
        )
        logger.info("=" * 60)

        # Create all download tasks
        async def process_task(
            law: str, url: str, path: Path
        ) -> tuple[str, int, str | None]:
            bytes_dl, error = await download_norm(client, url, path, semaphore)
            return (law, bytes_dl, error)

        # Run all downloads
        download_tasks = [process_task(law, url, path) for law, url, path in tasks]

        last_log_time = time.time()

        for coro in asyncio.as_completed(download_tasks):
            law, bytes_downloaded, error = await coro

            # Initialize law results if needed
            if law not in law_results:
                law_results[law] = {
                    "law": law,
                    "downloaded": 0,
                    "skipped": 0,
                    "failed": 0,
                    "errors": [],
                }

            if error:
                stats.failed += 1
                stats.errors.append(error)
                law_results[law]["failed"] += 1
                law_results[law]["errors"].append(error)
            elif bytes_downloaded > 0:
                stats.downloaded += 1
                stats.total_bytes += bytes_downloaded
                law_results[law]["downloaded"] += 1
            else:
                stats.skipped += 1
                law_results[law]["skipped"] += 1

            # Log progress every 2 seconds
            now = time.time()
            if now - last_log_time >= 2:
                stats.log_progress()
                last_log_time = now

    return law_results


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fast bulk download German law HTML files using HTTP/2"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/html",
        help="Output directory (default: data/html)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=100,
        help="Concurrent download workers (default: 100)",
    )
    parser.add_argument(
        "--laws",
        type=str,
        default=None,
        help="Comma-separated list of laws to download (default: priority laws)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download ALL laws (~6800 laws)",
    )
    args = parser.parse_args()

    # Determine which laws to download
    if args.laws:
        laws = [law.strip().upper() for law in args.laws.split(",")]
    elif args.all:
        logger.info("Discovering all available laws...")
        all_laws = discover_all_laws()
        laws = [law.abbreviation for law in all_laws]
        logger.info("Found %d laws total", len(laws))
    else:
        laws = PRIORITY_LAWS.copy()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("German Law Fast Downloader (HTTP/2)")
    logger.info("=" * 60)
    logger.info("Laws to download: %d", len(laws))
    logger.info("Output directory: %s", output_dir.absolute())
    logger.info("Concurrent workers: %d", args.workers)
    logger.info("=" * 60)

    # Collect all tasks
    all_tasks = collect_all_tasks(laws, output_dir)

    if not all_tasks:
        logger.error("No tasks to download!")
        sys.exit(1)

    stats = DownloadStats()

    # Run async download
    law_results = asyncio.run(
        download_all_async(
            tasks=all_tasks,
            stats=stats,
            max_concurrent=args.workers,
        )
    )

    results = list(law_results.values())

    # Final summary
    logger.info("=" * 60)
    logger.info("DOWNLOAD COMPLETE")
    logger.info("=" * 60)
    logger.info("Total time: %.1f minutes", stats.elapsed / 60)
    logger.info(
        "Downloaded: %d norms (%.1f MB)",
        stats.downloaded,
        stats.total_bytes / (1024 * 1024),
    )
    logger.info("Skipped: %d norms (already existed)", stats.skipped)
    logger.info("Failed: %d norms", stats.failed)
    logger.info("Average rate: %.0f norms/sec", stats.rate)
    logger.info("")

    # Sort results by law name
    results.sort(key=lambda x: x["law"])

    for result in results:
        downloaded = result["downloaded"]
        skipped = result["skipped"]
        failed = result["failed"]
        total = downloaded + skipped + failed

        if failed == 0:
            status = "✅"
        elif failed < total * 0.1:
            status = "⚠️"
        else:
            status = "❌"

        logger.info(
            "  %s %s: ✓%d ⊘%d ✗%d",
            status,
            result["law"],
            downloaded,
            skipped,
            failed,
        )

    if stats.errors:
        logger.warning("")
        logger.warning("Errors (first 20):")
        for err in stats.errors[:20]:
            logger.warning("  - %s", err[:100])

    # Save manifest
    manifest_path = output_dir / "manifest.txt"
    with open(manifest_path, "w") as f:
        f.write("# German Law HTML Download Manifest\n")
        f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total laws: {len(laws)}\n")
        f.write(f"# Total norms: {stats.total_norms}\n")
        f.write(f"# Downloaded: {stats.downloaded}\n")
        f.write(f"# Skipped: {stats.skipped}\n")
        f.write(f"# Failed: {stats.failed}\n")
        f.write(f"# Size: {stats.total_bytes} bytes\n")
        f.write(f"# Time: {stats.elapsed:.1f} seconds\n")
        f.write(f"# Rate: {stats.rate:.1f} norms/sec\n")
        f.write("#\n")
        for law in sorted(laws):
            f.write(f"{law}\n")

    logger.info("")
    logger.info("Manifest saved to: %s", manifest_path)


if __name__ == "__main__":
    main()
