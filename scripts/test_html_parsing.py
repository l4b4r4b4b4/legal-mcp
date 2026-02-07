#!/usr/bin/env python3
"""Test HTML parsing of German law pages from gesetze-im-internet.de."""

import sys
from html.parser import HTMLParser
from typing import Any
from urllib.request import urlopen


class GermanLawHTMLAnalyzer(HTMLParser):
    """Analyze structure of German law HTML pages."""

    def __init__(self) -> None:
        super().__init__()
        self.indent = 0
        self.in_content = False
        self.structure: list[dict[str, Any]] = []
        self.current_path: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track opening tags and their attributes."""
        attrs_dict = dict(attrs)

        # Build display string
        attr_display = ""
        if "class" in attrs_dict:
            attr_display = f'class="{attrs_dict["class"]}"'
        elif "id" in attrs_dict:
            attr_display = f'id="{attrs_dict["id"]}"'
        elif "href" in attrs_dict:
            href = attrs_dict["href"]
            if href:
                attr_display = (
                    f'href="{href[:30]}..."' if len(href) > 30 else f'href="{href}"'
                )

        print("  " * self.indent + f"<{tag} {attr_display}>")

        # Track structure for analysis
        self.current_path.append(tag)
        self.structure.append(
            {
                "tag": tag,
                "attrs": attrs_dict,
                "depth": self.indent,
                "path": list(self.current_path),
            }
        )

        self.indent += 1

    def handle_endtag(self, tag: str) -> None:
        """Track closing tags."""
        self.indent = max(0, self.indent - 1)
        if self.current_path and self.current_path[-1] == tag:
            self.current_path.pop()

    def handle_data(self, data: str) -> None:
        """Track text content."""
        data = data.strip()
        if data and len(data) > 3:  # Skip whitespace and tiny fragments
            preview = data[:80] + "..." if len(data) > 80 else data
            print("  " * self.indent + f'TEXT: "{preview}"')


def fetch_and_analyze(url: str) -> None:
    """Fetch HTML page and analyze structure."""
    print(f"\n{'=' * 80}")
    print(f"Analyzing: {url}")
    print(f"{'=' * 80}\n")

    # Fetch with proper encoding
    with urlopen(url) as response:
        # German law pages use ISO-8859-1 encoding
        html_content = response.read().decode("iso-8859-1")

    # Parse and analyze
    parser = GermanLawHTMLAnalyzer()
    parser.feed(html_content)

    # Print summary
    print(f"\n{'=' * 80}")
    print("Summary:")
    print(f"  Total elements: {len(parser.structure)}")

    # Find key structural elements
    headings = [s for s in parser.structure if s["tag"] in ["h1", "h2", "h3", "h4"]]
    divs_with_class = [
        s for s in parser.structure if s["tag"] == "div" and s["attrs"].get("class")
    ]
    paragraphs = [s for s in parser.structure if s["tag"] == "p"]

    print(f"  Headings (h1-h4): {len(headings)}")
    print(f"  Divs with class: {len(divs_with_class)}")
    print(f"  Paragraphs: {len(paragraphs)}")

    if divs_with_class:
        print("\n  Key div classes:")
        classes = {s["attrs"].get("class", "") for s in divs_with_class}
        for cls in sorted(classes):
            if cls:
                print(f"    - {cls}")

    print(f"{'=' * 80}\n")


def main() -> None:
    """Test HTML parsing with multiple examples."""
    test_urls = [
        "https://www.gesetze-im-internet.de/gg/art_1.html",  # Grundgesetz Article 1
        "https://www.gesetze-im-internet.de/bgb/__433.html",  # BGB § 433
        "https://www.gesetze-im-internet.de/stgb/__211.html",  # StGB § 211 (if exists)
    ]

    if len(sys.argv) > 1:
        # Allow custom URL from command line
        test_urls = [sys.argv[1]]

    for url in test_urls:
        try:
            fetch_and_analyze(url)
        except Exception as e:
            print(f"Error analyzing {url}: {e}")
            print()


if __name__ == "__main__":
    main()
