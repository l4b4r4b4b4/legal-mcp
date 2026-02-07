"""Tests for conservative Markdown section extraction and ingestion section metadata.

These tests cover:
- Section extraction by ATX headings only (conservative mode)
- Ignoring headings inside fenced code blocks
- Fallback behavior when no headings exist
- Presence of `section_*` metadata on ingested chunks
"""

from __future__ import annotations

import pytest

from app.custom_documents.embeddings import (
    AddChunksResult,
    CustomDocumentEmbeddingStore,
    TextChunk,
)
from app.custom_documents.pipeline import ingest_custom_documents
from app.custom_documents.sectioning.markdown_sections import extract_markdown_sections


class InMemoryCustomDocumentStore(CustomDocumentEmbeddingStore):
    """A test store that captures chunks without touching Chroma.

    This avoids I/O and external dependencies. It emulates the `add_text_chunks`
    contract enough for the ingestion pipeline tests.
    """

    def __init__(self) -> None:
        # Intentionally do not call base init; it would create Chroma clients.
        self.captured_chunks: list[TextChunk] = []

    def add_text_chunks(  # type: ignore[override]
        self,
        chunks: list[TextChunk],
        *,
        batch_size: int = 256,
        replace: bool = False,
    ) -> AddChunksResult:
        # `replace` is intentionally ignored here; deletion behavior is tested at
        # the store layer separately.
        self.captured_chunks.extend(chunks)

        return AddChunksResult(
            vectors_added=len(chunks),
            chunk_ids=[chunk.chunk_id for chunk in chunks],
        )


class TestExtractMarkdownSections:
    """Unit tests for conservative ATX heading-based section extraction."""

    def test_empty_text_returns_single_document_section(self) -> None:
        """Should return one synthetic section for empty input."""
        sections = extract_markdown_sections("")
        assert len(sections) == 1
        assert sections[0].title == "Document"
        assert sections[0].level == 0
        assert sections[0].path == "0"
        assert sections[0].start_char == 0
        assert sections[0].end_char == 0

    def test_no_headings_returns_single_document_section(self) -> None:
        """Should return one synthetic section when no headings exist."""
        text = "Line one\nLine two\n"
        sections = extract_markdown_sections(text)

        assert len(sections) == 1
        section = sections[0]
        assert section.title == "Document"
        assert section.level == 0
        assert section.path == "0"
        assert section.start_char == 0
        assert section.end_char == len(text)
        assert section.slice_text(text) == text

    def test_simple_headings_split_into_sections(self) -> None:
        """Should split into sections starting at each ATX heading."""
        text = "# A\nAlpha\n## B\nBeta\n"
        sections = extract_markdown_sections(text)

        assert [section.title for section in sections] == ["A", "B"]
        assert [section.level for section in sections] == [1, 2]

        a_text = sections[0].slice_text(text)
        b_text = sections[1].slice_text(text)

        assert a_text.startswith("# A")
        assert "Alpha" in a_text
        assert "# A" not in b_text
        assert b_text.startswith("## B")
        assert "Beta" in b_text

    def test_preamble_before_first_heading_becomes_document_section(self) -> None:
        """If text exists before first heading, it becomes a synthetic preamble section."""
        text = "Preamble line\n# Heading\nBody\n"
        sections = extract_markdown_sections(text)

        assert len(sections) == 2
        preamble, heading = sections

        assert preamble.title == "Document"
        assert preamble.level == 0
        assert preamble.slice_text(text).startswith("Preamble line")

        assert heading.title == "Heading"
        assert heading.level == 1
        assert heading.slice_text(text).startswith("# Heading")

    def test_ignores_headings_in_fenced_code_blocks(self) -> None:
        """Headings inside fenced code blocks must not create sections."""
        text = "# Real\nOutside\n```\n# Not a heading\n```\n## Also real\nMore\n"
        sections = extract_markdown_sections(text)

        assert [section.title for section in sections] == ["Real", "Also real"]
        extracted_titles = [
            section.slice_text(text).splitlines()[0] for section in sections
        ]
        assert extracted_titles[0].startswith("# Real")
        assert extracted_titles[1].startswith("## Also real")

    def test_requires_space_after_hashes(self) -> None:
        """Lines like '##NotHeading' should not be treated as headings.

        Note: With conservative sectioning, any preamble text before the first
        valid heading becomes a synthetic "Document" section.
        """
        text = "##NotHeading\n# Heading\nBody\n"
        sections = extract_markdown_sections(text)

        assert len(sections) == 2
        assert sections[0].title == "Document"
        assert sections[1].title == "Heading"

    def test_trailing_hashes_are_stripped_conservatively(self) -> None:
        """Headings like '# Title ##' should produce title 'Title'."""
        text = "# Title ##\nBody\n"
        sections = extract_markdown_sections(text)

        assert len(sections) == 1
        assert sections[0].title == "Title"


