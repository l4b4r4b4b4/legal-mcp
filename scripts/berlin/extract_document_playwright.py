#!/usr/bin/env python3
"""Playwright-based extractor for the Berlin state law portal (gesetze.berlin.de).

This script renders a Berlin portal document URL in headless Chromium and extracts:

- rendered HTML (full page + best-effort main content)
- rendered plain text (best-effort main content + fallback to page text)
- a bounded list of observed network request URLs and paths

It is intended as a *research helper* to:
1) quickly obtain parseable text from SPA document routes like:
   `https://gesetze.berlin.de/bsbe/document/jlr-OpenDataBerVBErahmen`
2) identify candidate backend endpoints to later re-implement without a browser.

It is NOT an ingestion pipeline and is intentionally conservative:
- single URL per run (or a small list if you call it repeatedly)
- bounded network capture list to avoid huge output
- avoids logging cookies, headers, and response bodies
- sets a strict navigation / overall timeout
- writes a JSON report under `data/raw/de-state/berlin/playwright-extract/`

Prerequisites:
- Add Playwright dev dependency:
  `uv add --group dev playwright`
- Install Chromium:
  `uv run python -m playwright install chromium`

IMPORTANT: system library dependencies (Linux / Nix devShell caveat)
- Playwright's downloaded Chromium needs common system libraries (e.g. GLib).
  If you see errors like:
      error while loading shared libraries: libglib-2.0.so.0: cannot open shared object file
  then your dev environment is missing runtime libs for the Playwright browser.

- If you are using the project's Nix dev shell (`legal-mcp/flake.nix`), a minimal fix is
  to add GLib and friends to the shell's `targetPkgs` list, for example:
      glib
      nss
      nspr
      atk
      at-spi2-atk
      cups
      libdrm
      libxkbcommon
      mesa
      pango
      cairo
      alsa-lib
      dbus
      xorg.libX11
      xorg.libXcomposite
      xorg.libXdamage
      xorg.libXext
      xorg.libXfixes
      xorg.libXrandr
      xorg.libxcb
      xorg.libxshmfence
      xorg.libXi
      xorg.libXtst
  Keep it minimal and iterate based on the missing-library error messages.

Usage:
    uv run python scripts/berlin/extract_document_playwright.py \
      --url "https://gesetze.berlin.de/bsbe/document/jlr-OpenDataBerVBErahmen" \
      --output-dir "data/raw/de-state/berlin/playwright-extract" \
      --max-network 250 \
      --timeout-seconds 45 \
      --headful false

Outputs:
- JSON file with fields:
  - url, fetched_at, timings
  - extracted_html (optional), extracted_text (optional)
  - network_requests (bounded list of {method, url, path, resource_type})
  - hints (candidate API base paths like `/jportal/wsrest/recherche3/`)

Security / privacy notes:
- This script does not record cookies, request headers, or any token values.
- It only stores the target URL, rendered HTML/text, and a list of URLs requested.
  If you consider the URL list sensitive, do not commit the output.

Limitations:
- DOM extraction is heuristic; selectors may need adjustment.
- Some portals may block headless browsers or require additional waits.

"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Browser, Page, sync_playwright

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_OUTPUT_DIRECTORY = "data/raw/de-state/berlin/playwright-extract"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_NAVIGATION_WAIT_UNTIL = "networkidle"

DEFAULT_MAX_NETWORK_REQUESTS = 250
DEFAULT_MAX_TEXT_CHARS = 500_000
DEFAULT_MAX_HTML_CHARS = 500_000

DEFAULT_USER_AGENT = "legal-mcp-berlin-playwright-extract/0.1 (bounded; research-only)"

CANDIDATE_API_HINTS = (
    "/jportal/wsrest/recherche3/",
    "/jportal/wsrest/",
)

DOCUMENT_URL_PATTERN = re.compile(
    r"^https://gesetze\.berlin\.de/bsbe/document/(?P<document_id>[A-Za-z0-9._-]+)$"
)


class ExtractionError(RuntimeError):
    """Raised when extraction fails in a way that should stop the run."""


@dataclass(frozen=True, slots=True)
class NetworkRequestRecord:
    """A sanitized, bounded representation of a single network request."""

    method: str
    url: str
    path: str
    resource_type: str


def _utc_now_rfc3339() -> str:
    return datetime.now(UTC).isoformat()


def _clip_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    return text if len(text) <= max_chars else text[:max_chars]


def _ensure_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _safe_output_path(output_directory: Path, *, fetched_at_rfc3339: str) -> Path:
    safe_timestamp = (
        fetched_at_rfc3339.replace(":", "")
        .replace("-", "")
        .replace("+", "Z")
        .replace(".", "_")
    )
    return output_directory / f"berlin_playwright_extract_{safe_timestamp}.json"


def _extract_document_id(url: str) -> str | None:
    match = DOCUMENT_URL_PATTERN.match(url.strip())
    if not match:
        return None
    return match.group("document_id")


def _best_effort_main_content_selector_candidates() -> list[str]:
    # Intentionally heuristic: choose a few generic candidates; adjust once we observe DOM structure.
    # We avoid single-letter variables per project rules.
    return [
        "main",
        "#main",
        "#container",
        "[role='main']",
        "article",
        ".document",
        ".content",
        ".juris-content",
        ".jportal-content",
    ]


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _page_extract_best_effort(
    page: Page, *, max_text_chars: int, max_html_chars: int
) -> dict[str, str]:
    selectors = _best_effort_main_content_selector_candidates()

    extracted_html: str | None = None
    extracted_text: str | None = None
    selected_selector: str | None = None

    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue
        if count <= 0:
            continue

        try:
            candidate_html = locator.first.inner_html(timeout=2_000)
            candidate_text = locator.first.inner_text(timeout=2_000)
        except Exception:
            continue

        # Heuristic: require some substance.
        if candidate_text and len(candidate_text.strip()) >= 200:
            extracted_html = candidate_html
            extracted_text = candidate_text
            selected_selector = selector
            break

    # Fallbacks if we couldn't find "main content"
    if extracted_text is None:
        extracted_text = page.inner_text("body")
    if extracted_html is None:
        extracted_html = page.content()

    return {
        "selected_selector": selected_selector or "",
        "extracted_text": _clip_text(extracted_text, max_text_chars),
        "extracted_html": _clip_text(extracted_html, max_html_chars),
    }


def _capture_network_requests(
    page: Page,
    *,
    max_network_requests: int,
) -> list[NetworkRequestRecord]:
    network_requests: list[NetworkRequestRecord] = []

    def on_request(request: Any) -> None:
        # Request object from Playwright; treated as Any to keep script light.
        if len(network_requests) >= max_network_requests:
            return
        try:
            url = request.url
            method = request.method
            resource_type = request.resource_type
        except Exception:
            return

        try:
            # Playwright provides only URL; we can derive path via simple parsing.
            # We avoid importing urllib.parse to keep the script minimal.
            # This is safe enough for our classification purpose.
            path_start_index = url.find("://")
            path_start_index = (
                url.find("/", path_start_index + 3)
                if path_start_index != -1
                else url.find("/")
            )
            path = url[path_start_index:] if path_start_index != -1 else "/"
        except Exception:
            path = "/"

        network_requests.append(
            NetworkRequestRecord(
                method=str(method),
                url=str(url),
                path=str(path),
                resource_type=str(resource_type),
            )
        )

    page.on("request", on_request)
    return network_requests


def _summarize_endpoint_hints(
    network_requests: list[NetworkRequestRecord],
) -> dict[str, Any]:
    urls = [record.url for record in network_requests]
    paths = [record.path for record in network_requests]

    hints: dict[str, Any] = {"api_base_candidates": [], "matching_requests": []}

    api_base_candidates: list[str] = []
    matching_requests: list[dict[str, str]] = []

    for hint in CANDIDATE_API_HINTS:
        if any(hint in path for path in paths):
            api_base_candidates.append(hint)

    for record in network_requests:
        if any(hint in record.path for hint in CANDIDATE_API_HINTS):
            matching_requests.append(
                {
                    "method": record.method,
                    "url": record.url,
                    "path": record.path,
                    "resource_type": record.resource_type,
                }
            )

    hints["api_base_candidates"] = _dedupe_preserve_order(api_base_candidates)
    hints["matching_requests"] = matching_requests[:50]  # keep output bounded
    hints["unique_domains"] = sorted(
        _dedupe_preserve_order(
            [
                re.sub(r"^https?://", "", url).split("/")[0]
                for url in urls
                if "://" in url
            ]
        )
    )

    return hints


def _open_browser(*, headful: bool) -> tuple[Browser, Any]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=not headful)
    return browser, playwright


def _close_browser(browser: Browser, playwright: Any) -> None:
    try:
        browser.close()
    finally:
        playwright.stop()


def main() -> int:
    """CLI entry point for extracting Berlin portal documents via Playwright."""
    parser = argparse.ArgumentParser(
        prog="extract_document_playwright.py",
        description="Render and extract Berlin portal documents via Playwright (Chromium).",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Document URL to render (e.g. https://gesetze.berlin.de/bsbe/document/jlr-OpenDataBerVBErahmen).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=f"Output directory for JSON report (default: {DEFAULT_OUTPUT_DIRECTORY}).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Overall timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--max-network",
        type=int,
        default=DEFAULT_MAX_NETWORK_REQUESTS,
        help=f"Maximum number of network requests to record (default: {DEFAULT_MAX_NETWORK_REQUESTS}).",
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=DEFAULT_MAX_TEXT_CHARS,
        help=f"Maximum characters of extracted text to store (default: {DEFAULT_MAX_TEXT_CHARS}).",
    )
    parser.add_argument(
        "--max-html-chars",
        type=int,
        default=DEFAULT_MAX_HTML_CHARS,
        help=f"Maximum characters of extracted HTML to store (default: {DEFAULT_MAX_HTML_CHARS}).",
    )
    parser.add_argument(
        "--headful",
        type=str,
        default="false",
        help="Set to 'true' to run with a visible browser window (default: false).",
    )
    parser.add_argument(
        "--wait-until",
        default=DEFAULT_NAVIGATION_WAIT_UNTIL,
        choices=["load", "domcontentloaded", "networkidle"],
        help=f"Navigation wait condition (default: {DEFAULT_NAVIGATION_WAIT_UNTIL}).",
    )
    parser.add_argument(
        "--extra-wait-ms",
        type=int,
        default=1_000,
        help="Extra fixed wait after navigation completes (default: 1000).",
    )

    args = parser.parse_args()

    url = str(args.url).strip()
    if not url.startswith("https://"):
        raise ExtractionError("Only https:// URLs are supported for this probe.")

    output_directory = Path(args.output_dir)
    _ensure_output_directory(output_directory)

    fetched_at = _utc_now_rfc3339()
    output_path = _safe_output_path(output_directory, fetched_at_rfc3339=fetched_at)

    headful_string = str(args.headful).strip().lower()
    headful = headful_string in {"1", "true", "yes", "y"}

    start_time = time.monotonic()

    browser: Browser | None = None
    playwright: Any | None = None

    try:
        browser, playwright = _open_browser(headful=headful)

        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            ignore_https_errors=False,
            locale="de-DE",
        )
        page = context.new_page()

        # Start capturing network requests before navigation.
        network_requests_pointer = _capture_network_requests(
            page, max_network_requests=int(args.max_network)
        )

        navigation_timeout_ms = int(float(args.timeout_seconds) * 1000)
        page.set_default_timeout(navigation_timeout_ms)
        page.set_default_navigation_timeout(navigation_timeout_ms)

        page.goto(url, wait_until=args.wait_until)

        # Extra wait to allow SPA to render visible document content after network idle
        page.wait_for_timeout(int(args.extra_wait_ms))

        extraction = _page_extract_best_effort(
            page,
            max_text_chars=int(args.max_text_chars),
            max_html_chars=int(args.max_html_chars),
        )

        # Snapshot the captured network requests.
        network_request_dicts = [
            {
                "method": record.method,
                "url": record.url,
                "path": record.path,
                "resource_type": record.resource_type,
            }
            for record in list(network_requests_pointer)
        ]

        hints = _summarize_endpoint_hints(list(network_requests_pointer))

        elapsed_seconds = time.monotonic() - start_time

        report: dict[str, Any] = {
            "fetched_at_rfc3339": fetched_at,
            "url": url,
            "document_id": _extract_document_id(url),
            "timings": {
                "elapsed_seconds": round(elapsed_seconds, 3),
                "timeout_seconds": float(args.timeout_seconds),
                "wait_until": str(args.wait_until),
                "extra_wait_ms": int(args.extra_wait_ms),
            },
            "extraction": {
                "selected_selector": extraction["selected_selector"],
                "text_chars": len(extraction["extracted_text"]),
                "html_chars": len(extraction["extracted_html"]),
                "extracted_text": extraction["extracted_text"],
                "extracted_html": extraction["extracted_html"],
            },
            "network": {
                "max_network_requests": int(args.max_network),
                "captured_count": len(network_request_dicts),
                "network_requests": network_request_dicts,
            },
            "hints": hints,
        }

        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(str(output_path))
        return 0

    except Exception as exception:
        elapsed_seconds = time.monotonic() - start_time
        error_report = {
            "fetched_at_rfc3339": fetched_at,
            "url": url,
            "timings": {"elapsed_seconds": round(elapsed_seconds, 3)},
            "error": f"{type(exception).__name__}: {exception}",
        }
        output_path.write_text(
            json.dumps(error_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(str(output_path), file=sys.stderr)
        print(error_report["error"], file=sys.stderr)
        return 2

    finally:
        if browser is not None and playwright is not None:
            _close_browser(browser, playwright)


if __name__ == "__main__":
    raise SystemExit(main())
