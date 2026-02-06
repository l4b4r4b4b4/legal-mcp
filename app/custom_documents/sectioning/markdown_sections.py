r"""Conservative Markdown section extraction for custom documents.

This module parses Markdown text into a list of sections using **only** ATX-style
headings (lines starting with `#`).

It is intentionally conservative:
- It does not infer headings from formatting, numbering, or legal patterns.
- It does not attempt to be a full Markdown parser.
- It is designed for stable, deterministic section spans suitable for metadata.

If the document contains no headings, the entire document is returned as a single
section titled `"Document"`.

Example:
    >>> from app.custom_documents.sectioning.markdown_sections import extract_markdown_sections
    >>> sections = extract_markdown_sections("# A\\nText\\n## B\\nMore")
    >>> [s.title for s in sections]
    ['A', 'B']
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class MarkdownSection:
    """A contiguous span of Markdown text belonging to a single heading-defined section.

    Attributes:
        section_index: Stable 0-based index of the section.
        title: Heading title text (or `"Document"` if no headings exist).
        level: Heading level 1-6 (or 0 for the synthetic `"Document"` section).
        path: Stable section path for nested headings (e.g., "0", "0/1/0").
        start_char: Start character offset in the original Markdown text (inclusive).
        end_char: End character offset in the original Markdown text (exclusive).
    """

    section_index: int
    title: str
    level: int
    path: str
    start_char: int
    end_char: int

    def slice_text(self, markdown_text: str) -> str:
        """Return the Markdown substring for this section."""
        return markdown_text[self.start_char : self.end_char]


@dataclass(frozen=True)
class _Heading:
    """Internal representation of a heading match."""

    level: int
    title: str
    start_char: int
    end_char: int


def _iter_lines_with_offsets(markdown_text: str) -> Iterable[tuple[int, str]]:
    """Yield `(line_start_offset, line_text)` for each line in `markdown_text`."""
    cursor = 0
    for line in markdown_text.splitlines(keepends=True):
        yield cursor, line
        cursor += len(line)


def _parse_atx_heading(line_text: str) -> tuple[int, str] | None:
    """Parse an ATX heading from a line, returning `(level, title)` or None.

    Recognizes headings in the form:
      # Heading
      ## Heading ##
    and ignores headings inside fenced code blocks (handled by caller via state).

    This parser is conservative:
    - Leading spaces before `#` are not allowed (to avoid false positives).
    - It requires at least one space after the `#` run.
    - It permits optional trailing `#` markers (as common Markdown allows).
    """
    if not line_text:
        return None

    # Strip only newline characters; keep other whitespace for conservative parsing.
    stripped_newlines = line_text.rstrip("\r\n")

    if not stripped_newlines.startswith("#"):
        return None

    hash_count = 0
    for character in stripped_newlines:
        if character == "#":
            hash_count += 1
        else:
            break

    if hash_count < 1 or hash_count > 6:
        return None

    remainder = stripped_newlines[hash_count:]
    if not remainder.startswith(" "):
        return None

    title_candidate = remainder.strip()

    # Remove optional trailing hash markers, if they are separated by spaces.
    # E.g. "Heading ##" -> "Heading"
    if " #" in title_candidate:
        # Conservative strip: only strip hashes at end if preceded by space.
        while title_candidate.endswith("#"):
            title_candidate = title_candidate[:-1].rstrip()

    if not title_candidate:
        return None

    return hash_count, title_candidate


def _find_headings(markdown_text: str) -> list[_Heading]:
    """Find ATX headings in markdown text, excluding fenced code blocks."""
    headings: list[_Heading] = []
    in_fenced_code_block = False
    active_fence: str | None = None

    for line_start, line_text in _iter_lines_with_offsets(markdown_text):
        # Detect fenced code blocks (``` or ~~~), conservatively at start of line.
        stripped_newlines = line_text.rstrip("\r\n")
        if stripped_newlines.startswith("```") or stripped_newlines.startswith("~~~"):
            fence_marker = stripped_newlines[:3]
            if not in_fenced_code_block:
                in_fenced_code_block = True
                active_fence = fence_marker
            else:
                if active_fence == fence_marker:
                    in_fenced_code_block = False
                    active_fence = None
            continue

        if in_fenced_code_block:
            continue

        parsed = _parse_atx_heading(line_text)
        if parsed is None:
            continue

        level, title = parsed
        headings.append(
            _Heading(
                level=level,
                title=title,
                start_char=line_start,
                end_char=line_start + len(line_text),
            )
        )

    return headings


def _compute_section_paths(headings: list[_Heading]) -> list[str]:
    """Compute stable nested section paths for headings.

    The path is built based on heading level nesting. For example:
    - First H1 -> "0"
    - First H2 under that H1 -> "0/0"
    - Second H2 under that H1 -> "0/1"
    - An H3 under the second H2 -> "0/1/0"
    """
    paths: list[str] = []
    counters_by_level: dict[int, int] = {}
    current_stack: list[int] = []

    for heading in headings:
        level = heading.level

        # Pop to parent level
        while current_stack and current_stack[-1] >= level:
            current_stack.pop()

        # Reset deeper counters when descending/ascending levels
        for existing_level in list(counters_by_level.keys()):
            if existing_level > level:
                counters_by_level.pop(existing_level, None)

        index_at_level = counters_by_level.get(level, 0)
        counters_by_level[level] = index_at_level + 1

        # Build path from parent stack + this index
        path_components: list[str] = []
        for parent_level in current_stack:
            parent_index = counters_by_level.get(parent_level, 1) - 1
            path_components.append(str(parent_index))

        path_components.append(str(index_at_level))
        paths.append("/".join(path_components))

        current_stack.append(level)

    return paths


def extract_markdown_sections(markdown_text: str) -> list[MarkdownSection]:
    """Extract conservative sections from Markdown using ATX headings only.

    Args:
        markdown_text: Markdown document text.

    Returns:
        List of MarkdownSection spans covering the entire document.

    Notes:
        - The returned spans cover `markdown_text` from 0..len(text) without gaps.
        - If headings exist, each section begins at its heading line.
        - If no headings exist, a single section is returned.
    """
    normalized_text = markdown_text or ""
    if not normalized_text:
        return [
            MarkdownSection(
                section_index=0,
                title="Document",
                level=0,
                path="0",
                start_char=0,
                end_char=0,
            )
        ]

    headings = _find_headings(normalized_text)
    if not headings:
        return [
            MarkdownSection(
                section_index=0,
                title="Document",
                level=0,
                path="0",
                start_char=0,
                end_char=len(normalized_text),
            )
        ]

    section_paths = _compute_section_paths(headings)

    sections: list[MarkdownSection] = []
    for section_index, heading in enumerate(headings):
        start_char = heading.start_char
        if section_index + 1 < len(headings):
            end_char = headings[section_index + 1].start_char
        else:
            end_char = len(normalized_text)

        sections.append(
            MarkdownSection(
                section_index=section_index,
                title=heading.title,
                level=heading.level,
                path=section_paths[section_index],
                start_char=start_char,
                end_char=end_char,
            )
        )

    # Ensure coverage (conservative): if the first heading doesn't start at 0,
    # prepend a synthetic "Document" section for the preamble.
    if sections and sections[0].start_char > 0:
        sections = [
            MarkdownSection(
                section_index=0,
                title="Document",
                level=0,
                path="0",
                start_char=0,
                end_char=sections[0].start_char,
            ),
            *[
                MarkdownSection(
                    section_index=section.section_index + 1,
                    title=section.title,
                    level=section.level,
                    path=section.path,
                    start_char=section.start_char,
                    end_char=section.end_char,
                )
                for section in sections
            ],
        ]

    return sections


__all__ = [
    "MarkdownSection",
    "extract_markdown_sections",
]
