#!/usr/bin/env python3
"""Berlin portal bounded probe script (research helper).

This script performs *small, bounded* HTTP requests against the Berlin
"Vorschriften- und Rechtsprechungsdatenbank" portal to help discover:

- Terms / imprint URLs (where available)
- The JS entrypoint bundle location
- Candidate backend endpoints referenced in the JS bundle

It is intentionally conservative to avoid:
- downloading large assets
- hammering the server
- exploding local logs / output

It is NOT an ingestion script. It is a research tool used during
Goal 04 Phase A (source validation).

Usage:
    python scripts/berlin_probe_endpoints.py
    python scripts/berlin_probe_endpoints.py --max-js-bytes 250000
    python scripts/berlin_probe_endpoints.py --output report.json

Notes:
- This script does not execute JavaScript.
- Endpoint discovery is heuristic: we scan the HTML and the first N bytes
  of the JS bundle for URL-like strings and common API path patterns.
- Treat any candidate endpoints as "leads" to verify manually with
  careful, bounded follow-up requests.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://www.gesetze.berlin.de"
DEFAULT_TIMEOUT_SECONDS = 20.0

DEFAULT_MAX_HTML_BYTES = 200_000
DEFAULT_MAX_JS_BYTES = 200_000
DEFAULT_SLEEP_SECONDS = 0.25

USER_AGENT = "legal-mcp-berlin-probe/0.1 (bounded; research-only)"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.8",
}


class ProbeError(RuntimeError):
    """Raised for probe failures that should stop the script."""


@dataclass(frozen=True)
class FetchResult:
    """Represents the bounded result of a single HTTP fetch."""

    url: str
    status: int
    content_type: str | None
    bytes_read: int
    truncated: bool
    body_preview: str


def _safe_decode_bytes(payload: bytes) -> str:
    try:
        return payload.decode("utf-8", errors="replace")
    except Exception:
        # Very defensive: we never want decoding failures to crash the probe.
        return repr(payload[:2000])


def fetch_bounded(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    headers: dict[str, str],
    use_range_request: bool = True,
) -> FetchResult:
    """Fetch a URL with a strict size cap and small preview.

    Args:
        url: URL to fetch.
        max_bytes: Maximum number of bytes to read.
        timeout_seconds: Per-request timeout.
        headers: Request headers.
        use_range_request: If True, sends a Range header to try to avoid
            downloading more than max_bytes.

    Returns:
        A bounded fetch result (preview + metadata).

    Raises:
        ProbeError: If the request fails in a hard way.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    request_headers = dict(headers)
    if use_range_request:
        # Many servers support Range for static assets; if ignored, we still
        # cap reading client-side as a second line of defense.
        request_headers["Range"] = f"bytes=0-{max_bytes - 1}"

    request = Request(url, headers=request_headers)

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            content_type = response.headers.get("content-type")

            chunk = response.read(max_bytes)
            bytes_read = len(chunk)

            # Detect likely truncation: if we read max_bytes, assume truncated.
            truncated = bytes_read >= max_bytes

            preview_bytes = chunk[: min(bytes_read, 5000)]
            body_preview = _safe_decode_bytes(preview_bytes)

            return FetchResult(
                url=url,
                status=int(status),
                content_type=content_type,
                bytes_read=bytes_read,
                truncated=truncated,
                body_preview=body_preview,
            )
    except Exception as exception:
        raise ProbeError(f"Fetch failed for {url}: {exception}") from exception


def extract_js_entrypoints(html_text: str) -> list[str]:
    """Extract candidate JS entrypoint URLs from HTML."""
    # Typical Vite / bundler patterns:
    # <script type="module" crossorigin src="/bsbe/assets/index-XYZ.js"></script>
    script_src_pattern = re.compile(r"""<script[^>]+src=['"]([^'"]+\.js[^'"]*)['"]""")
    entrypoints = script_src_pattern.findall(html_text)

    # Prefer module entrypoint(s) if any.
    unique = []
    seen: set[str] = set()
    for candidate in entrypoints:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def extract_terms_and_imprint_links(base_url: str, html_text: str) -> list[str]:
    """Extract candidate ToS / imprint / privacy related links."""
    # This is heuristic and language-dependent (German).
    keywords = [
        "impressum",
        "datenschutz",
        "nutzungsbedingungen",
        "agb",
        "terms",
        "privacy",
        "lizenz",
        "license",
        "urheber",
        "copyright",
    ]
    href_pattern = re.compile(r"""href=['"]([^'"]+)['"]""", re.IGNORECASE)
    links = []
    for href in href_pattern.findall(html_text):
        href_lower = href.lower()
        if any(keyword in href_lower for keyword in keywords):
            links.append(urljoin(base_url, href))

    # Dedupe while preserving order
    unique: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link not in seen:
            unique.append(link)
            seen.add(link)
    return unique


