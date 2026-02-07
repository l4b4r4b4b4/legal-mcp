#!/usr/bin/env python3
"""Test selectolax HTML parsing of German law pages from gesetze-im-internet.de."""

import sys
from dataclasses import dataclass
from urllib.request import urlopen

from selectolax.parser import HTMLParser


@dataclass
class GermanLawNorm:
    """Represents a parsed German law norm (§/Art)."""

    law_title: str
    norm_id: str  # e.g., "§ 433", "Art 1"
    norm_title: str  # e.g., "Vertragstypische Pflichten beim Kaufvertrag"
    paragraphs: list[str]  # Each Absatz (1), (2), etc.
    full_text: str  # Combined text of all paragraphs


def fetch_html(url: str) -> str:
    """Fetch HTML content with proper encoding."""
    with urlopen(url) as response:
        # German law pages use ISO-8859-1 encoding
        return response.read().decode("iso-8859-1")


def parse_german_law_page(html_content: str) -> GermanLawNorm:
    """Parse a German law HTML page using selectolax.

    HTML Structure observed:
    - Law title: <h1>
    - Norm identifier: <span class="jnenbez"> (e.g., "Art 1", "§ 433")
    - Norm title: <span class="jnentitel"> (optional)
    - Paragraphs: <div class="jurAbsatz"> contains each Absatz
    """
    tree = HTMLParser(html_content)

    # Extract law title (h1)
    h1 = tree.css_first("h1")
    law_title = h1.text(strip=True) if h1 else ""

    # Extract norm identifier (§ 433, Art 1, etc.)
    norm_id_elem = tree.css_first("span.jnenbez")
    norm_id = norm_id_elem.text(strip=True) if norm_id_elem else ""

    # Extract norm title (optional)
    norm_title_elem = tree.css_first("span.jnentitel")
    norm_title = norm_title_elem.text(strip=True) if norm_title_elem else ""

    # Extract all paragraphs (Absätze)
    paragraph_elements = tree.css("div.jurAbsatz")
    paragraphs = [elem.text(strip=True) for elem in paragraph_elements]

    # Combine all paragraphs into full text
    full_text = "\n\n".join(paragraphs)

    return GermanLawNorm(
        law_title=law_title,
        norm_id=norm_id,
        norm_title=norm_title,
        paragraphs=paragraphs,
        full_text=full_text,
    )


def analyze_and_display(url: str) -> None:
    """Fetch, parse, and display German law page."""
    print(f"\n{'=' * 80}")
    print(f"Analyzing: {url}")
    print(f"{'=' * 80}\n")

    # Fetch HTML
    html_content = fetch_html(url)
    print(f"Fetched HTML: {len(html_content):,} bytes")

    # Parse with selectolax
    norm = parse_german_law_page(html_content)

    # Display results
    print("\n--- Parsed Structure ---")
    print(f"Law Title:    {norm.law_title}")
    print(f"Norm ID:      {norm.norm_id}")
    print(f"Norm Title:   {norm.norm_title}")
    print(f"Paragraphs:   {len(norm.paragraphs)}")
    print(f"Full Text:    {len(norm.full_text)} chars")

    print("\n--- Paragraphs ---")
    for i, para in enumerate(norm.paragraphs, 1):
        print(f"\nParagraph {i} ({len(para)} chars):")
        # Show first 200 chars of each paragraph
        preview = para[:200] + "..." if len(para) > 200 else para
        print(f"  {preview}")

    print("\n--- Full Combined Text ---")
    preview = (
        norm.full_text[:500] + "..." if len(norm.full_text) > 500 else norm.full_text
    )
    print(preview)

    print(f"\n{'=' * 80}\n")


def test_performance(url: str, iterations: int) -> None:
    """Test parsing performance."""
    import time

    print(f"\n{'=' * 80}")
    print(f"Performance Test: {iterations} iterations")
    print(f"{'=' * 80}\n")

    html_content = fetch_html(url)

    start = time.perf_counter()
    for _ in range(iterations):
        parse_german_law_page(html_content)
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / iterations) * 1000
    print(f"Total time:    {elapsed:.4f}s")
    print(f"Average:       {avg_ms:.2f}ms per parse")
    print(f"Throughput:    {iterations / elapsed:.1f} parses/sec")
    print(f"\n{'=' * 80}\n")


def main() -> None:
    """Run tests on German law pages."""
    test_urls = [
        ("Grundgesetz Art 1", "https://www.gesetze-im-internet.de/gg/art_1.html"),
        ("BGB § 433", "https://www.gesetze-im-internet.de/bgb/__433.html"),
        ("StGB § 211", "https://www.gesetze-im-internet.de/stgb/__211.html"),
    ]

    if len(sys.argv) > 1:
        # Allow custom URL from command line
        test_urls = [("Custom URL", sys.argv[1])]

    for name, url in test_urls:
        try:
            print(f"\n{'#' * 80}")
            print(f"# {name}")
            print(f"{'#' * 80}")
            analyze_and_display(url)
        except Exception as e:
            print(f"Error analyzing {url}: {e}")
            import traceback

            traceback.print_exc()

    # Performance test on first URL
    if test_urls:
        try:
            test_performance(test_urls[0][1], 10)
        except Exception as e:
            print(f"Error in performance test: {e}")


if __name__ == "__main__":
    main()
