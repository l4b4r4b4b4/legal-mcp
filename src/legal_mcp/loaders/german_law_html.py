"""German Law HTML Loader for LangChain.

Loads and parses German federal law HTML pages from gesetze-im-internet.de
into LangChain Document objects with rich metadata.

Supports Tor SOCKS proxy for IP rotation to avoid rate limiting.
Set USE_TOR=true environment variable to enable.
"""

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, urlopen

import socks
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from selectolax.parser import HTMLParser
from sockshandler import SocksiPyHandler


@dataclass
class GermanLawNorm:
    """Represents a parsed German law norm (§/Art)."""

    law_title: str
    norm_id: str  # e.g., "§ 433", "Art 1"
    norm_title: str  # e.g., "Vertragstypische Pflichten beim Kaufvertrag"
    paragraphs: list[str]  # Each Absatz (1), (2), etc.
    full_text: str  # Combined text of all paragraphs
    url: str


class GermanLawHTMLLoader(BaseLoader):
    """Load German federal law HTML pages from gesetze-im-internet.de.

    Extracts structured legal content and converts it to LangChain Documents
    with rich metadata for embedding and retrieval.

    HTML Structure parsed:
    - Law title: <h1>
    - Norm identifier: <span class="jnenbez"> (e.g., "Art 1", "§ 433")
    - Norm title: <span class="jnentitel"> (optional)
    - Paragraphs: <div class="jurAbsatz"> contains each Absatz

    Example:
        >>> loader = GermanLawHTMLLoader(
        ...     url="https://www.gesetze-im-internet.de/bgb/__433.html",
        ...     law_abbrev="BGB"
        ... )
        >>> documents = loader.load()
        >>> len(documents)  # One doc per paragraph + one for full norm
        3
    """

    def __init__(
        self,
        url: str,
        law_abbrev: str,
        jurisdiction: str = "de-federal",
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
        use_tor: bool | None = None,
        tor_host: str = "127.0.0.1",
        tor_port: int = 9050,
    ) -> None:
        """Initialize the loader.

        Args:
            url: Full URL to the law norm HTML page
            law_abbrev: Law abbreviation (e.g., "BGB", "StGB", "GG")
            jurisdiction: Legal jurisdiction (default: "de-federal")
            user_agent: User agent string for HTTP requests
            use_tor: Use Tor SOCKS proxy (default: from USE_TOR env var)
            tor_host: Tor SOCKS proxy host
            tor_port: Tor SOCKS proxy port
        """
        self.url = url
        self.law_abbrev = law_abbrev
        self.jurisdiction = jurisdiction
        self.user_agent = user_agent
        self.use_tor = (
            use_tor
            if use_tor is not None
            else os.getenv("USE_TOR", "").lower() in ("true", "1", "yes")
        )
        self.tor_host = tor_host
        self.tor_port = tor_port

    def _fetch_html(self, max_retries: int = 5, base_delay: float = 0.5) -> str:
        """Fetch HTML content with proper encoding, headers, and retries.

        Supports Tor SOCKS proxy for IP rotation when USE_TOR=true.

        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds (doubles with each retry)

        Returns:
            HTML content as string

        Raises:
            URLError: If all retries fail
        """
        request = Request(
            self.url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive",
            },
        )
        last_error: Exception | None = None

        # Create opener - use SOCKS handler for Tor, default for regular requests
        if self.use_tor:
            opener = build_opener(
                SocksiPyHandler(socks.SOCKS5, self.tor_host, self.tor_port)
            )
        else:
            opener = None  # Use default urlopen

        for attempt in range(max_retries):
            try:
                if opener:
                    with opener.open(request, timeout=30) as response:
                        return response.read().decode("iso-8859-1")
                else:
                    with urlopen(request, timeout=30) as response:
                        return response.read().decode("iso-8859-1")

            except (
                HTTPError,
                URLError,
                ConnectionResetError,
                TimeoutError,
                OSError,
            ) as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    time.sleep(delay)

        raise URLError(f"Failed after {max_retries} attempts: {last_error}")

    def _parse_html(self, html_content: str) -> GermanLawNorm:
        """Parse German law HTML into structured data.

        Args:
            html_content: Raw HTML content

        Returns:
            GermanLawNorm with extracted fields
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
            url=self.url,
        )

    def _create_documents(self, norm: GermanLawNorm) -> list[Document]:
        """Convert parsed norm into LangChain Documents.

        Creates documents at two levels:
        1. Full norm (all paragraphs combined)
        2. Individual paragraphs (for large norms)

        Args:
            norm: Parsed German law norm

        Returns:
            List of LangChain Document objects
        """
        documents: list[Document] = []

        # Base metadata shared by all documents
        base_metadata: dict[str, Any] = {
            "jurisdiction": self.jurisdiction,
            "law_abbrev": self.law_abbrev,
            "law_title": norm.law_title,
            "norm_id": norm.norm_id,
            "norm_title": norm.norm_title,
            "source_url": norm.url,
            "source_type": "html",
        }

        # Document 1: Full norm (all paragraphs combined)
        norm_doc = Document(
            page_content=norm.full_text,
            metadata={
                **base_metadata,
                "level": "norm",
                "doc_id": f"{self.law_abbrev.lower()}_{norm.norm_id.replace('§', 'para').replace(' ', '_').lower()}",
                "paragraph_count": len(norm.paragraphs),
            },
        )
        documents.append(norm_doc)

        # Documents 2+: Individual paragraphs (for fine-grained retrieval)
        # Only create if there are multiple paragraphs
        if len(norm.paragraphs) > 1:
            for i, paragraph_text in enumerate(norm.paragraphs, 1):
                para_doc = Document(
                    page_content=paragraph_text,
                    metadata={
                        **base_metadata,
                        "level": "paragraph",
                        "doc_id": f"{self.law_abbrev.lower()}_{norm.norm_id.replace('§', 'para').replace(' ', '_').lower()}_abs_{i}",
                        "paragraph_index": i,
                        "parent_norm_id": f"{self.law_abbrev.lower()}_{norm.norm_id.replace('§', 'para').replace(' ', '_').lower()}",
                    },
                )
                documents.append(para_doc)

        return documents

    def load(self) -> list[Document]:
        """Load and parse the HTML page into LangChain Documents.

        Returns:
            List of Document objects (1 norm + N paragraphs)

        Raises:
            URLError: If the page cannot be fetched
            Exception: If parsing fails
        """
        html_content = self._fetch_html()
        norm = self._parse_html(html_content)
        return self._create_documents(norm)

    def lazy_load(self) -> Iterator[Document]:
        """Lazy load documents one at a time.

        Yields:
            Document objects
        """
        yield from self.load()


class GermanLawBulkHTMLLoader(BaseLoader):
    """Load multiple German law norms from a list of URLs.

    Efficiently loads and parses multiple law pages, yielding Documents
    as they are parsed (for streaming ingestion).

    Example:
        >>> urls = [
        ...     ("BGB", "https://www.gesetze-im-internet.de/bgb/__433.html"),
        ...     ("BGB", "https://www.gesetze-im-internet.de/bgb/__434.html"),
        ... ]
        >>> loader = GermanLawBulkHTMLLoader(urls)
        >>> documents = list(loader.lazy_load())
    """

    def __init__(
        self,
        urls: list[tuple[str, str]],
        jurisdiction: str = "de-federal",
        user_agent: str = "LegalMCP/0.0.0 (Research/Education)",
    ) -> None:
        """Initialize the bulk loader.

        Args:
            urls: List of (law_abbrev, url) tuples
            jurisdiction: Legal jurisdiction (default: "de-federal")
            user_agent: User agent string for HTTP requests
        """
        self.urls = urls
        self.jurisdiction = jurisdiction
        self.user_agent = user_agent

    def lazy_load(self) -> Iterator[Document]:
        """Lazily load documents from all URLs.

        Yields:
            Document objects as they are parsed
        """
        for law_abbrev, url in self.urls:
            loader = GermanLawHTMLLoader(
                url=url,
                law_abbrev=law_abbrev,
                jurisdiction=self.jurisdiction,
                user_agent=self.user_agent,
            )
            try:
                yield from loader.lazy_load()
            except Exception as e:
                # Log error but continue processing other URLs
                print(f"Error loading {url}: {e}")
                continue

    def load(self) -> list[Document]:
        """Load all documents from all URLs.

        Returns:
            List of all Document objects
        """
        return list(self.lazy_load())
