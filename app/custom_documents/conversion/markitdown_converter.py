"""Safe MarkItDown-based file conversion utilities.

This module provides a thin, defensive wrapper around the `markitdown` package
to convert allowlisted files (e.g., PDFs) to Markdown/plain text suitable for
ingestion into the custom documents pipeline.

Security goals:
- This module must NOT resolve paths itself. Callers must pass already-validated
  allowlisted paths (see `app.custom_documents.file_ingestion`).
- Do not log or return raw user document content in errors.
- Provide bounded error messages suitable for MCP tool responses.

Notes:
- `markitdown` supports many formats. In this project we primarily use it for
  PDF → Markdown/text conversion.
- Conversion is CPU/memory intensive for large PDFs. Add sizing caps at tool
  level (bytes/chars) as needed.

Public API:
- `convert_allowlisted_file_to_markdown(...)`
- `convert_pdf_to_markdown(...)`
- `sanitize_converted_text_for_ingestion(...)`
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FileConversionError(RuntimeError):
    """Raised when document conversion fails.

    This error is safe to return via tools because it should never include raw
    document content.
    """


@dataclass(frozen=True)
class ConversionResult:
    """Result of converting a file to Markdown for ingestion.

    Attributes:
        source_name: Basename of the original file (suitable for ingestion).
        file_suffix: Lowercased file suffix (e.g., ".pdf").
        markdown: Converted Markdown text (may be empty if conversion yields none).
        metadata: Safe metadata about conversion (no extracted text).
    """

    source_name: str
    file_suffix: str
    markdown: str
    metadata: dict[str, str]


def _load_markitdown() -> Any:
    """Import MarkItDown lazily.

    Returns:
        The `MarkItDown` class from the `markitdown` package.

    Raises:
        FileConversionError: If the dependency is not installed.
    """
    try:
        from markitdown import MarkItDown  # type: ignore[import-not-found]
    except Exception as error:  # pragma: no cover
        raise FileConversionError(
            "File conversion is unavailable: optional dependency 'markitdown' is not installed."
        ) from error

    return MarkItDown


def _extract_markdown_text(conversion_output: Any) -> str:
    """Extract Markdown text from MarkItDown conversion output.

    MarkItDown has changed some implementation details over time. We defensively
    support common patterns:
    - output.text_content (preferred)
    - output.markdown
    - output.text
    - str(output) as a last resort

    Args:
        conversion_output: Object returned by MarkItDown.

    Returns:
        Markdown text (possibly empty).

    Raises:
        FileConversionError: If no meaningful output can be extracted or if
            fallback stringification fails.
    """
    if conversion_output is None:
        raise FileConversionError("Conversion returned no result.")

    # Common attribute names across releases/usages.
    for attribute_name in ("text_content", "markdown", "text"):
        if hasattr(conversion_output, attribute_name):
            candidate = getattr(conversion_output, attribute_name)
            if isinstance(candidate, str):
                return candidate

    # Fallback: stringification can raise (custom objects). Ensure we return a
    # safe, consistent error rather than bubbling arbitrary exceptions.
    try:
        candidate_text = str(conversion_output)
    except Exception as error:
        raise FileConversionError(
            "Failed to extract converted text from conversion result."
        ) from error

    if isinstance(candidate_text, str):
        return candidate_text

    raise FileConversionError(
        "Failed to extract converted text from conversion result."
    )


def sanitize_converted_text_for_ingestion(
    markdown_text: str,
    *,
    max_chars: int | None = 5_000_000,
) -> tuple[str, bool]:
    """Sanitize converted Markdown/text for ingestion.

    This function:
    - Normalizes newlines
    - Optionally truncates overly large content (character-based)
    - Ensures returned text is a string

    Args:
        markdown_text: Converted Markdown/text.
        max_chars: Optional maximum number of characters to return.

    Returns:
        Tuple of (sanitized_text, truncated_flag).

    Raises:
        ValueError: If max_chars is provided but non-positive.
    """
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars must be positive when provided.")

    normalized_text = (markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")

    if max_chars is None:
        return normalized_text, False

    truncated_text = normalized_text[:max_chars]
    truncated = len(normalized_text) > len(truncated_text)
    return truncated_text, truncated


def convert_allowlisted_file_to_markdown(
    path: Path,
    *,
    max_chars: int | None = 5_000_000,
) -> ConversionResult:
    """Convert an allowlisted file to Markdown/text using MarkItDown.

    IMPORTANT: `path` must already be validated as allowlisted and safe.
    This function does not attempt to enforce path traversal or root allowlisting.

    Args:
        path: Absolute path to a file on disk (already allowlisted).
        max_chars: Optional maximum characters for returned text.

    Returns:
        ConversionResult with Markdown, `source_name`, and safe metadata.

    Raises:
        FileConversionError: If conversion fails or input path is invalid.
    """
    if not isinstance(path, Path):
        raise FileConversionError("Invalid path type for conversion.")
    if not path.is_file():
        raise FileConversionError("Conversion target must be a file.")

    file_suffix = path.suffix.lower()
    source_name = path.name

    try:
        file_size_bytes = int(path.stat().st_size)
    except OSError:
        file_size_bytes = 0

    MarkItDown = _load_markitdown()
    converter = MarkItDown()

    try:
        conversion_output = converter.convert(str(path))
    except Exception as error:
        # Do not include file content; keep message bounded and generic.
        raise FileConversionError(
            f"Failed to convert file '{source_name}' ({file_suffix})."
        ) from error

    raw_markdown = _extract_markdown_text(conversion_output)

    sanitized_markdown, truncated = sanitize_converted_text_for_ingestion(
        raw_markdown,
        max_chars=max_chars,
    )

    metadata: dict[str, str] = {
        "source_name": source_name,
        "file_suffix": file_suffix,
        "size_bytes": str(file_size_bytes),
        "converter": "markitdown",
        "truncated": str(truncated).lower(),
    }

    # Best-effort: capture a few safe fields if present (no extracted text).
    for safe_attribute_name in ("title", "file_type", "mime_type"):
        if hasattr(conversion_output, safe_attribute_name):
            safe_value = getattr(conversion_output, safe_attribute_name)
            if isinstance(safe_value, str) and safe_value.strip():
                metadata[safe_attribute_name] = safe_value.strip()[:200]

    return ConversionResult(
        source_name=source_name,
        file_suffix=file_suffix,
        markdown=sanitized_markdown,
        metadata=metadata,
    )


def convert_pdf_to_markdown(
    path: Path,
    *,
    max_chars: int | None = 5_000_000,
) -> ConversionResult:
    """Convert a PDF to Markdown/text using MarkItDown.

    Args:
        path: Allowlisted absolute path to a `.pdf` file.
        max_chars: Optional maximum characters for returned text.

    Returns:
        ConversionResult.

    Raises:
        FileConversionError: If the file is not a PDF or conversion fails.
    """
    if path.suffix.lower() != ".pdf":
        raise FileConversionError("convert_pdf_to_markdown only accepts .pdf files.")

    return convert_allowlisted_file_to_markdown(path, max_chars=max_chars)


__all__ = [
    "ConversionResult",
    "FileConversionError",
    "convert_allowlisted_file_to_markdown",
    "convert_pdf_to_markdown",
    "sanitize_converted_text_for_ingestion",
]
