"""Safe file ingestion helpers for custom document ingestion.

This module provides utilities to read Markdown files from disk while enforcing
a single allowlisted root directory.

Security goals:
- Prevent path traversal (`..`) and absolute path reads
- Prevent symlink escapes (by resolving and validating real paths)
- Avoid logging or returning file contents
- Provide safe, minimal error messages suitable for tool responses

Allowlisted root:
- If `LEGAL_MCP_INGEST_ROOT` is configured by the server, it is used.
- If unset, a safe default root can be provided (recommended: `{worktree_root}/.agent/tmp`).

These helpers are intended to be used by MCP tools such as:
- `ingest_markdown_files` (read markdown from disk)
- `convert_files_to_markdown` / `ingest_pdf_files` (read source files, write converted markdown)

Example:
    >>> from pathlib import Path
    >>> root = Path("/tmp/ingest_root")
    >>> # Suppose /tmp/ingest_root/case/notes.md exists
    >>> resolved = resolve_allowlisted_file(root, "case/notes.md", allowed_suffixes={".md"})
    >>> text = read_text_lossy_utf8(resolved, max_chars=100)
    >>> assert isinstance(text, str)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class FileIngestionError(ValueError):
    """Raised when a file ingestion request violates safety constraints."""


@dataclass(frozen=True)
class FileReadResult:
    """Result of reading a file for ingestion.

    Attributes:
        path: Resolved absolute path to the file on disk.
        source_name: Basename of the file (suitable as `source_name` in ingestion).
        text: File content as text (lossy UTF-8 decoding).
        size_bytes: File size in bytes (as reported by filesystem).
        truncated: Whether the text was truncated to `max_chars`.
    """

    path: Path
    source_name: str
    text: str
    size_bytes: int
    truncated: bool


@dataclass(frozen=True)
class FileWriteResult:
    """Result of writing a file under the allowlisted root.

    Attributes:
        output_path: Resolved absolute path to the written file on disk.
        relative_path: Relative path (as provided/returned by tools) under the allowlisted root.
        size_bytes: Size in bytes of the written content (best-effort).
        overwritten: Whether an existing file was overwritten.
    """

    output_path: Path
    relative_path: str
    size_bytes: int
    overwritten: bool


def require_allowlisted_root(
    root_path: str | Path | None,
    *,
    default_root: Path | None = None,
) -> Path:
    """Validate and return a usable allowlisted ingest root.

    This supports two modes:
    1. Explicit configuration via environment/settings (`root_path`).
    2. Safe default root (`default_root`) when `root_path` is unset.

    Args:
        root_path: Root directory path from configuration (optional).
        default_root: Safe default root to use when `root_path` is unset.

    Returns:
        Resolved absolute Path to the root directory.

    Raises:
        FileIngestionError: If no root can be determined, the root does not exist,
            or the root is not a directory.
    """
    if root_path is None:
        if default_root is None:
            raise FileIngestionError(
                "File ingestion is disabled: set LEGAL_MCP_INGEST_ROOT to enable it."
            )
        root = default_root
    else:
        root = Path(root_path)

    root = root.expanduser()
    try:
        resolved_root = root.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileIngestionError(
            "File ingestion root does not exist. Set LEGAL_MCP_INGEST_ROOT to an existing directory."
        ) from error

    if not resolved_root.is_dir():
        raise FileIngestionError(
            "File ingestion root is not a directory. Set LEGAL_MCP_INGEST_ROOT to a directory."
        )

    return resolved_root


def _is_relative_path(candidate: str) -> bool:
    """Return True if candidate does not represent an absolute path."""
    try:
        return not Path(candidate).is_absolute()
    except Exception:
        # If Path parsing fails, treat it as unsafe.
        return False


def _contains_path_traversal(candidate: str) -> bool:
    """Return True if candidate includes any '..' segments."""
    parts = Path(candidate).parts
    return any(part == ".." for part in parts)


def _normalize_allowed_suffixes(allowed_suffixes: Iterable[str]) -> set[str]:
    """Normalize allowed suffixes to lowercase, dot-prefixed strings."""
    normalized: set[str] = set()
    for suffix in allowed_suffixes:
        cleaned_suffix = suffix.strip().lower()
        if not cleaned_suffix:
            continue
        if not cleaned_suffix.startswith("."):
            cleaned_suffix = f".{cleaned_suffix}"
        normalized.add(cleaned_suffix)
    return normalized


def resolve_allowlisted_file(
    root: Path,
    relative_path: str,
    *,
    allowed_suffixes: set[str] | None = None,
) -> Path:
    """Resolve a relative file path under an allowlisted root safely.

    This function enforces:
    - `relative_path` must be relative (no absolute paths)
    - No traversal segments ('..')
    - Resolved real path must stay within root (prevents symlink escapes)
    - Path must exist and be a file
    - Optional suffix allowlist (.md/.markdown etc.)

    Args:
        root: Allowlisted root directory (should already be resolved).
        relative_path: Path provided by user/tool input, relative to `root`.
        allowed_suffixes: Optional set of allowed file extensions.

    Returns:
        Resolved absolute Path to the file.

    Raises:
        FileIngestionError: If the path is unsafe or the file is invalid.
    """
    if not relative_path or not relative_path.strip():
        raise FileIngestionError("No file path provided.")

    candidate_path = relative_path.strip()

    if not _is_relative_path(candidate_path):
        raise FileIngestionError("Absolute paths are not allowed for file ingestion.")

    if _contains_path_traversal(candidate_path):
        raise FileIngestionError(
            "Path traversal ('..') is not allowed for file ingestion."
        )

    resolved_root = root.resolve()
    candidate = resolved_root / candidate_path

    try:
        resolved_candidate = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileIngestionError(
            "File not found under allowlisted ingest root."
        ) from error

    try:
        # Ensure the resolved candidate is under the resolved root
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise FileIngestionError(
            "File path escapes the allowlisted ingest root (possible symlink traversal)."
        ) from error

    if not resolved_candidate.is_file():
        raise FileIngestionError("Ingestion target must be a file.")

    if allowed_suffixes is not None:
        normalized_suffixes = _normalize_allowed_suffixes(allowed_suffixes)
        suffix = resolved_candidate.suffix.lower()
        if normalized_suffixes and suffix not in normalized_suffixes:
            allowed_list = ", ".join(sorted(normalized_suffixes))
            raise FileIngestionError(
                f"File type not allowed for ingestion. Allowed extensions: {allowed_list}"
            )

    return resolved_candidate


def read_text_lossy_utf8(path: Path, *, max_chars: int | None = None) -> str:
    """Read a file as UTF-8 text with lossy decoding.

    Args:
        path: Absolute path to a file.
        max_chars: Optional maximum number of characters to return.

    Returns:
        File contents as a string (may be truncated).

    Raises:
        FileIngestionError: If the file cannot be read.
        ValueError: If max_chars is non-positive.
    """
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars must be positive when provided.")

    try:
        with path.open("r", encoding="utf-8", errors="replace") as file_handle:
            text = file_handle.read()
    except OSError as error:
        raise FileIngestionError("Failed to read file for ingestion.") from error

    if max_chars is None:
        return text

    return text[:max_chars]


def resolve_allowlisted_write_path(
    root: Path,
    relative_path: str,
    *,
    allowed_suffixes: set[str] | None = None,
) -> Path:
    """Resolve a relative output path under an allowlisted root safely (for writing).

    This function enforces:
    - `relative_path` must be relative (no absolute paths)
    - No traversal segments ('..')
    - Parent directory must remain within root (prevents symlink escapes)
    - Optional suffix allowlist (e.g., only allow writing `.md`)
    - Output file itself may or may not exist yet

    Important:
    - This does not create directories or write the file. It only resolves a safe path.
    - It validates the *parent directory* containment. For existing files, it also
      validates the resolved file path is within root.

    Args:
        root: Allowlisted root directory (should already be resolved).
        relative_path: Desired path for writing, relative to `root`.
        allowed_suffixes: Optional set of allowed file extensions.

    Returns:
        Resolved absolute Path suitable for writing.

    Raises:
        FileIngestionError: If the path is unsafe or violates constraints.
    """
    if not relative_path or not relative_path.strip():
        raise FileIngestionError("No file path provided.")

    candidate_path = relative_path.strip()

    if not _is_relative_path(candidate_path):
        raise FileIngestionError("Absolute paths are not allowed for file ingestion.")

    if _contains_path_traversal(candidate_path):
        raise FileIngestionError(
            "Path traversal ('..') is not allowed for file ingestion."
        )

    resolved_root = root.resolve()
    candidate = resolved_root / candidate_path

    # Ensure parent directory exists and does not escape root (symlink-safe).
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileIngestionError(
            "Output directory does not exist under allowlisted ingest root."
        ) from error

    try:
        resolved_parent.relative_to(resolved_root)
    except ValueError as error:
        raise FileIngestionError(
            "Output path escapes the allowlisted ingest root (possible symlink traversal)."
        ) from error

    if allowed_suffixes is not None:
        normalized_suffixes = _normalize_allowed_suffixes(allowed_suffixes)
        suffix = candidate.suffix.lower()
        if normalized_suffixes and suffix not in normalized_suffixes:
            allowed_list = ", ".join(sorted(normalized_suffixes))
            raise FileIngestionError(
                f"File type not allowed for ingestion. Allowed extensions: {allowed_list}"
            )

    # If the file already exists, ensure it is within root even after resolving.
    try:
        if candidate.exists():
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise FileIngestionError(
            "Output path escapes the allowlisted ingest root (possible symlink traversal)."
        ) from error
    except OSError as error:
        # If we cannot resolve/inspect, treat it as unsafe.
        raise FileIngestionError(
            "Failed to validate output path for writing."
        ) from error

    return candidate


def write_text_utf8_under_allowlisted_root(
    root: Path,
    relative_path: str,
    *,
    text: str,
    allowed_suffixes: set[str] | None = None,
    overwrite: bool = True,
) -> FileWriteResult:
    """Write UTF-8 text to a file under the allowlisted root safely.

    Callers should use this for persisting converted Markdown (e.g., PDF → `.md`)
    under the ingestion root without allowing traversal or symlink escapes.

    Args:
        root: Allowlisted root directory (resolved).
        relative_path: Output path relative to `root`.
        text: Text content to write (UTF-8).
        allowed_suffixes: Optional suffix allowlist for the output file.
        overwrite: Whether to overwrite an existing file.

    Returns:
        FileWriteResult describing the write outcome.

    Raises:
        FileIngestionError: If path validation fails or the write fails.
    """
    output_path = resolve_allowlisted_write_path(
        root,
        relative_path,
        allowed_suffixes=allowed_suffixes,
    )

    existed_before = output_path.exists()

    if existed_before and not overwrite:
        raise FileIngestionError("Refusing to overwrite existing file.")

    try:
        output_path.write_text(text, encoding="utf-8")
    except OSError as error:
        raise FileIngestionError(
            "Failed to write file under allowlisted ingest root."
        ) from error

    size_bytes = 0
    try:
        size_bytes = int(output_path.stat().st_size)
    except OSError:
        size_bytes = 0

    return FileWriteResult(
        output_path=output_path.resolve(),
        relative_path=relative_path,
        size_bytes=size_bytes,
        overwritten=bool(existed_before),
    )


def read_markdown_file_for_ingestion(
    root: Path,
    relative_path: str,
    *,
    allowed_suffixes: set[str] | None = None,
    max_chars: int | None = None,
) -> FileReadResult:
    """Resolve and read a markdown file under the allowlisted root.

    Args:
        root: Allowlisted root directory (resolved).
        relative_path: Relative path of the file under root.
        allowed_suffixes: Allowed suffixes. If None, defaults to {".md", ".markdown"}.
        max_chars: Optional maximum characters to return (prevents huge payloads).

    Returns:
        FileReadResult with resolved path and text.

    Raises:
        FileIngestionError: If path validation fails or file cannot be read.
    """
    suffix_allowlist = allowed_suffixes or {".md", ".markdown"}
    resolved_path = resolve_allowlisted_file(
        root, relative_path, allowed_suffixes=suffix_allowlist
    )

    size_bytes = 0
    try:
        size_bytes = resolved_path.stat().st_size
    except OSError:
        # Size is optional; do not fail ingestion solely due to stat errors.
        size_bytes = 0

    text = read_text_lossy_utf8(resolved_path, max_chars=max_chars)

    truncated = False
    if max_chars is not None and len(text) >= max_chars:
        truncated = True

    return FileReadResult(
        path=resolved_path,
        source_name=resolved_path.name,
        text=text,
        size_bytes=int(size_bytes),
        truncated=truncated,
    )
