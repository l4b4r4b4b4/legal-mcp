#!/usr/bin/env python3
"""Normalize Berlin Playwright batch artifacts into an ingestion-ready JSONL.

This script converts the output from
`scripts/berlin/extract_from_sitemap_playwright.py` (Option A Playwright batch)
into a stable, ingestion-ready JSONL format.

Design goals:
- Deterministic output from deterministic input (stable ordering, stable fields)
- Lossless provenance (canonical/permalink/pdf URLs preserved)
- Safe by default: does not emit cookies/tokens/network payloads (not present in input)
- Keeps request volume at zero (pure file transform)
- Conservative: does not attempt fragile parsing of legal text beyond metadata fields

Input:
- A Playwright batch JSONL file where each line is a dict containing at least:
  - document_id, canonical_url, status
  - metadata (title, permalink_url, pdf_url, document_type_prefix, dokumentkopf)
  - extraction (extracted_text, text_chars, extracted_html, html_chars)

Output:
- JSONL file with one normalized record per successfully extracted document.

Example:
    uv run python scripts/berlin/normalize_playwright_batch.py \
      --input data/raw/de-state/berlin/playwright-batch/berlin_playwright_batch_....jsonl \
      --output-dir data/raw/de-state/berlin/normalized \
      --include-html false

Notes:
- This does not de-chrome UI text from extracted text. Keep it as-is for now.
- This script intentionally avoids a dependency on LangChain objects. It outputs JSON.

"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_DIRECTORY = Path("data/raw/de-state/berlin/normalized")


class NormalizationError(RuntimeError):
    """Raised for failures that should abort the normalization run."""


@dataclass(frozen=True, slots=True)
class NormalizedBerlinDocument:
    """A normalized, ingestion-ready representation of one Berlin portal document."""

    record: dict[str, Any]


def _read_jsonl_lines(input_path: Path) -> list[dict[str, Any]]:
    """Read JSONL file into a list of dicts.

    Args:
        input_path: Path to JSONL.

    Returns:
        Parsed JSON objects (one per line), skipping blank lines.

    Raises:
        NormalizationError: If parsing fails.
    """
    try:
        raw_text = input_path.read_text(encoding="utf-8")
    except Exception as exception:
        raise NormalizationError(
            f"Failed to read input: {input_path}: {exception}"
        ) from exception

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception as exception:
            raise NormalizationError(
                f"Invalid JSON on line {line_number} of {input_path}: {exception}"
            ) from exception
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _safe_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _bool_from_value(value: Any) -> bool | None:
    """Best-effort boolean parsing; returns None if unknown."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "ja", "1"}:
            return True
        if normalized in {"false", "no", "nein", "0"}:
            return False
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
    return None


def _document_class_from_prefix(document_type_prefix: str) -> str:
    if document_type_prefix == "jlr":
        return "norm"
    if document_type_prefix == "NJRE":
        return "decision"
    return "other"


def _extract_batch_id(input_records: list[dict[str, Any]]) -> str:
    for record in input_records:
        batch_id = record.get("batch_id")
        if isinstance(batch_id, str) and batch_id.strip():
            return batch_id.strip()
    return "unknown-batch"