class TestIngestionSectionMetadata:
    """Integration-ish tests: ingestion should attach `section_*` metadata to chunks."""

    def test_ingestion_adds_section_metadata_for_heading_document(self) -> None:
        """Each chunk must include `section_*` fields derived from Markdown headings."""
        store = InMemoryCustomDocumentStore()
        text = "# Intro\n" + ("A" * 2500) + "\n## Details\n" + ("B" * 2500)

        result = ingest_custom_documents(
            tenant_id="t_test",
            case_id="c_test",
            documents=[
                {
                    "source_name": "doc.md",
                    "text": text,
                    "metadata": {"relative_path": "case/doc.md"},
                }
            ],
            tags=["case"],
            chunking={"chunk_size_chars": 500, "chunk_overlap_chars": 100},
            store=store,
            replace=False,
        )

        assert result["status"] == "complete"
        assert store.captured_chunks, "Expected ingestion to write at least one chunk"

        for chunk in store.captured_chunks:
            metadata = chunk.metadata
            # Required section fields
            assert "section_index" in metadata
            assert "section_title" in metadata
            assert "section_level" in metadata
            assert "section_path" in metadata
            assert "section_start_char" in metadata
            assert "section_end_char" in metadata

            assert isinstance(metadata["section_index"], int)
            assert isinstance(metadata["section_title"], str)
            assert isinstance(metadata["section_level"], int)
            assert isinstance(metadata["section_path"], str)

            # Ensure standard required metadata still exists
            assert metadata.get("tenant_id") == "t_test"
            assert metadata.get("case_id") == "c_test"
            assert metadata.get("source_name") == "doc.md"
            assert metadata.get("document_id"), "document_id should be present"
            assert metadata.get("chunk_id"), "chunk_id should be present"

    def test_ingestion_fallback_section_metadata_when_no_headings(self) -> None:
        """If no headings exist, chunks should still include a synthetic Document section."""
        store = InMemoryCustomDocumentStore()
        text = "Plain text\n" + ("X" * 3000)

        result = ingest_custom_documents(
            tenant_id="t_test",
            case_id=None,
            documents=[{"source_name": "plain.txt", "text": text}],
            tags=None,
            chunking={"chunk_size_chars": 700, "chunk_overlap_chars": 100},
            store=store,
            replace=False,
        )

        assert result["status"] == "complete"
        assert store.captured_chunks

        titles = {
            chunk.metadata.get("section_title") for chunk in store.captured_chunks
        }
        levels = {
            chunk.metadata.get("section_level") for chunk in store.captured_chunks
        }

        assert titles == {"Document"}
        assert levels == {0}

    def test_ingestion_section_indices_are_stable_types(self) -> None:
        """Section metadata types should remain stable across chunks."""
        store = InMemoryCustomDocumentStore()
        text = "# One\n" + ("A" * 1200) + "\n# Two\n" + ("B" * 1200)

        ingest_custom_documents(
            tenant_id="t_test",
            case_id="c_test",
            documents=[{"source_name": "two_sections.md", "text": text}],
            tags=["x"],
            chunking={"chunk_size_chars": 400, "chunk_overlap_chars": 100},
            store=store,
            replace=False,
        )

        assert store.captured_chunks
        for chunk in store.captured_chunks:
            assert isinstance(chunk.metadata["section_index"], int)
            assert isinstance(chunk.metadata["section_level"], int)
            assert isinstance(chunk.metadata["section_start_char"], int)
            assert isinstance(chunk.metadata["section_end_char"], int)

    def test_ingestion_respects_max_chunks_per_document_across_sections(self) -> None:
        """max_chunks_per_document should cap total chunks across all sections."""
        store = InMemoryCustomDocumentStore()
        text = "# A\n" + ("A" * 4000) + "\n# B\n" + ("B" * 4000)

        result = ingest_custom_documents(
            tenant_id="t_test",
            case_id="c_test",
            documents=[{"source_name": "capped.md", "text": text}],
            tags=None,
            chunking={
                "chunk_size_chars": 400,
                "chunk_overlap_chars": 100,
                "max_chunks_per_document": 5,
            },
            store=store,
            replace=False,
        )

        assert result["status"] == "complete"
        assert len(store.captured_chunks) <= 5

        summary_documents = result.get("documents", [])
        assert summary_documents, "Expected per-document summaries"
        assert summary_documents[0]["chunks_created"] <= 5


@pytest.mark.parametrize(
    ("markdown_text", "expected_titles"),
    [
        ("# A\nx\n## B\ny\n", ["A", "B"]),
        ("Preamble\n# A\nx\n", ["Document", "A"]),
        ("No headings\n", ["Document"]),
    ],
)
def test_extract_markdown_sections_titles(
    markdown_text: str, expected_titles: list[str]
) -> None:
    """Parametrized sanity coverage for section titles."""
    sections = extract_markdown_sections(markdown_text)
    assert [section.title for section in sections] == expected_titles
