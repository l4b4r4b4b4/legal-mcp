#!/usr/bin/env python3
"""Berlin portal sitemap discovery CLI script (snapshot document IDs).

This script downloads the Berlin laws portal sitemap index and referenced
sitemaps, then extracts canonical document URLs of the form:

    https://gesetze.berlin.de/bsbe/document/<document_id>

It writes a *discovery snapshot* JSON file under:

    data/raw/de-state/berlin/discovery/

The snapshot is intended to be a deterministic, reviewable input to later
retrieval/parsing steps. It does NOT attempt to retrieve document content.

Safety goals:
- bounded fetch size (protects against unexpectedly large responses)
- polite throttling between requests
- conservative parsing (XML sitemaps only)
- no sensitive data in logs (no cookies, tokens, etc.)

Usage:
    python scripts/berlin_portal_discovery.py
    python scripts/berlin_portal_discovery.py --output data/raw/de-state/berlin/discovery/custom.json
    python scripts/berlin_portal_discovery.py --limit-sitemaps 1 --limit-urls 1000
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from legal_mcp.net.http2_fetcher import (
    DEFAULT_HEADERS,
    Http2Fetcher,
    Http2FetcherConfig,
)

DEFAULT_BASE_URL = "https://gesetze.berlin.de"
DEFAULT_SITEMAP_INDEX_URL = "https://gesetze.berlin.de/sitemapindex.xml"

DEFAULT_OUTPUT_DIRECTORY = "data/raw/de-state/berlin/discovery"
DEFAULT_MAX_BYTES_PER_SITEMAP = 5_000_000  # sitemaps are usually << 5MB, but be safe
DEFAULT_SLEEP_SECONDS = 0.25

DOCUMENT_URL_PATTERN = re.compile(r"^/bsbe/document/(?P<document_id>[A-Za-z0-9._-]+)$")


class DiscoveryError(RuntimeError):
    """Raised for discovery failures that should stop the script."""


@dataclass(frozen=True, slots=True)
class DiscoveredDocument:
    """Represents a canonical document discovered via sitemap."""

    document_id: str
    canonical_url: str


def _utc_now_rfc3339() -> str:
    return datetime.now(UTC).isoformat()


def _parse_xml(xml_bytes: bytes) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exception:
        raise DiscoveryError(f"Failed to parse XML: {exception}") from exception


def _iter_loc_texts(xml_root: ElementTree.Element) -> list[str]:
    """Return all <loc> values (namespace-agnostic).

    Sitemaps typically use namespaces, so we avoid hard-coding them and instead
    match by localname.
    """
    loc_values: list[str] = []
    for element in xml_root.iter():
        # element.tag can look like "{namespace}loc" or "loc"
        tag = element.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag != "loc":
            continue
        if element.text:
            loc_values.append(element.text.strip())
    return [value for value in loc_values if value]


def _extract_document_from_url(url: str) -> DiscoveredDocument | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc != "gesetze.berlin.de":
        return None

    match = DOCUMENT_URL_PATTERN.match(parsed.path)
    if not match:
        return None

    document_id = match.group("document_id")
    canonical_url = f"https://gesetze.berlin.de{parsed.path}"
    return DiscoveredDocument(document_id=document_id, canonical_url=canonical_url)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item in seen:
            continue
        unique.append(item)
        seen.add(item)
    return unique


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _default_output_path(output_directory: str, *, timestamp_rfc3339: str) -> Path:
    # Use a filesystem-friendly timestamp hint (but keep the real timestamp in JSON).
    safe_ts = timestamp_rfc3339.replace(":", "").replace("+", "Z").replace("-", "")
    filename = f"berlin_sitemap_discovery_{safe_ts}.json"
    return Path(output_directory) / filename


async def _download_text_bytes(
    *,
    fetcher: Http2Fetcher,
    url: str,
    max_bytes: int,
    sleep_seconds: float,
) -> bytes:
    if sleep_seconds > 0:
        await _sleep_polite(sleep_seconds)

    response = await fetcher.get_bytes(
        url,
        max_bytes=max_bytes,
        headers={
            **dict(DEFAULT_HEADERS),
            # Sitemaps should be XML; ask for it explicitly.
            "Accept": "application/xml,text/xml,*/*;q=0.1",
        },
        range_request=True,
    )

    # Some servers respond to Range requests with 206 Partial Content even when
    # the full resource would be small. For discovery purposes, 200 and 206 are
    # both acceptable as long as the payload parses as XML.
    if response.status_code not in {200, 206}:
        raise DiscoveryError(f"Unexpected HTTP status {response.status_code} for {url}")

    return response.content


async def _sleep_polite(seconds: float) -> None:
    # Keep it tiny and deterministic (no random jitter); the fetcher already
    # has backoff jitter for retries.
    import asyncio

    await asyncio.sleep(max(0.0, float(seconds)))


async def discover_from_sitemaps(
    *,
    sitemap_index_url: str,
    max_bytes_per_sitemap: int,
    sleep_seconds: float,
    limit_sitemaps: int | None,
    limit_urls: int | None,
) -> dict[str, Any]:
    """Run sitemap discovery and return a JSON-serializable snapshot payload."""
    if max_bytes_per_sitemap <= 0:
        raise ValueError("max_bytes_per_sitemap must be > 0")
    if limit_sitemaps is not None and limit_sitemaps <= 0:
        raise ValueError("limit_sitemaps must be > 0")
    if limit_urls is not None and limit_urls <= 0:
        raise ValueError("limit_urls must be > 0")

    fetched_at = _utc_now_rfc3339()

    config = Http2FetcherConfig(
        # Discovery is light; keep concurrency modest and polite.
        max_concurrent_requests=5,
        max_connections=10,
        max_keepalive_connections=5,
        timeout_seconds=30.0,
        retry_attempts=4,
    )

    async with Http2Fetcher(config=config) as fetcher:
        index_bytes = await _download_text_bytes(
            fetcher=fetcher,
            url=sitemap_index_url,
            max_bytes=max_bytes_per_sitemap,
            sleep_seconds=0.0,
        )
        index_root = _parse_xml(index_bytes)
        sitemap_urls = _dedupe_preserve_order(_iter_loc_texts(index_root))

        if not sitemap_urls:
            raise DiscoveryError("Sitemap index returned no <loc> entries")

        if limit_sitemaps is not None:
            sitemap_urls = sitemap_urls[:limit_sitemaps]

        discovered_documents: list[DiscoveredDocument] = []
        processed_sitemaps: list[dict[str, Any]] = []

        for sitemap_url in sitemap_urls:
            started_at = time.time()
            sitemap_bytes = await _download_text_bytes(
                fetcher=fetcher,
                url=sitemap_url,
                max_bytes=max_bytes_per_sitemap,
                sleep_seconds=sleep_seconds,
            )
            sitemap_root = _parse_xml(sitemap_bytes)
            url_locs = _iter_loc_texts(sitemap_root)

            document_count_before = len(discovered_documents)

            for loc in url_locs:
                discovered = _extract_document_from_url(loc)
                if discovered is None:
                    continue
                discovered_documents.append(discovered)
                if limit_urls is not None and len(discovered_documents) >= limit_urls:
                    break

            processed_sitemaps.append(
                {
                    "sitemap_url": sitemap_url,
                    "bytes_read": len(sitemap_bytes),
                    "url_loc_count": len(url_locs),
                    "documents_added": len(discovered_documents)
                    - document_count_before,
                    "duration_seconds": round(time.time() - started_at, 4),
                }
            )

            if limit_urls is not None and len(discovered_documents) >= limit_urls:
                break

    # Dedupe documents while preserving order
    seen_document_ids: set[str] = set()
    unique_documents: list[DiscoveredDocument] = []
    for document in discovered_documents:
        if document.document_id in seen_document_ids:
            continue
        unique_documents.append(document)
        seen_document_ids.add(document.document_id)

    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "name": "gesetze.berlin.de",
            "portal_id": "bsbe",
            "sitemap_index_url": sitemap_index_url,
        },
        "fetched_at": fetched_at,
        "limits": {
            "limit_sitemaps": limit_sitemaps,
            "limit_urls": limit_urls,
            "max_bytes_per_sitemap": max_bytes_per_sitemap,
            "sleep_seconds": sleep_seconds,
        },
        "stats": {
            "sitemaps_processed": len(processed_sitemaps),
            "documents_total": len(unique_documents),
        },
        "sitemaps": processed_sitemaps,
        "documents": [
            {
                "document_id": document.document_id,
                "canonical_url": document.canonical_url,
            }
            for document in unique_documents
        ],
        "notes": [
            "Discovery only. No document content was retrieved.",
            "Document IDs are derived from /bsbe/document/<document_id> URLs in the sitemap(s).",
        ],
    }
    return snapshot


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Berlin portal sitemap discovery (snapshot document IDs)"
    )
    parser.add_argument(
        "--sitemap-index-url",
        default=DEFAULT_SITEMAP_INDEX_URL,
        help=f"Sitemap index URL (default: {DEFAULT_SITEMAP_INDEX_URL})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output JSON file path. If omitted, writes a timestamped file under "
            f"{DEFAULT_OUTPUT_DIRECTORY}/"
        ),
    )
    parser.add_argument(
        "--output-directory",
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=f"Default output directory (default: {DEFAULT_OUTPUT_DIRECTORY})",
    )
    parser.add_argument(
        "--max-bytes-per-sitemap",
        type=int,
        default=DEFAULT_MAX_BYTES_PER_SITEMAP,
        help=f"Max bytes to read per sitemap (default: {DEFAULT_MAX_BYTES_PER_SITEMAP})",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Polite sleep between sitemap fetches (default: {DEFAULT_SLEEP_SECONDS})",
    )
    parser.add_argument(
        "--limit-sitemaps",
        type=int,
        default=None,
        help="Optional limit for number of sitemaps to process (debugging)",
    )
    parser.add_argument(
        "--limit-urls",
        type=int,
        default=None,
        help="Optional limit for number of document URLs to keep (debugging)",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = _parse_args()

    # Resolve output path early so failures are obvious.
    fetched_at = _utc_now_rfc3339()
    if args.output is not None:
        output_path = Path(args.output)
    else:
        output_path = _default_output_path(
            args.output_directory, timestamp_rfc3339=fetched_at
        )

    # Ensure relative paths are anchored at repo root when run from repo root.
    # If user runs from elsewhere, this still works if output is absolute or
    # they accept relative output where invoked.
    if not output_path.is_absolute():
        output_path = Path(os.getcwd()) / output_path

    async def _run() -> dict[str, Any]:
        return await discover_from_sitemaps(
            sitemap_index_url=str(args.sitemap_index_url),
            max_bytes_per_sitemap=int(args.max_bytes_per_sitemap),
            sleep_seconds=float(args.sleep_seconds),
            limit_sitemaps=int(args.limit_sitemaps) if args.limit_sitemaps else None,
            limit_urls=int(args.limit_urls) if args.limit_urls else None,
        )

    try:
        import asyncio

        snapshot = asyncio.run(_run())
        _write_json(output_path, snapshot)
        sys.stdout.write(
            f"Wrote discovery snapshot: {output_path}\n"
            f"Documents: {snapshot['stats']['documents_total']}\n"
            f"Sitemaps processed: {snapshot['stats']['sitemaps_processed']}\n"
        )
    except KeyboardInterrupt:
        raise
    except Exception as exception:
        raise SystemExit(f"Discovery failed: {exception}") from exception


if __name__ == "__main__":
    main()