def _build_normalized_record(
    source_record: dict[str, Any],
    *,
    include_html: bool,
    include_extraction_stats: bool,
) -> NormalizedBerlinDocument | None:
    """Convert one batch record into a normalized document record.

    Args:
        source_record: Input record from Playwright batch JSONL.
        include_html: Whether to include `html` in output.
        include_extraction_stats: Whether to include extracted text/html sizes.

    Returns:
        NormalizedBerlinDocument, or None if record should be skipped.
    """
    status = source_record.get("status")
    if status != "ok":
        return None

    document_id = source_record.get("document_id")
    canonical_url = source_record.get("canonical_url")
    if not isinstance(document_id, str) or not document_id.strip():
        return None
    if not isinstance(canonical_url, str) or not canonical_url.strip():
        return None

    metadata = source_record.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    dokumentkopf = metadata.get("dokumentkopf") or {}
    if not isinstance(dokumentkopf, dict):
        dokumentkopf = {}

    extraction = source_record.get("extraction") or {}
    if not isinstance(extraction, dict):
        extraction = {}

    extracted_text = _safe_str(extraction.get("extracted_text")).strip()
    extracted_html = _safe_str(extraction.get("extracted_html")).strip()

    title = _safe_str(metadata.get("title")).strip()
    permalink_url = _safe_str(metadata.get("permalink_url")).strip()
    pdf_url = _safe_str(metadata.get("pdf_url")).strip()
    document_type_prefix = _safe_str(metadata.get("document_type_prefix")).strip()

    document_class = _document_class_from_prefix(document_type_prefix)

    # Common dokumentkopf fields (already normalized by extraction script, best-effort).
    document_type = _safe_str(dokumentkopf.get("document_type")).strip()

    normalized: dict[str, Any] = {
        "schema_version": "berlin-normalized:0.0.0",
        "jurisdiction": "de-state:berlin",
        "source_name": "gesetze-berlin",
        "document_id": document_id.strip(),
        "document_class": document_class,
        "document_type": document_type,
        "title": title,
        "canonical_url": canonical_url.strip(),
        "permalink_url": permalink_url,
        "pdf_url": pdf_url,
        "provenance": {
            "batch_id": _safe_str(source_record.get("batch_id")),
            "fetched_at_rfc3339": _safe_str(source_record.get("fetched_at_rfc3339")),
            "snapshot_path": _safe_str(source_record.get("snapshot_path")),
        },
        "dokumentkopf": dokumentkopf,
        "content": {
            "text": extracted_text,
        },
    }

    if include_html:
        normalized["content"]["html"] = extracted_html

    if include_extraction_stats:
        normalized["content"]["text_chars"] = extraction.get("text_chars")
        if include_html:
            normalized["content"]["html_chars"] = extraction.get("html_chars")

    # A couple of normalized convenience fields (safe, low-risk).
    # Keep them duplicated for easier filtering later.
    if isinstance(dokumentkopf.get("court"), str):
        normalized["court"] = _safe_str(dokumentkopf.get("court")).strip()
    if isinstance(dokumentkopf.get("file_number"), str):
        normalized["file_number"] = _safe_str(dokumentkopf.get("file_number")).strip()
    if isinstance(dokumentkopf.get("ecli"), str):
        normalized["ecli"] = _safe_str(dokumentkopf.get("ecli")).strip()

    is_final_value = dokumentkopf.get("is_final")
    parsed_is_final = _bool_from_value(is_final_value)
    if parsed_is_final is not None:
        normalized["is_final"] = parsed_is_final

    for date_key in (
        "decision_date",
        "execution_date",
        "valid_from",
        "valid_to",
        "version_date",
    ):
        date_value = dokumentkopf.get(date_key)
        raw_value = dokumentkopf.get(f"{date_key}_raw")
        if isinstance(date_value, str) and date_value.strip():
            normalized[date_key] = date_value.strip()
        if isinstance(raw_value, str) and raw_value.strip():
            normalized[f"{date_key}_raw"] = raw_value.strip()

    referenced_norms = dokumentkopf.get("referenced_norms")
    if isinstance(referenced_norms, str) and referenced_norms.strip():
        normalized["referenced_norms"] = referenced_norms.strip()

    return NormalizedBerlinDocument(record=normalized)


def _write_jsonl(output_path: Path, records: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    """Normalize a Berlin Playwright batch JSONL into a stable ingestion JSONL."""
    parser = argparse.ArgumentParser(
        prog="normalize_playwright_batch.py",
        description="Normalize Berlin Playwright batch JSONL into ingestion-ready JSONL.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to Playwright batch JSONL (berlin_playwright_batch_<batch_id>.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIRECTORY),
        help=f"Directory to write normalized outputs (default: {DEFAULT_OUTPUT_DIRECTORY}).",
    )
    parser.add_argument(
        "--include-html",
        default="false",
        help="If 'true', include extracted HTML in the normalized output (default: false).",
    )
    parser.add_argument(
        "--include-extraction-stats",
        default="true",
        help="If 'true', include extracted text/html sizes (default: true).",
    )
    parser.add_argument(
        "--fail-on-empty",
        default="true",
        help="If 'true', exit non-zero when no records were normalized (default: true).",
    )

    args = parser.parse_args()

    input_path = Path(str(args.input))
    if not input_path.exists():
        raise NormalizationError(f"Input file not found: {input_path}")

    include_html = str(args.include_html).strip().lower() in {"1", "true", "yes", "y"}
    include_extraction_stats = str(args.include_extraction_stats).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    fail_on_empty = str(args.fail_on_empty).strip().lower() in {"1", "true", "yes", "y"}

    output_directory = Path(str(args.output_dir))
    output_directory.mkdir(parents=True, exist_ok=True)

    input_records = _read_jsonl_lines(input_path)
    batch_id = _extract_batch_id(input_records)

    normalized_records: list[dict[str, Any]] = []
    skipped = 0

    for source_record in input_records:
        normalized = _build_normalized_record(
            source_record,
            include_html=include_html,
            include_extraction_stats=include_extraction_stats,
        )
        if normalized is None:
            skipped += 1
            continue
        normalized_records.append(normalized.record)

    # Stable ordering
    normalized_records_sorted = sorted(
        normalized_records,
        key=lambda item: (item.get("document_class", ""), item.get("document_id", "")),
    )

    suffix = "with_html" if include_html else "no_html"
    output_path = output_directory / f"berlin_normalized_{batch_id}_{suffix}.jsonl"

    _write_jsonl(output_path, normalized_records_sorted)

    print(
        json.dumps(
            {
                "input_path": str(input_path),
                "output_path": str(output_path),
                "batch_id": batch_id,
                "input_records": len(input_records),
                "normalized_records": len(normalized_records_sorted),
                "skipped_records": skipped,
                "include_html": include_html,
            },
            ensure_ascii=False,
        )
    )

    if fail_on_empty and not normalized_records_sorted:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NormalizationError as exception:
        print(f"NormalizationError: {exception}", file=sys.stderr)
        raise SystemExit(2) from exception