def extract_candidate_endpoints(base_url: str, text: str) -> list[str]:
    """Extract candidate backend endpoint URLs from arbitrary text.

    This is intentionally heuristic. We try to find:
    - absolute URLs
    - root-relative paths that look like APIs
    """
    # Absolute URLs (keep it conservative: stop at quotes/whitespace)
    absolute_url_pattern = re.compile(r"""https?://[^\s"'<>]+""", re.IGNORECASE)
    absolute_urls = absolute_url_pattern.findall(text)

    # Root-relative API-ish paths
    apiish_path_pattern = re.compile(
        r"""(?:"|')(/(?:api|rest|graphql|jportal|portal|r3|services|service|suche|search|daten|data)[^\s"'<>]*)""",
        re.IGNORECASE,
    )
    relative_paths = list(apiish_path_pattern.findall(text))

    candidates: list[str] = []
    for url in absolute_urls:
        candidates.append(url)
    for path in relative_paths:
        candidates.append(urljoin(base_url, path))

    # Dedupe; also normalize trivial trailing punctuation
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = candidate.rstrip(").,;")
        if cleaned not in seen:
            normalized.append(cleaned)
            seen.add(cleaned)
    return normalized


def _same_host(url_a: str, url_b: str) -> bool:
    try:
        parsed_a = urlparse(url_a)
        parsed_b = urlparse(url_b)
        return parsed_a.netloc == parsed_b.netloc
    except Exception:
        return False


def build_report_dict(
    *,
    base_url: str,
    html_fetch: FetchResult,
    js_fetch: FetchResult | None,
    js_entrypoints: list[str],
    terms_links: list[str],
    endpoint_candidates: list[str],
) -> dict[str, Any]:
    """Construct a JSON-serializable report."""
    report: dict[str, Any] = {
        "base_url": base_url,
        "fetched": {
            "html": {
                "url": html_fetch.url,
                "status": html_fetch.status,
                "content_type": html_fetch.content_type,
                "bytes_read": html_fetch.bytes_read,
                "truncated": html_fetch.truncated,
            },
            "js": None,
        },
        "discovered": {
            "js_entrypoints": js_entrypoints,
            "terms_and_imprint_links": terms_links,
            "endpoint_candidates": endpoint_candidates,
        },
        "notes": [
            "This report is heuristic: discovered endpoints must be verified manually.",
            "No JavaScript was executed. JS bundle scanning is based on bounded bytes only.",
        ],
    }

    if js_fetch is not None:
        report["fetched"]["js"] = {
            "url": js_fetch.url,
            "status": js_fetch.status,
            "content_type": js_fetch.content_type,
            "bytes_read": js_fetch.bytes_read,
            "truncated": js_fetch.truncated,
        }

    return report


def main() -> None:
    """Run a bounded, non-JavaScript probe against the Berlin laws portal.

    This is a research helper for Goal 04 Phase A. It performs small,
    size-capped requests to discover likely JS entrypoints and candidate
    backend endpoints, and emits a JSON report.

    It is intentionally conservative and is not an ingestion script.
    """
    parser = argparse.ArgumentParser(
        description="Bounded probe for Berlin laws portal endpoints (research helper)"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout per request (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-html-bytes",
        type=int,
        default=DEFAULT_MAX_HTML_BYTES,
        help=f"Max bytes to read for HTML (default: {DEFAULT_MAX_HTML_BYTES})",
    )
    parser.add_argument(
        "--max-js-bytes",
        type=int,
        default=DEFAULT_MAX_JS_BYTES,
        help=f"Max bytes to read for JS bundle (default: {DEFAULT_MAX_JS_BYTES})",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Polite sleep between requests (default: {DEFAULT_SLEEP_SECONDS})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to write JSON report (default: stdout)",
    )
    parser.add_argument(
        "--include-offsite",
        action="store_true",
        help="Include endpoint candidates not on the base host (default: false)",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if not base_url.startswith("http"):
        raise ProbeError("--base-url must start with http/https")

    html_fetch = fetch_bounded(
        base_url,
        max_bytes=args.max_html_bytes,
        timeout_seconds=args.timeout_seconds,
        headers=DEFAULT_HEADERS,
        use_range_request=False,  # HTML is small; avoid range weirdness
    )
    html_text = html_fetch.body_preview

    js_entrypoints = extract_js_entrypoints(html_text)
    terms_links = extract_terms_and_imprint_links(base_url, html_text)

    # Endpoint candidates from HTML itself (usually few)
    endpoint_candidates = extract_candidate_endpoints(base_url, html_text)

    js_fetch: FetchResult | None = None
    js_text = ""

    if js_entrypoints:
        # Prefer the first entrypoint; keep the probe minimal.
        js_url = urljoin(base_url, js_entrypoints[0])

        time.sleep(max(0.0, float(args.sleep_seconds)))

        js_fetch = fetch_bounded(
            js_url,
            max_bytes=args.max_js_bytes,
            timeout_seconds=args.timeout_seconds,
            headers={
                **DEFAULT_HEADERS,
                "Accept": "application/javascript,text/javascript,*/*;q=0.1",
            },
            use_range_request=True,
        )
        js_text = js_fetch.body_preview
        endpoint_candidates.extend(extract_candidate_endpoints(base_url, js_text))

    # Dedupe again and optionally filter to same-host only
    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in endpoint_candidates:
        if not args.include_offsite and not _same_host(base_url, candidate):
            continue
        if candidate not in seen:
            unique_candidates.append(candidate)
            seen.add(candidate)

    report = build_report_dict(
        base_url=base_url,
        html_fetch=html_fetch,
        js_fetch=js_fetch,
        js_entrypoints=js_entrypoints,
        terms_links=terms_links,
        endpoint_candidates=unique_candidates,
    )

    output_text = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as output_file:
            output_file.write(output_text)
            output_file.write("\n")
    else:
        sys.stdout.write(output_text)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
