"""German legal prompt templates for RAG pipeline.

Provides system and user prompt templates optimized for German legal Q&A.
All prompts are in German for better legal terminology understanding.

Usage:
    from app.rag.prompts import SYSTEM_PROMPT, format_user_prompt

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_user_prompt(question, context)},
    ]
"""

from __future__ import annotations

# =============================================================================
# System Prompt
# =============================================================================

SYSTEM_PROMPT = """Du bist ein Rechtsassistent für deutsches Recht. Deine Aufgabe:

1. Beantworte Fragen NUR basierend auf den bereitgestellten Gesetzestexten
2. Zitiere IMMER die relevanten Paragraphen mit [1], [2], etc.
3. Wenn die Antwort nicht aus den Quellen hervorgeht, sage: "Diese Frage kann ich anhand der verfügbaren Gesetzestexte nicht beantworten."
4. Gib keine Rechtsberatung - verweise auf professionelle Beratung für konkrete Fälle
5. Antworte präzise und strukturiert

Wichtig: Du darfst KEINE Informationen erfinden oder Paragraphen zitieren, die nicht in den Quellen stehen."""

# =============================================================================
# Context Template
# =============================================================================

CONTEXT_TEMPLATE = """=== Relevante Gesetzestexte ===

{sources}

=== Frage ===
{question}"""

SOURCE_TEMPLATE = """[{index}] {law_abbrev} {norm_id} - {title}
{content}"""

# =============================================================================
# No Sources Template
# =============================================================================

NO_SOURCES_TEMPLATE = """=== Keine relevanten Gesetzestexte gefunden ===

Die Suche hat keine passenden Gesetzestexte zu dieser Frage ergeben.

=== Frage ===
{question}"""


# =============================================================================
# Formatting Functions
# =============================================================================


def format_source(
    index: int,
    law_abbrev: str,
    norm_id: str,
    title: str,
    content: str,
    max_content_length: int = 2000,
) -> str:
    """Format a single source for the context.

    Args:
        index: 1-based source index for citation
        law_abbrev: Law abbreviation (e.g., "BGB", "StGB")
        norm_id: Norm identifier (e.g., "§ 433", "Art. 1")
        title: Section title
        content: Full text content
        max_content_length: Maximum characters for content (truncate if longer)

    Returns:
        Formatted source string with citation index
    """
    # Truncate content if too long
    if len(content) > max_content_length:
        content = content[: max_content_length - 3] + "..."

    return SOURCE_TEMPLATE.format(
        index=index,
        law_abbrev=law_abbrev or "Gesetz",
        norm_id=norm_id or "",
        title=title or "Ohne Titel",
        content=content.strip(),
    )


def format_sources(
    sources: list[dict[str, str]],
    max_sources: int = 5,
    max_content_length: int = 2000,
) -> str:
    """Format multiple sources for the context.

    Args:
        sources: List of source dictionaries with keys:
            - law_abbrev: Law abbreviation
            - norm_id: Norm identifier
            - title: Section title
            - content: Full text content
        max_sources: Maximum number of sources to include
        max_content_length: Maximum characters per source content

    Returns:
        Formatted sources string with citation indices
    """
    if not sources:
        return ""

    formatted_sources: list[str] = []
    for i, source in enumerate(sources[:max_sources], start=1):
        formatted = format_source(
            index=i,
            law_abbrev=source.get("law_abbrev", ""),
            norm_id=source.get("norm_id", ""),
            title=source.get("title", ""),
            content=source.get("content", ""),
            max_content_length=max_content_length,
        )
        formatted_sources.append(formatted)

    return "\n\n".join(formatted_sources)


def format_user_prompt(
    question: str,
    sources: list[dict[str, str]],
    max_sources: int = 5,
    max_content_length: int = 2000,
) -> str:
    """Format the complete user prompt with context and question.

    Args:
        question: User's legal question
        sources: List of source dictionaries from search results
        max_sources: Maximum number of sources to include
        max_content_length: Maximum characters per source content

    Returns:
        Complete user prompt with context and question
    """
    if not sources:
        return NO_SOURCES_TEMPLATE.format(question=question)

    formatted_sources = format_sources(
        sources=sources,
        max_sources=max_sources,
        max_content_length=max_content_length,
    )

    return CONTEXT_TEMPLATE.format(
        sources=formatted_sources,
        question=question,
    )


__all__ = [
    "CONTEXT_TEMPLATE",
    "NO_SOURCES_TEMPLATE",
    "SOURCE_TEMPLATE",
    "SYSTEM_PROMPT",
    "format_source",
    "format_sources",
    "format_user_prompt",
]
