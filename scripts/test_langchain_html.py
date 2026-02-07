#!/usr/bin/env python3
"""Test LangChain HTMLSplitter with German law pages."""

import sys
from urllib.request import urlopen

from langchain_text_splitters import (
    HTMLHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


def fetch_html(url: str) -> str:
    """Fetch HTML content with proper encoding."""
    with urlopen(url) as response:
        # German law pages use ISO-8859-1 encoding
        return response.read().decode("iso-8859-1")


def test_html_header_splitter(url: str) -> None:
    """Test HTMLHeaderTextSplitter on German law page."""
    print(f"\n{'=' * 80}")
    print(f"Testing HTMLHeaderTextSplitter on: {url}")
    print(f"{'=' * 80}\n")

    html_content = fetch_html(url)

    # Define headers to split on
    headers_to_split_on = [
        ("h1", "Law"),
        ("h2", "Section"),
        ("h3", "Subsection"),
    ]

    # Create splitter
    html_splitter = HTMLHeaderTextSplitter(headers_to_split_on=headers_to_split_on)

    # Split the HTML
    html_header_splits = html_splitter.split_text(html_content)

    print(f"Number of splits: {len(html_header_splits)}")
    print()

    # Display each split
    for i, doc in enumerate(html_header_splits):
        print(f"--- Split {i + 1} ---")
        print(f"Metadata: {doc.metadata}")
        print(f"Content length: {len(doc.page_content)} chars")
        print(f"Content preview:\n{doc.page_content[:300]}...")
        print()


def test_recursive_splitter_after_html(url: str) -> None:
    """Test combining HTML splitter with recursive character splitter."""
    print(f"\n{'=' * 80}")
    print(f"Testing HTML + Recursive Splitter on: {url}")
    print(f"{'=' * 80}\n")

    html_content = fetch_html(url)

    # First split by headers
    headers_to_split_on = [
        ("h1", "Law"),
        ("h2", "Section"),
        ("div", "Content"),
    ]

    html_splitter = HTMLHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    html_header_splits = html_splitter.split_text(html_content)

    # Then split large chunks further
    chunk_size = 500
    chunk_overlap = 50

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    # Split documents that are too large
    all_splits = []
    for doc in html_header_splits:
        if len(doc.page_content) > chunk_size:
            # Split this document further
            sub_splits = text_splitter.split_documents([doc])
            all_splits.extend(sub_splits)
            print(
                f"Split large doc ({len(doc.page_content)} chars) into {len(sub_splits)} chunks"
            )
        else:
            all_splits.append(doc)

    print(f"\nTotal splits after recursive splitting: {len(all_splits)}")
    print()

    # Show examples
    for i, doc in enumerate(all_splits[:5]):  # Show first 5
        print(f"--- Split {i + 1} ---")
        print(f"Metadata: {doc.metadata}")
        print(f"Content length: {len(doc.page_content)} chars")
        print(f"Content preview:\n{doc.page_content[:200]}...")
        print()


def test_custom_extraction(url: str) -> None:
    """Test custom extraction of German law structure."""
    print(f"\n{'=' * 80}")
    print(f"Testing Custom Extraction on: {url}")
    print(f"{'=' * 80}\n")

    html_content = fetch_html(url)

    # Key observations from HTML structure:
    # - Law title: <h1> with class potentially
    # - Norm identifier: <span class="jnenbez"> (e.g., "Art 1", "§ 433")
    # - Norm title: <span class="jnentitel"> (optional)
    # - Paragraphs: <div class="jurAbsatz"> contains each Absatz (paragraph)

    from html.parser import HTMLParser

    class GermanLawParser(HTMLParser):
        """Parse German law HTML structure."""

        def __init__(self) -> None:
            super().__init__()
            self.law_title = ""
            self.norm_id = ""
            self.norm_title = ""
            self.paragraphs = []
            self.current_tag = ""
            self.current_class = ""

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            self.current_tag = tag
            attrs_dict = dict(attrs)
            self.current_class = attrs_dict.get("class", "")

        def handle_data(self, data: str) -> None:
            data = data.strip()
            if not data:
                return

            if self.current_tag == "h1" and not self.law_title:
                self.law_title = data
            elif self.current_class == "jnenbez":
                self.norm_id = data
            elif self.current_class == "jnentitel":
                self.norm_title = data
            elif self.current_class == "jurAbsatz":
                # Each jurAbsatz is a paragraph (Absatz)
                self.paragraphs.append(data)

    parser = GermanLawParser()
    parser.feed(html_content)

    print(f"Law Title: {parser.law_title}")
    print(f"Norm ID: {parser.norm_id}")
    print(f"Norm Title: {parser.norm_title}")
    print(f"Paragraphs found: {len(parser.paragraphs)}")
    print()

    for i, para in enumerate(parser.paragraphs, 1):
        print(f"--- Paragraph {i} ---")
        print(f"Length: {len(para)} chars")
        print(f"Content: {para[:200]}...")
        print()


def main() -> None:
    """Run all tests."""
    test_urls = [
        "https://www.gesetze-im-internet.de/gg/art_1.html",  # Grundgesetz Article 1
        "https://www.gesetze-im-internet.de/bgb/__433.html",  # BGB § 433
    ]

    if len(sys.argv) > 1:
        test_urls = [sys.argv[1]]

    for url in test_urls:
        try:
            print("\n" + "=" * 80)
            print(f"TESTING URL: {url}")
            print("=" * 80)

            test_html_header_splitter(url)
            test_recursive_splitter_after_html(url)
            test_custom_extraction(url)

        except Exception as e:
            print(f"Error testing {url}: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
