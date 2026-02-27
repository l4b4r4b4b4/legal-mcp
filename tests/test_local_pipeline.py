"""Tests for app.ingestion.local_pipeline — local HTML parsing and discovery.

Covers the unit-testable parts of local_pipeline.py:
- discover_local_laws: directory scanning and filtering
- parse_local_html_file: HTML → LangChain Document conversion
- _parse_law_directory: batch parsing wrapper

Integration-heavy functions (ingest_from_local_html, get_corpus_status,
get_ingested_law_abbreviations) require a running ChromaDB instance and
are NOT tested here — they are covered by local E2E tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.ingestion.local_pipeline import (
    SKIP_PATTERNS,
    SKIP_SUBSTRINGS,
    _parse_law_directory,
    discover_local_laws,
    parse_local_html_file,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# HTML Fixtures (gesetze-im-internet.de format)
# ---------------------------------------------------------------------------

SAMPLE_NORM_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<h1>B\u00fcrgerliches Gesetzbuch (BGB)</h1>
<span class="jnenbez">\u00a7 433</span>
<span class="jnentitel">Vertragstypische Pflichten beim Kaufvertrag</span>
<div class="jurAbsatz">
(1) Durch den Kaufvertrag wird der Verk\u00e4ufer einer Sache verpflichtet,
dem K\u00e4ufer die Sache zu \u00fcbergeben und das Eigentum an der Sache zu
verschaffen.
</div>
<div class="jurAbsatz">
(2) Der K\u00e4ufer ist verpflichtet, dem Verk\u00e4ufer den vereinbarten Kaufpreis
zu zahlen und die gekaufte Sache abzunehmen.
</div>
</body>
</html>
"""

SINGLE_PARAGRAPH_HTML = """
<!DOCTYPE html>
<html>
<body>
<h1>Grundgesetz (GG)</h1>
<span class="jnenbez">Art 1</span>
<span class="jnentitel">Menschenw\u00fcrde</span>
<div class="jurAbsatz">
Die W\u00fcrde des Menschen ist unantastbar.
</div>
</body>
</html>
"""

EMPTY_NORM_HTML = """
<!DOCTYPE html>
<html>
<body>
<h1>Repealed Law</h1>
<span class="jnenbez">\u00a7 999</span>
</body>
</html>
"""

NO_TITLE_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="jurAbsatz">Some content without headers.</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_html(directory: Path, filename: str, content: str) -> Path:
    """Write HTML content to a file with ISO-8859-1 encoding."""
    file_path = directory / filename
    file_path.write_text(content, encoding="iso-8859-1")
    return file_path


def _create_law_directory(
    root: Path,
    law_name: str,
    html_files: dict[str, str],
) -> Path:
    """Create a law directory with HTML files."""
    law_directory = root / law_name
    law_directory.mkdir(parents=True, exist_ok=True)
    for filename, content in html_files.items():
        _write_html(law_directory, filename, content)
    return law_directory


# ===========================================================================
# discover_local_laws
# ===========================================================================


