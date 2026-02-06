#!/usr/bin/env python3
"""Bulk download all German law HTML files via Tor.

Downloads all HTML files from gesetze-im-internet.de to local disk
for fast offline processing. Uses Tor for IP rotation and high concurrency.

Features:
- Concurrent downloads via ThreadPoolExecutor
- Tor SOCKS proxy for IP rotation (avoids rate limiting)
- Progress tracking with ETA
- Resume capability (skips already downloaded files)
- Organized directory structure: data/html/{law_abbrev}/{norm_id}.html

Usage:
    # Download all priority laws
    USE_TOR=true python scripts/download_all_laws.py

    # Download specific laws
    USE_TOR=true python scripts/download_all_laws.py --laws BGB,GG,StGB

    # Download all ~6800 laws (takes a while!)
    USE_TOR=true python scripts/download_all_laws.py --all

    # Customize concurrency
    USE_TOR=true python scripts/download_all_laws.py --workers 32 --delay 0.02
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, urlopen

import socks
from sockshandler import SocksiPyHandler

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
    "SGB",  # Sozialgesetzbuch (Social Code) - actually multiple books
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


@dataclass
class DownloadStats:
    """Track download statistics."""

    total_laws: int = 0
    completed_laws: int = 0
    total_norms: int = 0
    downloaded_norms: int = 0
    skipped_norms: int = 0
    failed_norms: int = 0
    total_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    lock: Lock = field(default_factory=Lock)

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds."""
        return time.time() - self.start_time

    @property
    def rate(self) -> float:
        """Download rate in norms per second."""
        if self.elapsed == 0:
            return 0.0
        return (self.downloaded_norms + self.skipped_norms) / self.elapsed

    def log_progress(self, law: str = "") -> None:
        """Log current progress."""
        processed = self.downloaded_norms + self.skipped_norms + self.failed_norms
        pct = (processed / self.total_norms * 100) if self.total_norms > 0 else 0
        remaining = self.total_norms - processed
        eta = remaining / self.rate if self.rate > 0 else 0

        mb = self.total_bytes / (1024 * 1024)
        prefix = f"[{law}] " if law else ""

        logger.info(
            "%s%d/%d (%.1f%%) | ✓%d ⊘%d ✗%d | %.1f MB | %.1f/sec | ETA: %.0fs",
            prefix,
            processed,
            self.total_norms,
            pct,
            self.downloaded_norms,
            self.skipped_norms,
            self.failed_norms,
            mb,
            self.rate,
            eta,
        )


def get_opener(
    use_tor: bool, tor_host: str = "127.0.0.1", tor_port: int = 9050
) -> build_opener | None:
    """Create URL opener with optional Tor proxy."""
    if use_tor:
        return build_opener(SocksiPyHandler(socks.SOCKS5, tor_host, tor_port))
    return None


def download_norm(
    url: str,
    output_path: Path,
    opener: build_opener | None,
    delay: float = 0.0,
) -> tuple[bool, int, str | None]:
    """Download a single norm HTML file.

    Args:
        url: URL to download
        output_path: Path to save the file
        opener: URL opener (with Tor proxy if configured)
        delay: Delay before request

    Returns:
        Tuple of (success, bytes_downloaded, error_message)
    """
    # Skip if already exists
    if output_path.exists() and output_path.stat().st_size > 100:
        return (True, 0, None)  # Skipped

    if delay > 0:
        time.sleep(delay)

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        },
    )

    max_retries = 5
    base_delay = 0.5
    last_error = None

    for attempt in range(max_retries):
        try:
            if opener:
                with opener.open(request, timeout=30) as response:
                    content = response.read()
            else:
                with urlopen(request, timeout=30) as response:
                    content = response.read()

            # Ensure parent directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write to file
            with open(output_path, "wb") as f:
                f.write(content)

            return (True, len(content), None)

        except (HTTPError, URLError, ConnectionResetError, TimeoutError, OSError) as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                wait = base_delay * (2**attempt)
                time.sleep(wait)

    return (False, 0, f"Failed {url}: {last_error}")


