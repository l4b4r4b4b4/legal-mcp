"""Tests for allowlisted file ingestion path handling.

These tests validate security properties of `resolve_allowlisted_file`,
`resolve_allowlisted_write_path`, and `require_allowlisted_root`:
- Absolute paths are rejected
- Path traversal segments (`..`) are rejected
- Paths outside the allowlisted root are rejected (including via symlink escape)
- Only allowed suffixes are accepted
- Safe writes stay under the allowlisted root and respect overwrite behavior
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.custom_documents.file_ingestion import (
    FileIngestionError,
    require_allowlisted_root,
    resolve_allowlisted_file,
    resolve_allowlisted_write_path,
    write_text_utf8_under_allowlisted_root,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestRequireAllowlistedRoot:
    """Tests for ingest-root validation."""

    def test_root_missing_without_default_fails(self) -> None:
        """Should fail fast when root is not configured and no default is provided."""
        with pytest.raises(FileIngestionError, match="LEGAL_MCP_INGEST_ROOT"):
            require_allowlisted_root(None)

    def test_root_missing_with_default_ok(self, tmp_path: Path) -> None:
        """Should use default root when config root is unset."""
        resolved = require_allowlisted_root(None, default_root=tmp_path)
        assert resolved.is_dir()
        assert resolved.resolve() == tmp_path.resolve()

    def test_root_nonexistent_fails(self, tmp_path: Path) -> None:
        """Should fail when configured root does not exist."""
        missing_root = tmp_path / "does_not_exist"
        with pytest.raises(FileIngestionError, match="does not exist"):
            require_allowlisted_root(missing_root)

    def test_root_not_a_directory_fails(self, tmp_path: Path) -> None:
        """Should fail when configured root is not a directory."""
        root_file = tmp_path / "root.txt"
        root_file.write_text("not a dir", encoding="utf-8")

        with pytest.raises(FileIngestionError, match="not a directory"):
            require_allowlisted_root(root_file)

    def test_root_directory_ok(self, tmp_path: Path) -> None:
        """Should return a resolved directory path when root is valid."""
        resolved = require_allowlisted_root(tmp_path)
        assert resolved.is_dir()
        assert resolved.resolve() == tmp_path.resolve()


class TestResolveAllowlistedFile:
    """Tests for safe path resolution."""

    def test_empty_path_rejected(self, tmp_path: Path) -> None:
        """Should reject empty/blank paths."""
        with pytest.raises(FileIngestionError, match="No file path"):
            resolve_allowlisted_file(tmp_path, "")

        with pytest.raises(FileIngestionError, match="No file path"):
            resolve_allowlisted_file(tmp_path, "   ")

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        """Should reject absolute paths."""
        absolute_path = str((tmp_path / "file.md").resolve())
        with pytest.raises(FileIngestionError, match="Absolute paths"):
            resolve_allowlisted_file(tmp_path, absolute_path)

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        """Should reject '..' traversal segments."""
        # Even if the file doesn't exist, traversal should be rejected first.
        with pytest.raises(FileIngestionError, match="Path traversal"):
            resolve_allowlisted_file(tmp_path, "../secrets.md")

        with pytest.raises(FileIngestionError, match="Path traversal"):
            resolve_allowlisted_file(tmp_path, "case/../secrets.md")

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        """Should reject paths that do not exist under root."""
        with pytest.raises(FileIngestionError, match="File not found"):
            resolve_allowlisted_file(tmp_path, "missing.md", allowed_suffixes={".md"})

    def test_directory_rejected(self, tmp_path: Path) -> None:
        """Should reject directories (ingestion target must be a file)."""
        (tmp_path / "case").mkdir()
        with pytest.raises(FileIngestionError, match="must be a file"):
            resolve_allowlisted_file(tmp_path, "case", allowed_suffixes={".md"})

    def test_suffix_allowlist_enforced(self, tmp_path: Path) -> None:
        """Should reject files whose extension is not in the allowlist."""
        candidate = tmp_path / "notes.txt"
        candidate.write_text("hello", encoding="utf-8")

        with pytest.raises(FileIngestionError, match="File type not allowed"):
            resolve_allowlisted_file(tmp_path, "notes.txt", allowed_suffixes={".md"})

    def test_suffix_allowlist_accepts_md(self, tmp_path: Path) -> None:
        """Should accept markdown files when allowed."""
        candidate = tmp_path / "notes.md"
        candidate.write_text("# Title\n", encoding="utf-8")

        resolved = resolve_allowlisted_file(
            tmp_path, "notes.md", allowed_suffixes={".md"}
        )
        assert resolved.is_file()
        assert resolved.name == "notes.md"

    def test_outside_root_rejected_via_symlink_escape(self, tmp_path: Path) -> None:
        """Should reject symlink escapes that point outside the root.

        Skips on platforms/filesystems where symlinks are not supported.
        """
        root = tmp_path / "root"
        root.mkdir()

        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "outside.md"
        outside_file.write_text("outside", encoding="utf-8")

        link_path = root / "escape.md"
        try:
            link_path.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported or not permitted on this platform")

        with pytest.raises(
            FileIngestionError, match="escapes the allowlisted ingest root"
        ):
            resolve_allowlisted_file(root, "escape.md", allowed_suffixes={".md"})

    def test_within_root_allowed_nested_path(self, tmp_path: Path) -> None:
        """Should allow nested relative paths under root."""
        (tmp_path / "case").mkdir()
        candidate = tmp_path / "case" / "doc.md"
        candidate.write_text("content", encoding="utf-8")

        resolved = resolve_allowlisted_file(
            tmp_path, "case/doc.md", allowed_suffixes={".md"}
        )
        assert resolved.resolve() == candidate.resolve()


class TestResolveAllowlistedWritePath:
    """Tests for safe path resolution for writing."""

    def test_empty_path_rejected(self, tmp_path: Path) -> None:
        """Should reject empty/blank paths."""
        with pytest.raises(FileIngestionError, match="No file path"):
            resolve_allowlisted_write_path(tmp_path, "")

        with pytest.raises(FileIngestionError, match="No file path"):
            resolve_allowlisted_write_path(tmp_path, "   ")

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        """Should reject absolute paths."""
        absolute_path = str((tmp_path / "out.md").resolve())
        with pytest.raises(FileIngestionError, match="Absolute paths"):
            resolve_allowlisted_write_path(tmp_path, absolute_path)

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        """Should reject '..' traversal segments for write paths."""
        with pytest.raises(FileIngestionError, match="Path traversal"):
            resolve_allowlisted_write_path(tmp_path, "../escape.md")

        with pytest.raises(FileIngestionError, match="Path traversal"):
            resolve_allowlisted_write_path(tmp_path, "case/../escape.md")

    def test_missing_parent_directory_rejected(self, tmp_path: Path) -> None:
        """Should reject output paths whose parent directory does not exist."""
        with pytest.raises(FileIngestionError, match="Output directory does not exist"):
            resolve_allowlisted_write_path(tmp_path, "missing_dir/out.md")

    def test_suffix_allowlist_enforced(self, tmp_path: Path) -> None:
        """Should reject output paths whose extension is not allowed."""
        (tmp_path / "case").mkdir()
        with pytest.raises(FileIngestionError, match="File type not allowed"):
            resolve_allowlisted_write_path(
                tmp_path,
                "case/out.txt",
                allowed_suffixes={".md"},
            )

    def test_within_root_allowed_nested_path(self, tmp_path: Path) -> None:
        """Should allow output paths under root when parent exists."""
        (tmp_path / "case").mkdir()

        resolved = resolve_allowlisted_write_path(
            tmp_path,
            "case/out.md",
            allowed_suffixes={".md"},
        )
        assert resolved.resolve() == (tmp_path / "case" / "out.md").resolve()

    def test_existing_symlink_escape_rejected(self, tmp_path: Path) -> None:
        """Should reject writing to an existing symlink that points outside the root.

        Skips on platforms/filesystems where symlinks are not supported.
        """
        root = tmp_path / "root"
        root.mkdir()
        (root / "case").mkdir()

        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "outside.md"
        outside_file.write_text("outside", encoding="utf-8")

        link_path = root / "case" / "escape.md"
        try:
            link_path.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported or not permitted on this platform")

        with pytest.raises(
            FileIngestionError, match="escapes the allowlisted ingest root"
        ):
            resolve_allowlisted_write_path(
                root, "case/escape.md", allowed_suffixes={".md"}
            )


class TestWriteTextUtf8UnderAllowlistedRoot:
    """Tests for safe writes under the allowlisted root."""

    def test_writes_file_under_root(self, tmp_path: Path) -> None:
        """Should write a UTF-8 file under the root."""
        (tmp_path / "case").mkdir()

        result = write_text_utf8_under_allowlisted_root(
            tmp_path,
            "case/out.md",
            text="# Title\n",
            allowed_suffixes={".md"},
            overwrite=True,
        )

        assert result.output_path.is_file()
        assert result.relative_path == "case/out.md"
        assert result.overwritten is False
        assert result.size_bytes > 0

        written_text = (tmp_path / "case" / "out.md").read_text(encoding="utf-8")
        assert written_text == "# Title\n"

    def test_overwrite_false_rejects_existing_file(self, tmp_path: Path) -> None:
        """Should refuse to overwrite when overwrite=False."""
        (tmp_path / "case").mkdir()
        out_file = tmp_path / "case" / "out.md"
        out_file.write_text("old", encoding="utf-8")

        with pytest.raises(FileIngestionError, match="Refusing to overwrite"):
            write_text_utf8_under_allowlisted_root(
                tmp_path,
                "case/out.md",
                text="new",
                allowed_suffixes={".md"},
                overwrite=False,
            )

        assert out_file.read_text(encoding="utf-8") == "old"

    def test_overwrite_true_overwrites_existing_file(self, tmp_path: Path) -> None:
        """Should overwrite when overwrite=True."""
        (tmp_path / "case").mkdir()
        out_file = tmp_path / "case" / "out.md"
        out_file.write_text("old", encoding="utf-8")

        result = write_text_utf8_under_allowlisted_root(
            tmp_path,
            "case/out.md",
            text="new",
            allowed_suffixes={".md"},
            overwrite=True,
        )

        assert result.overwritten is True
        assert out_file.read_text(encoding="utf-8") == "new"

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        """Should reject traversal in write paths."""
        with pytest.raises(FileIngestionError, match="Path traversal"):
            write_text_utf8_under_allowlisted_root(
                tmp_path,
                "../escape.md",
                text="nope",
                allowed_suffixes={".md"},
                overwrite=True,
            )