class TestDiscoverLocalLaws:
    """Tests for discover_local_laws directory scanning."""

    def test_discovers_single_law(self, tmp_path):
        """Single law directory with one norm file is discovered."""
        _create_law_directory(tmp_path, "bgb", {"para_433.html": SAMPLE_NORM_HTML})

        result = discover_local_laws(tmp_path)

        assert len(result) == 1
        assert result[0][0] == "bgb"
        assert len(result[0][1]) == 1
        assert result[0][1][0].name == "para_433.html"

    def test_discovers_multiple_laws_sorted(self, tmp_path):
        """Multiple law directories are returned sorted alphabetically."""
        _create_law_directory(tmp_path, "stgb", {"para_1.html": SAMPLE_NORM_HTML})
        _create_law_directory(tmp_path, "bgb", {"para_433.html": SAMPLE_NORM_HTML})
        _create_law_directory(tmp_path, "gg", {"art_1.html": SINGLE_PARAGRAPH_HTML})

        result = discover_local_laws(tmp_path)

        law_names = [law_abbreviation for law_abbreviation, _ in result]
        assert law_names == ["bgb", "gg", "stgb"]

    def test_multiple_html_files_per_law(self, tmp_path):
        """Law with multiple HTML files returns all of them."""
        _create_law_directory(
            tmp_path,
            "bgb",
            {
                "para_433.html": SAMPLE_NORM_HTML,
                "para_434.html": SAMPLE_NORM_HTML,
                "para_435.html": SAMPLE_NORM_HTML,
            },
        )

        result = discover_local_laws(tmp_path)

        assert len(result) == 1
        assert len(result[0][1]) == 3

    def test_skips_index_html(self, tmp_path):
        """index.html is filtered out from discovered files."""
        _create_law_directory(
            tmp_path,
            "bgb",
            {
                "index.html": "<html></html>",
                "para_433.html": SAMPLE_NORM_HTML,
            },
        )

        result = discover_local_laws(tmp_path)

        filenames = [f.name for f in result[0][1]]
        assert "index.html" not in filenames
        assert "para_433.html" in filenames

    def test_skips_gesamt_html(self, tmp_path):
        """gesamt.html is filtered out from discovered files."""
        _create_law_directory(
            tmp_path,
            "bgb",
            {
                "gesamt.html": "<html></html>",
                "para_433.html": SAMPLE_NORM_HTML,
            },
        )

        result = discover_local_laws(tmp_path)

        filenames = [f.name for f in result[0][1]]
        assert "gesamt.html" not in filenames

    def test_skips_bjnr_files(self, tmp_path):
        """Files with 'bjnr' substring are filtered out."""
        _create_law_directory(
            tmp_path,
            "bgb",
            {
                "bjnr001950896.html": "<html></html>",
                "para_433.html": SAMPLE_NORM_HTML,
            },
        )

        result = discover_local_laws(tmp_path)

        filenames = [f.name for f in result[0][1]]
        assert all("bjnr" not in f for f in filenames)

    def test_skips_empty_law_directories(self, tmp_path):
        """Law directories with no valid HTML files are excluded."""
        law_directory = tmp_path / "empty_law"
        law_directory.mkdir()
        # Only has index.html — no real norm files
        _write_html(law_directory, "index.html", "<html></html>")

        _create_law_directory(tmp_path, "bgb", {"para_433.html": SAMPLE_NORM_HTML})

        result = discover_local_laws(tmp_path)

        law_names = [name for name, _ in result]
        assert "empty_law" not in law_names
        assert "bgb" in law_names

    def test_skips_non_directory_files(self, tmp_path):
        """Regular files in root are ignored (only directories matter)."""
        (tmp_path / "readme.txt").write_text("not a law")
        _create_law_directory(tmp_path, "bgb", {"para_433.html": SAMPLE_NORM_HTML})

        result = discover_local_laws(tmp_path)

        assert len(result) == 1
        assert result[0][0] == "bgb"

    def test_empty_root_returns_empty_list(self, tmp_path):
        """Empty root directory returns an empty list."""
        result = discover_local_laws(tmp_path)

        assert result == []

    def test_nonexistent_root_raises_file_not_found(self, tmp_path):
        """Non-existent root directory raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            discover_local_laws(tmp_path / "nonexistent")

    def test_skip_patterns_constant(self):
        """SKIP_PATTERNS contains expected filenames."""
        assert "index.html" in SKIP_PATTERNS
        assert "gesamt.html" in SKIP_PATTERNS

    def test_skip_substrings_constant(self):
        """SKIP_SUBSTRINGS contains expected patterns."""
        assert "bjnr" in SKIP_SUBSTRINGS
        assert "gesamt" in SKIP_SUBSTRINGS
        assert "xml" in SKIP_SUBSTRINGS
        assert "epub" in SKIP_SUBSTRINGS
        assert "pdf" in SKIP_SUBSTRINGS


# ===========================================================================
# parse_local_html_file
# ===========================================================================


class TestParseLocalHtmlFile:
    """Tests for parse_local_html_file HTML → Document conversion."""

    def test_parses_multi_paragraph_norm(self, tmp_path):
        """Multi-paragraph norm produces full-text + per-paragraph documents."""
        html_file = _write_html(tmp_path, "para_433.html", SAMPLE_NORM_HTML)

        documents = parse_local_html_file(html_file, "bgb")

        # 1 full-text norm + 2 individual paragraphs
        assert len(documents) == 3

        # First document: full norm
        full_norm = documents[0]
        assert full_norm.metadata["level"] == "norm"
        assert full_norm.metadata["law_abbrev"] == "BGB"
        assert full_norm.metadata["norm_id"] == "\u00a7 433"
        assert "Kaufvertrag" in full_norm.metadata["norm_title"]
        assert full_norm.metadata["paragraph_count"] == 2
        assert full_norm.metadata["jurisdiction"] == "de-federal"
        assert full_norm.metadata["source_type"] == "local_html"
        assert "bgb" in full_norm.metadata["source_url"]
        assert "Kaufvertrag" in full_norm.page_content

        # Second and third: individual paragraphs
        paragraph_one = documents[1]
        assert paragraph_one.metadata["level"] == "paragraph"
        assert paragraph_one.metadata["paragraph_index"] == 1
        assert "Verk\u00e4ufer" in paragraph_one.page_content

        paragraph_two = documents[2]
        assert paragraph_two.metadata["level"] == "paragraph"
        assert paragraph_two.metadata["paragraph_index"] == 2
        assert "K\u00e4ufer" in paragraph_two.page_content

    def test_parses_single_paragraph_norm(self, tmp_path):
        """Single-paragraph norm produces only the full-text document."""
        html_file = _write_html(tmp_path, "art_1.html", SINGLE_PARAGRAPH_HTML)

        documents = parse_local_html_file(html_file, "gg")

        # Single paragraph — no per-paragraph split
        assert len(documents) == 1
        assert documents[0].metadata["level"] == "norm"
        assert documents[0].metadata["law_abbrev"] == "GG"
        assert documents[0].metadata["norm_id"] == "Art 1"
        assert "unantastbar" in documents[0].page_content

    def test_empty_norm_returns_empty_list(self, tmp_path):
        """Norm with no paragraph content (repealed) returns empty list."""
        html_file = _write_html(tmp_path, "para_999.html", EMPTY_NORM_HTML)

        documents = parse_local_html_file(html_file, "bgb")

        assert documents == []

    def test_missing_headers_still_parses(self, tmp_path):
        """HTML without h1/jnenbez/jnentitel still extracts paragraph text."""
        html_file = _write_html(tmp_path, "no_title.html", NO_TITLE_HTML)

        documents = parse_local_html_file(html_file, "test")

        assert len(documents) == 1
        assert documents[0].metadata["law_title"] == ""
        assert documents[0].metadata["norm_id"] == ""
        assert documents[0].metadata["norm_title"] == ""
        assert "Some content" in documents[0].page_content

    def test_nonexistent_file_returns_empty_list(self, tmp_path):
        """Non-existent file returns empty list instead of raising."""
        fake_path = tmp_path / "nonexistent.html"

        documents = parse_local_html_file(fake_path, "bgb")

        assert documents == []

    def test_doc_id_sanitization(self, tmp_path):
        """Doc ID sanitizes § and spaces from norm identifiers."""
        html_file = _write_html(tmp_path, "para_433.html", SAMPLE_NORM_HTML)

        documents = parse_local_html_file(html_file, "bgb")

        norm_doc_id = documents[0].metadata["doc_id"]
        # § 433 → para_433
        assert "\u00a7" not in norm_doc_id
        assert " " not in norm_doc_id
        assert "bgb" in norm_doc_id

    def test_parent_norm_id_links_paragraphs(self, tmp_path):
        """Paragraph documents have parent_norm_id linking to the full norm."""
        html_file = _write_html(tmp_path, "para_433.html", SAMPLE_NORM_HTML)

        documents = parse_local_html_file(html_file, "bgb")

        full_norm_id = documents[0].metadata["doc_id"]
        for paragraph_document in documents[1:]:
            assert paragraph_document.metadata["parent_norm_id"] == full_norm_id

    def test_law_abbreviation_uppercased_in_metadata(self, tmp_path):
        """Law abbreviation is stored as uppercase in metadata."""
        html_file = _write_html(tmp_path, "para_1.html", SINGLE_PARAGRAPH_HTML)

        documents = parse_local_html_file(html_file, "stgb")

        assert documents[0].metadata["law_abbrev"] == "STGB"

    def test_custom_jurisdiction(self, tmp_path):
        """Custom jurisdiction parameter is stored in metadata."""
        html_file = _write_html(tmp_path, "norm.html", SINGLE_PARAGRAPH_HTML)

        documents = parse_local_html_file(
            html_file, "bgb", jurisdiction="de-state-berlin"
        )

        assert documents[0].metadata["jurisdiction"] == "de-state-berlin"

    def test_source_file_path_in_metadata(self, tmp_path):
        """Source file path is recorded in metadata."""
        html_file = _write_html(tmp_path, "norm.html", SINGLE_PARAGRAPH_HTML)

        documents = parse_local_html_file(html_file, "bgb")

        assert documents[0].metadata["source_file"] == str(html_file)

    def test_fallback_doc_id_uses_stem(self, tmp_path):
        """When norm_id is empty, doc_id falls back to file stem."""
        # HTML with no jnenbez span → empty norm_id
        html_without_norm_id = """
        <html><body>
        <h1>Test Law</h1>
        <div class="jurAbsatz">Content here.</div>
        </body></html>
        """
        html_file = _write_html(tmp_path, "special_norm.html", html_without_norm_id)

        documents = parse_local_html_file(html_file, "bgb")

        assert "special_norm" in documents[0].metadata["doc_id"]


# ===========================================================================
# _parse_law_directory
# ===========================================================================


class TestParseLawDirectory:
    """Tests for _parse_law_directory batch parsing."""

    def test_parses_all_files_in_directory(self, tmp_path):
        """All valid HTML files are parsed and documents collected."""
        law_directory = tmp_path / "bgb"
        law_directory.mkdir()
        _write_html(law_directory, "para_433.html", SAMPLE_NORM_HTML)
        _write_html(law_directory, "art_1.html", SINGLE_PARAGRAPH_HTML)

        html_files = sorted(law_directory.glob("*.html"))
        law_abbreviation, documents, errors = _parse_law_directory("bgb", html_files)

        assert law_abbreviation == "bgb"
        assert len(documents) >= 2  # At least one doc per file
        assert errors == []

    def test_collects_errors_without_crashing(self, tmp_path):
        """Errors in individual files are collected, not raised."""
        law_directory = tmp_path / "bgb"
        law_directory.mkdir()

        # Valid file
        _write_html(law_directory, "para_433.html", SAMPLE_NORM_HTML)

        # Binary garbage that will cause parsing issues
        bad_file = law_directory / "bad.html"
        bad_file.write_bytes(b"\x80\x81\x82\x83")

        html_files = sorted(law_directory.glob("*.html"))
        law_abbreviation, documents, _errors = _parse_law_directory("bgb", html_files)

        assert law_abbreviation == "bgb"
        # At least the valid file produced documents
        assert len(documents) >= 1

    def test_empty_file_list(self, tmp_path):
        """Empty file list produces no documents."""
        law_abbreviation, documents, errors = _parse_law_directory("bgb", [])

        assert law_abbreviation == "bgb"
        assert documents == []
        assert errors == []

    def test_all_empty_norms(self, tmp_path):
        """Directory of repealed norms produces no documents."""
        law_directory = tmp_path / "old_law"
        law_directory.mkdir()
        _write_html(law_directory, "para_1.html", EMPTY_NORM_HTML)
        _write_html(law_directory, "para_2.html", EMPTY_NORM_HTML)

        html_files = sorted(law_directory.glob("*.html"))
        _, documents, errors = _parse_law_directory("old_law", html_files)

        assert documents == []
        assert errors == []

    def test_multi_paragraph_with_empty_paragraph_skips_blank(self, tmp_path):
        """Empty paragraphs in multi-paragraph norms are skipped."""
        html_with_empty_paragraph = """
        <html><body>
        <h1>Test Law</h1>
        <span class="jnenbez">§ 1</span>
        <span class="jnentitel">Test Norm</span>
        <div class="jurAbsatz">First paragraph content.</div>
        <div class="jurAbsatz">   </div>
        <div class="jurAbsatz">Third paragraph content.</div>
        </body></html>
        """
        html_file = _write_html(tmp_path, "para_1.html", html_with_empty_paragraph)

        documents = parse_local_html_file(html_file, "test")

        # Full norm + 2 non-empty paragraphs (blank one skipped)
        paragraph_documents = [
            document
            for document in documents
            if document.metadata["level"] == "paragraph"
        ]
        assert len(paragraph_documents) == 2
        assert all(document.page_content.strip() for document in paragraph_documents)

    def test_xml_epub_pdf_files_skipped(self, tmp_path):
        """Files containing xml, epub, pdf substrings are filtered."""
        _create_law_directory(
            tmp_path,
            "bgb",
            {
                "para_433.html": SAMPLE_NORM_HTML,
                "bgb_xml_export.html": "<html></html>",
                "bgb_epub_version.html": "<html></html>",
                "bgb_pdf_link.html": "<html></html>",
            },
        )

        result = discover_local_laws(tmp_path)

        filenames = [f.name for f in result[0][1]]
        assert filenames == ["para_433.html"]