def norm_url_to_filename(url: str) -> str:
    """Convert norm URL to safe filename."""
    # Extract the last part of the URL path
    # e.g., https://www.gesetze-im-internet.de/bgb/__433.html -> __433.html
    path = url.rstrip("/").split("/")[-1]
    # Make it safe for filesystem
    safe = path.replace("/", "_").replace("\\", "_")
    return safe


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
    stats: DownloadStats,
) -> list[tuple[str, str, Path]]:
    """Collect all download tasks for all laws upfront.

    Args:
        laws: List of law abbreviations
        output_dir: Base output directory
        stats: Shared statistics tracker

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

    stats.total_norms = len(all_tasks)
    stats.total_laws = len(laws)

    logger.info("Total: %d norms across %d laws", len(all_tasks), len(laws))

    return all_tasks


def download_all_parallel(
    tasks: list[tuple[str, str, Path]],
    stats: DownloadStats,
    use_tor: bool,
    max_workers: int = 64,
    delay: float = 0.01,
) -> dict[str, dict]:
    """Download all norms in parallel across all laws.

    Args:
        tasks: List of (law_abbrev, url, output_path) tuples
        stats: Shared statistics tracker
        use_tor: Whether to use Tor proxy
        max_workers: Number of concurrent workers
        delay: Delay between requests

    Returns:
        Dictionary of law -> results
    """
    logger.info("=" * 60)
    logger.info(
        "Starting parallel download: %d norms, %d workers", len(tasks), max_workers
    )
    logger.info("=" * 60)

    # Track results per law
    law_results: dict[str, dict] = {}

    # Create opener once (thread-safe)
    opener = get_opener(use_tor)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks at once
        futures = {
            executor.submit(download_norm, url, path, opener, delay): (law, url, path)
            for law, url, path in tasks
        }

        for future in as_completed(futures):
            law, _url, _path = futures[future]
            _success, bytes_downloaded, error = future.result()

            # Initialize law results if needed
            if law not in law_results:
                law_results[law] = {
                    "law": law,
                    "total": 0,
                    "downloaded": 0,
                    "skipped": 0,
                    "failed": 0,
                    "errors": [],
                }

            law_results[law]["total"] += 1

            with stats.lock:
                if error:
                    stats.failed_norms += 1
                    stats.errors.append(error)
                    law_results[law]["failed"] += 1
                    law_results[law]["errors"].append(error)
                elif bytes_downloaded > 0:
                    stats.downloaded_norms += 1
                    stats.total_bytes += bytes_downloaded
                    law_results[law]["downloaded"] += 1
                else:
                    stats.skipped_norms += 1
                    law_results[law]["skipped"] += 1

                # Log progress every 200 norms
                total_processed = (
                    stats.downloaded_norms + stats.skipped_norms + stats.failed_norms
                )
                if total_processed % 200 == 0:
                    stats.log_progress()

    stats.completed_laws = len(law_results)

    return law_results


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Bulk download German law HTML files")
    parser.add_argument(
        "--output",
        type=str,
        default="data/html",
        help="Output directory (default: data/html)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Concurrent download workers (default: 16)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.02,
        help="Delay between requests in seconds (default: 0.02)",
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
        help="Download ALL laws (~6800 laws, takes hours)",
    )
    args = parser.parse_args()

    use_tor = os.getenv("USE_TOR", "").lower() in ("true", "1", "yes")

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
    logger.info("German Law Bulk Downloader")
    logger.info("=" * 60)
    logger.info("Laws to download: %d", len(laws))
    logger.info("Output directory: %s", output_dir.absolute())
    logger.info("Workers: %d", args.workers)
    logger.info("Request delay: %.3fs", args.delay)
    logger.info("Using Tor: %s", use_tor)
    if use_tor:
        # Verify Tor is working
        try:
            opener = get_opener(True)
            req = Request(
                "https://check.torproject.org/api/ip",
                headers={"User-Agent": USER_AGENT},
            )
            with opener.open(req, timeout=30) as resp:
                import json

                data = json.loads(resp.read().decode())
                logger.info("Tor IP: %s", data.get("IP", "unknown"))
        except Exception as e:
            logger.error("Tor check failed: %s", e)
            logger.error("Make sure Tor is running with client.enable = true")
            sys.exit(1)
    logger.info("=" * 60)

    stats = DownloadStats(total_laws=len(laws))

    # Collect ALL tasks upfront
    all_tasks = collect_all_tasks(laws, output_dir, stats)

    if not all_tasks:
        logger.error("No tasks to download!")
        sys.exit(1)

    # Download everything in parallel
    law_results = download_all_parallel(
        tasks=all_tasks,
        stats=stats,
        use_tor=use_tor,
        max_workers=args.workers,
        delay=args.delay,
    )

    results = list(law_results.values())

    # Final summary
    logger.info("=" * 60)
    logger.info("DOWNLOAD COMPLETE")
    logger.info("=" * 60)
    logger.info("Total time: %.1f minutes", stats.elapsed / 60)
    logger.info("Total downloaded: %d norms", stats.downloaded_norms)
    logger.info("Total skipped: %d norms (already existed)", stats.skipped_norms)
    logger.info("Total failed: %d norms", stats.failed_norms)
    logger.info("Total size: %.1f MB", stats.total_bytes / (1024 * 1024))
    logger.info("Average rate: %.1f norms/sec", stats.rate)
    logger.info("")

    for result in results:
        total = result.get("total", 0)
        downloaded = result["downloaded"]
        skipped = result["skipped"]
        failed = result["failed"]

        if failed == 0:
            status = "✅"
        elif failed < total * 0.1:
            status = "⚠️"
        else:
            status = "❌"

        logger.info(
            "  %s %s: ✓%d ⊘%d ✗%d / %d",
            status,
            result["law"],
            downloaded,
            skipped,
            failed,
            total,
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
        f.write(f"# Downloaded: {stats.downloaded_norms}\n")
        f.write(f"# Skipped: {stats.skipped_norms}\n")
        f.write(f"# Failed: {stats.failed_norms}\n")
        f.write(f"# Size: {stats.total_bytes} bytes\n")
        f.write("#\n")
        for law in laws:
            f.write(f"{law}\n")

    logger.info("")
    logger.info("Manifest saved to: %s", manifest_path)


if __name__ == "__main__":
    main()
