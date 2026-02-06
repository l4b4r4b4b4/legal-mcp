#!/usr/bin/env python3
"""Conservative Berlin portal retrieval probe (cookie + CSRF bootstrap).

This script is a *research helper* for the Berlin state law portal
(https://gesetze.berlin.de). It attempts a minimal anonymous bootstrap and then
tries a small set of candidate backend API endpoints to learn how document
content might be retrieved.

It is intentionally conservative:
- single-document probing (by explicit document_id or discovered snapshot)
- low request volume and explicit sleep between requests
- bounded response sizes
- avoids logging sensitive data (cookie values, CSRF token values)

It is NOT an ingestion script.

Usage:
    # Probe a specific document_id (recommended)
    uv run python scripts/berlin_portal_retrieval_probe.py --document-id NJRE000029157

    # Auto-pick first document_id from the latest discovery snapshot
    uv run python scripts/berlin_portal_retrieval_probe.py

    # Increase verbosity (still safe)
    uv run python scripts/berlin_portal_retrieval_probe.py --verbose

Outputs:
    Writes a JSON report under:
        data/raw/de-state/berlin/retrieval-probe/

Notes / assumptions:
- The SPA appears to call backend endpoints under `/jportal/wsrest/recherche3/`.
- Requests may require:
  - header `JURIS-PORTALID: bsbe`
  - cookies (session)
  - CSRF token (source varies; best-effort detection here)
- Some endpoints may reject anonymous access. This script only tries a small
  set of likely endpoints and reports what it observes.

Security:
- This script will NOT print cookie values or CSRF token values.
- Report contains only cookie names and whether a CSRF token was detected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

DEFAULT_BASE_URL = "https://gesetze.berlin.de"
DEFAULT_PORTAL_PATH = "/bsbe/"
DEFAULT_PORTAL_ID = "bsbe"

DEFAULT_OUTPUT_DIRECTORY = "data/raw/de-state/berlin/retrieval-probe"

DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_SLEEP_SECONDS = 0.75
DEFAULT_MAX_BYTES = 200_000
DEFAULT_PER_ENDPOINT_TIMEOUT_SECONDS = 20.0

DEFAULT_USER_AGENT = "legal-mcp-berlin-retrieval-probe/0.1 (bounded; research-only)"

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "de,en;q=0.8",
}

# Common CSRF patterns (heuristic)
CSRF_META_TAG_PATTERN = re.compile(
    r"""<meta[^>]+name=["']csrf-token["'][^>]+content=["']([^"']+)["']""",
    re.IGNORECASE,
)
CSRF_COOKIE_NAME_HINTS = (
    "csrf",
    "xsrf",
    "x-csrf",
    "x-xsrf",
)

# Berlin portal appears to require this header on backend calls
PORTAL_HEADER_NAME = "JURIS-PORTALID"
CSRF_HEADER_NAME = "X-CSRF-TOKEN"


class ProbeError(RuntimeError):
    """Raised for probe failures that should stop execution."""


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """Result of a single endpoint attempt (sanitized)."""

    name: str
    method: str
    url: str
    status_code: int | None
    content_type: str | None
    bytes_read: int
    response_preview: str | None
    error: str | None


def _utc_now_rfc3339() -> str:
    return datetime.now(UTC).isoformat()


def _safe_decode_bytes(payload: bytes) -> str:
    try:
        return payload.decode("utf-8", errors="replace")
    except Exception:
        return repr(payload[:2000])


def _clip_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    return text[:max_chars]


def _default_output_path(output_directory: str, *, fetched_at_rfc3339: str) -> Path:
    safe_timestamp = (
        fetched_at_rfc3339.replace(":", "")
        .replace("-", "")
        .replace("+", "Z")
        .replace(".", "_")
    )
    return Path(output_directory) / f"berlin_retrieval_probe_{safe_timestamp}.json"


def _read_latest_discovery_snapshot(discovery_directory: Path) -> dict[str, Any]:
    if not discovery_directory.exists():
        raise ProbeError(
            f"Discovery directory does not exist: {discovery_directory}. "
            "Run the discovery script first or pass --document-id."
        )

    candidates = sorted(
        (path for path in discovery_directory.glob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise ProbeError(
            f"No discovery snapshots found in {discovery_directory}. "
            "Run the discovery script first or pass --document-id."
        )

    latest_path = candidates[0]
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as exception:
        raise ProbeError(
            f"Failed to parse discovery snapshot {latest_path}: {exception}"
        ) from exception

    if not isinstance(payload, dict):
        raise ProbeError(
            f"Discovery snapshot {latest_path} did not contain a JSON object"
        )

    payload["_discovery_snapshot_path"] = str(latest_path)
    return payload


def _pick_document_id_from_snapshot(snapshot: dict[str, Any]) -> str:
    documents = snapshot.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ProbeError("Discovery snapshot has no 'documents' list to pick from")

    first = documents[0]
    if not isinstance(first, dict) or "document_id" not in first:
        raise ProbeError("Discovery snapshot 'documents[0]' missing 'document_id'")

    document_id = first["document_id"]
    if not isinstance(document_id, str) or not document_id:
        raise ProbeError("Discovery snapshot 'document_id' is invalid")
    return document_id


def _extract_csrf_from_html(html_text: str) -> str | None:
    match = CSRF_META_TAG_PATTERN.search(html_text)
    if not match:
        return None
    token = match.group(1).strip()
    return token or None


def _cookie_names(cookie_jar: httpx.Cookies) -> list[str]:
    # httpx stores cookies with domain/path. We only return unique names.
    names: set[str] = set()
    for cookie in cookie_jar.jar:
        if cookie.name:
            names.add(cookie.name)
    return sorted(names)


def _csrf_cookie_name_candidates(cookie_names: list[str]) -> list[str]:
    lower_names = [name.lower() for name in cookie_names]
    matches: list[str] = []
    for original_name, lower_name in zip(cookie_names, lower_names, strict=True):
        if any(hint in lower_name for hint in CSRF_COOKIE_NAME_HINTS):
            matches.append(original_name)
    return matches


def _looks_like_security_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "security_wrongdomain".lower(),
            "security_notauthenticated".lower(),
            "not authenticated",
            "unauthorized",
            "forbidden",
        )
    )


async def _sleep_polite(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(max(0.0, float(seconds)))


async def _bounded_request(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[int | None, str | None, bytes, str | None]:
    """Execute a bounded HTTP request with size and timeout limits.

    Returns:
        (status_code, content_type, content_bytes, error_string).
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    request = client.build_request(
        method=method, url=url, headers=headers, json=json_body
    )

    try:
        async with client.stream(
            method=request.method,
            url=str(request.url),
            headers=request.headers,
            content=request.content,
            timeout=timeout_seconds,
        ) as response:
            content_type = response.headers.get("content-type")
            content_parts: list[bytes] = []
            downloaded_bytes = 0

            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                remaining = max_bytes - downloaded_bytes
                if remaining <= 0:
                    break
                if len(chunk) <= remaining:
                    content_parts.append(chunk)
                    downloaded_bytes += len(chunk)
                else:
                    content_parts.append(chunk[:remaining])
                    downloaded_bytes += remaining
                    break

            return response.status_code, content_type, b"".join(content_parts), None
    except httpx.TimeoutException as exception:
        return None, None, b"", f"timeout: {exception}"
    except httpx.HTTPError as exception:
        return None, None, b"", f"http_error: {exception}"
    except Exception as exception:
        return None, None, b"", f"unexpected_error: {exception}"


def _candidate_endpoints(base_url: str) -> list[dict[str, Any]]:
    """Candidate endpoints to try in first pass.

    These are intentionally limited and may need adjustment based on probe results.
    """
    # Keep the list small. We'll learn and iterate.
    # The shapes below are generic; the server may reject or ignore them.
    return [
        {
            "name": "recherche3-root-get",
            "method": "GET",
            "path": "/jportal/wsrest/recherche3/",
            "json_body": None,
        },
        {
            "name": "recherche3-search-post",
            "method": "POST",
            "path": "/jportal/wsrest/recherche3/search",
            "json_body": {"query": "", "page": 1},
        },
        {
            "name": "recherche3-document-post",
            "method": "POST",
            "path": "/jportal/wsrest/recherche3/document",
            "json_body": {"documentId": "__DOCUMENT_ID__"},
        },
        {
            "name": "recherche3-document-get",
            "method": "GET",
            "path": "/jportal/wsrest/recherche3/document/__DOCUMENT_ID__",
            "json_body": None,
        },
    ]


def _build_backend_headers(*, portal_id: str, csrf_token: str | None) -> dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    headers[PORTAL_HEADER_NAME] = portal_id
    # Some backends care about X-Requested-With, but adding it can also trigger
    # stricter CSRF checks. Keep it off by default.
    if csrf_token:
        headers[CSRF_HEADER_NAME] = csrf_token
    return headers


async def run_probe(
    *,
    base_url: str,
    portal_path: str,
    portal_id: str,
    document_id: str,
    sleep_seconds: float,
    max_bytes: int,
    timeout_seconds: float,
    per_endpoint_timeout_seconds: float,
    verbose: bool,
) -> dict[str, Any]:
    """Probe Berlin portal endpoints for a document and return retrieval results."""
    fetched_at = _utc_now_rfc3339()
    portal_url = urljoin(base_url, portal_path.lstrip("/"))

    # We keep redirects on; we need whatever the portal uses as canonical.
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=5)
    async with httpx.AsyncClient(
        headers=dict(DEFAULT_HEADERS),
        timeout=timeout_seconds,
        follow_redirects=True,
        http2=True,
        limits=limits,
    ) as client:
        # 1) Bootstrap: GET portal shell to obtain cookies and potential CSRF.
        if verbose:
            sys.stdout.write(f"Bootstrap GET {portal_url}\n")

        status_code, content_type, content_bytes, error = await _bounded_request(
            client,
            method="GET",
            url=portal_url,
            headers=dict(DEFAULT_HEADERS),
            json_body=None,
            max_bytes=max_bytes,
            timeout_seconds=per_endpoint_timeout_seconds,
        )
        bootstrap_preview = None
        csrf_token: str | None = None

        if error is None and content_bytes:
            bootstrap_text = _safe_decode_bytes(content_bytes)
            bootstrap_preview = _clip_text(bootstrap_text, 2000)
            csrf_token = _extract_csrf_from_html(bootstrap_text)

        cookie_names = _cookie_names(client.cookies)
        csrf_cookie_candidates = _csrf_cookie_name_candidates(cookie_names)

        # 2) Try candidate endpoints with required portal header (+ CSRF if found).
        backend_headers = _build_backend_headers(
            portal_id=portal_id, csrf_token=csrf_token
        )

        attempts: list[AttemptResult] = []
        for candidate in _candidate_endpoints(base_url):
            await _sleep_polite(sleep_seconds)

            method = str(candidate["method"])
            name = str(candidate["name"])
            path = str(candidate["path"])
            json_body = candidate.get("json_body")

            url = urljoin(base_url, path.lstrip("/"))
            if "__DOCUMENT_ID__" in url:
                url = url.replace("__DOCUMENT_ID__", document_id)

            prepared_body: dict[str, Any] | None
            if isinstance(json_body, dict):
                prepared_body = json.loads(
                    json.dumps(json_body)
                )  # deep copy, JSON-safe
                for key, value in list(prepared_body.items()):
                    if value == "__DOCUMENT_ID__":
                        prepared_body[key] = document_id
            else:
                prepared_body = None

            if verbose:
                sys.stdout.write(f"Attempt {name}: {method} {url}\n")

            (
                candidate_status,
                candidate_content_type,
                candidate_bytes,
                candidate_error,
            ) = await _bounded_request(
                client,
                method=method,
                url=url,
                headers=backend_headers,
                json_body=prepared_body,
                max_bytes=max_bytes,
                timeout_seconds=per_endpoint_timeout_seconds,
            )

            preview = None
            if candidate_error is None and candidate_bytes:
                response_text = _safe_decode_bytes(candidate_bytes)
                # Keep preview short; do not attempt to scrub perfectly, but avoid
                # huge logs. Also, avoid accidental token dumping by not including
                # any CSRF token we found.
                preview = _clip_text(response_text, 2000)

            attempts.append(
                AttemptResult(
                    name=name,
                    method=method,
                    url=url,
                    status_code=candidate_status,
                    content_type=candidate_content_type,
                    bytes_read=len(candidate_bytes),
                    response_preview=preview,
                    error=candidate_error,
                )
            )

        # 3) Build report
        report: dict[str, Any] = {
            "schema_version": 1,
            "source": {
                "base_url": base_url,
                "portal_path": portal_path,
                "portal_id": portal_id,
                "backend_api_base_hint": "/jportal/wsrest/recherche3/",
            },
            "fetched_at": fetched_at,
            "input": {"document_id": document_id},
            "limits": {
                "sleep_seconds": sleep_seconds,
                "max_bytes": max_bytes,
                "client_timeout_seconds": timeout_seconds,
                "per_endpoint_timeout_seconds": per_endpoint_timeout_seconds,
            },
            "bootstrap": {
                "url": portal_url,
                "status_code": status_code,
                "content_type": content_type,
                "bytes_read": len(content_bytes),
                "cookie_names": cookie_names,
                "csrf_token_detected": csrf_token is not None,
                "csrf_cookie_name_candidates": csrf_cookie_candidates,
                "body_preview": bootstrap_preview if verbose else None,
                "error": error,
            },
            "attempts": [
                {
                    "name": attempt.name,
                    "method": attempt.method,
                    "url": attempt.url,
                    "status_code": attempt.status_code,
                    "content_type": attempt.content_type,
                    "bytes_read": attempt.bytes_read,
                    "looks_like_security_error": (
                        _looks_like_security_error(attempt.response_preview)
                        if attempt.response_preview
                        else False
                    ),
                    "response_preview": attempt.response_preview if verbose else None,
                    "error": attempt.error,
                }
                for attempt in attempts
            ],
            "notes": [
                "This is a conservative research probe. It may not hit the correct endpoints yet.",
                f"Backend requests include header {PORTAL_HEADER_NAME}={portal_id}.",
                "CSRF token values and cookie values are not logged.",
                "If all attempts return authentication/security errors, next step is to "
                "inspect the SPA JS bundle and reproduce its exact request sequence.",
            ],
        }
        return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Conservative Berlin portal retrieval probe (cookie and CSRF bootstrap)"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--portal-path",
        default=DEFAULT_PORTAL_PATH,
        help=f"Portal path to bootstrap (default: {DEFAULT_PORTAL_PATH})",
    )
    parser.add_argument(
        "--portal-id",
        default=DEFAULT_PORTAL_ID,
        help=f"Portal ID for {PORTAL_HEADER_NAME} header (default: {DEFAULT_PORTAL_ID})",
    )
    parser.add_argument(
        "--document-id",
        default=None,
        help="Document ID to probe (e.g., NJRE000029157). If omitted, tries latest discovery snapshot.",
    )
    parser.add_argument(
        "--discovery-directory",
        default="data/raw/de-state/berlin/discovery",
        help="Where to find discovery snapshots if --document-id omitted.",
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
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Polite sleep between requests (default: {DEFAULT_SLEEP_SECONDS})",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Max bytes to read per response (default: {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Client timeout seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--per-endpoint-timeout-seconds",
        type=float,
        default=DEFAULT_PER_ENDPOINT_TIMEOUT_SECONDS,
        help=f"Per-endpoint timeout seconds (default: {DEFAULT_PER_ENDPOINT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include small body previews in report and print progress to stdout.",
    )
    return parser.parse_args()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def main() -> None:
    """CLI entry point for probing Berlin portal document retrieval endpoints."""
    args = _parse_args()

    fetched_at = _utc_now_rfc3339()

    if args.output is not None:
        output_path = Path(args.output)
    else:
        output_path = _default_output_path(
            args.output_directory, fetched_at_rfc3339=fetched_at
        )

    if not output_path.is_absolute():
        output_path = Path(os.getcwd()) / output_path

    document_id = args.document_id
    discovery_snapshot_path: str | None = None
    if not document_id:
        snapshot = _read_latest_discovery_snapshot(Path(args.discovery_directory))
        discovery_snapshot_path = snapshot.get("_discovery_snapshot_path")
        document_id = _pick_document_id_from_snapshot(snapshot)

    async def _run() -> dict[str, Any]:
        report = await run_probe(
            base_url=str(args.base_url).rstrip("/"),
            portal_path=str(args.portal_path),
            portal_id=str(args.portal_id),
            document_id=str(document_id),
            sleep_seconds=float(args.sleep_seconds),
            max_bytes=int(args.max_bytes),
            timeout_seconds=float(args.timeout_seconds),
            per_endpoint_timeout_seconds=float(args.per_endpoint_timeout_seconds),
            verbose=bool(args.verbose),
        )
        if discovery_snapshot_path is not None:
            report["input"]["discovery_snapshot_path"] = discovery_snapshot_path
        return report

    try:
        import asyncio

        report_payload = asyncio.run(_run())
        _write_json(output_path, report_payload)

        sys.stdout.write(f"Wrote probe report: {output_path}\n")
        sys.stdout.write(f"Document ID: {document_id}\n")
        bootstrap = report_payload.get("bootstrap", {})
        sys.stdout.write(
            f"Bootstrap status: {bootstrap.get('status_code')} "
            f"(cookies: {len(bootstrap.get('cookie_names', []))}, "
            f"csrf_detected: {bootstrap.get('csrf_token_detected')})\n"
        )
        attempts = report_payload.get("attempts", [])
        sys.stdout.write(f"Attempts: {len(attempts)}\n")
        # Summarize attempt statuses
        for attempt in attempts:
            sys.stdout.write(
                f"- {attempt.get('name')}: {attempt.get('status_code')} "
                f"({attempt.get('content_type')}) "
                f"err={attempt.get('error')}\n"
            )
    except KeyboardInterrupt:
        raise
    except Exception as exception:
        raise SystemExit(f"Probe failed: {exception}") from exception


if __name__ == "__main__":
    main()
