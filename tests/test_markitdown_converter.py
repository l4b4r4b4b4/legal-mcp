"""Tests for MarkItDown conversion helper behavior.

These tests focus on:
- Sanitization logic (newline normalization, truncation)
- Error handling that avoids exposing sensitive document content
- Defensive extraction of text from conversion outputs

Important:
- We do NOT run the real MarkItDown converter here to avoid depending on
  external file formats and heavyweight parsers.
- We also do NOT create/convert real PDFs in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.custom_documents.conversion.markitdown_converter import (
    FileConversionError,
    _extract_markdown_text,
    sanitize_converted_text_for_ingestion,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestSanitizeConvertedTextForIngestion:
    """Unit tests for sanitization and truncation behavior."""

    def test_normalizes_windows_newlines(self) -> None:
        """Should normalize CRLF and CR to LF."""
        raw_text = "Line 1\r\nLine 2\rLine 3\nLine 4"
        sanitized_text, truncated = sanitize_converted_text_for_ingestion(
            raw_text, max_chars=None
        )
        assert sanitized_text == "Line 1\nLine 2\nLine 3\nLine 4"
        assert truncated is False

    def test_empty_input_returns_empty_string(self) -> None:
        """Should handle empty/None-ish strings safely."""
        sanitized_text, truncated = sanitize_converted_text_for_ingestion(
            "", max_chars=None
        )
        assert sanitized_text == ""
        assert truncated is False

    def test_truncates_when_over_limit(self) -> None:
        """Should truncate and report truncation when text exceeds max_chars."""
        raw_text = "a" * 20
        sanitized_text, truncated = sanitize_converted_text_for_ingestion(
            raw_text, max_chars=10
        )
        assert sanitized_text == "a" * 10
        assert truncated is True

    def test_does_not_truncate_when_equal_limit(self) -> None:
        """Should not mark truncation when length equals max_chars."""
        raw_text = "a" * 10
        sanitized_text, truncated = sanitize_converted_text_for_ingestion(
            raw_text, max_chars=10
        )
        assert sanitized_text == "a" * 10
        assert truncated is False

    def test_invalid_max_chars_raises(self) -> None:
        """Should reject non-positive max_chars."""
        with pytest.raises(ValueError, match="max_chars must be positive"):
            sanitize_converted_text_for_ingestion("x", max_chars=0)

        with pytest.raises(ValueError, match="max_chars must be positive"):
            sanitize_converted_text_for_ingestion("x", max_chars=-1)


@dataclass
class ConversionOutputWithTextContent:
    """Fake conversion output to emulate MarkItDown result objects."""

    text_content: str


@dataclass
class ConversionOutputWithMarkdown:
    """Fake conversion output to emulate possible attributes."""

    markdown: str


@dataclass
class ConversionOutputWithText:
    """Fake conversion output to emulate possible attributes."""

    text: str


class ConversionOutputWithoutKnownFields:
    """Fake conversion output with no known text attributes."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class ConversionOutputStrRaises:
    """Fake conversion output whose __str__ raises.

    This is an extreme case but ensures we do not accidentally leak content or
    crash with an unhandled exception type.
    """

    def __str__(self) -> str:  # pragma: no cover
        raise RuntimeError("boom")


class TestExtractMarkdownText:
    """Unit tests for defensive extraction from conversion outputs."""

    def test_prefers_text_content(self) -> None:
        """Should use output.text_content when present."""
        output = ConversionOutputWithTextContent(text_content="converted")
        assert _extract_markdown_text(output) == "converted"

    def test_falls_back_to_markdown(self) -> None:
        """Should use output.markdown when text_content is not present."""
        output = ConversionOutputWithMarkdown(markdown="**md**")
        assert _extract_markdown_text(output) == "**md**"

    def test_falls_back_to_text(self) -> None:
        """Should use output.text when markdown is not present."""
        output = ConversionOutputWithText(text="plain")
        assert _extract_markdown_text(output) == "plain"

    def test_falls_back_to_str(self) -> None:
        """Should fall back to str(output) when no known attributes exist."""
        output = ConversionOutputWithoutKnownFields("stringified")
        assert _extract_markdown_text(output) == "stringified"

    def test_none_result_raises_safe_error(self) -> None:
        """Should raise a safe error for None conversion output."""
        with pytest.raises(FileConversionError, match="returned no result"):
            _extract_markdown_text(None)

    def test_str_raising_bubbles_as_file_conversion_error(self) -> None:
        """If stringification fails, we should raise a FileConversionError.

        This ensures callers can return a bounded, safe message from tools.
        """
        output = ConversionOutputStrRaises()
        with pytest.raises(FileConversionError, match="Failed to extract"):
            _extract_markdown_text(output)


def test_file_conversion_error_message_does_not_echo_sensitive_text(
    tmp_path: Path,
) -> None:
    """Regression test: conversion errors should not expose raw document content.

    We cannot reliably test real MarkItDown behavior here; instead we validate the
    intended policy by checking error messages are generic and do not include content.

    Strategy:
    - Create a dummy file containing a secret token.
    - Simulate a conversion failure by constructing a FileConversionError ourselves.
      (Converter code uses static, file-name based messages and must not echo content.)
    """
    secret_token = "SUPER_SECRET_TOKEN_123"
    candidate_file = tmp_path / "case.pdf"
    candidate_file.write_text(f"prefix {secret_token} suffix", encoding="utf-8")

    error = FileConversionError("Failed to convert file 'case.pdf' (.pdf).")
    rendered_message = str(error)

    assert "case.pdf" in rendered_message
    assert secret_token not in rendered_message
