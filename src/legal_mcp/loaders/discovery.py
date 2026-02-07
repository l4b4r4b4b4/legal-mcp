"""HTML-only discovery pipeline for German federal laws.

Discovers all laws and their norms directly from gesetze-im-internet.de HTML pages,
eliminating the need for XML corpus as primary source.

Discovery flow:
1. Fetch main index page → get alphabet links (A-Z, 0-9)
2. Fetch each letter page (Teilliste_A.html, etc.) → extract law abbreviations + URLs
3. Fetch each law's index page → extract norm URLs
4. Use GermanLawHTMLLoader to fetch and parse each norm

This provides a pure HTML approach that's always up-to-date with the official source.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from selectolax.parser import HTMLParser

# Base URL for all German federal laws
BASE_URL = "https://www.gesetze-im-internet.de"


@dataclass
class LawInfo:
    """Information about a law discovered from the index."""

    abbreviation: str  # e.g., "BGB", "StGB", "GG"
    title: str  # e.g., "Bürgerliches Gesetzbuch"
    url: str  # e.g., "https://www.gesetze-im-internet.de/bgb/"


@dataclass
class NormInfo:
    """Information about a norm (§/Art) within a law."""

    law_abbreviation: str  # Parent law abbreviation
    norm_id: str  # e.g., "§ 433", "Art 1"
    url: str  # Full URL to the norm HTML page


@dataclass
class DiscoveryResult:
    """Result of the discovery process."""

    laws: list[LawInfo] = field(default_factory=list)
    norms: list[NormInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class GermanLawDiscovery:
    """Discover all German federal laws and norms from HTML pages.

    Example:
        >>> discovery = GermanLawDiscovery()
        >>> laws = list(discovery.discover_laws())
        >>> print(f"Found {len(laws)} laws")
        Found 6871 laws

        >>> # Get norms for a specific law
        >>> norms = list(discovery.discover_norms(laws[0]))
        >>> print(f"Found {len(norms)} norms in {laws[0].abbreviation}")
    """

    # Letter pages for the alphabetical index
    ALPHABET_PAGES: ClassVar[list[str]] = [
        "Teilliste_A.html",
        "Teilliste_B.html",
        "Teilliste_C.html",
        "Teilliste_D.html",
        "Teilliste_E.html",
        "Teilliste_F.html",
        "Teilliste_G.html",
        "Teilliste_H.html",
        "Teilliste_I.html",
        "Teilliste_J.html",
        "Teilliste_K.html",
        "Teilliste_L.html",
        "Teilliste_M.html",
        "Teilliste_N.html",
        "Teilliste_O.html",
        "Teilliste_P.html",
        "Teilliste_Q.html",
        "Teilliste_R.html",
        "Teilliste_S.html",
        "Teilliste_T.html",
        "Teilliste_U.html",
        "Teilliste_V.html",
        "Teilliste_W.html",
        "Teilliste_X.html",
        "Teilliste_Y.html",
        "Teilliste_Z.html",
        "Teilliste_1.html",
        "Teilliste_2.html",
        "Teilliste_3.html",
        "Teilliste_4.html",
        "Teilliste_5.html",
        "Teilliste_6.html",
        "Teilliste_7.html",
        "Teilliste_8.html",
        "Teilliste_9.html",
    ]

    def __init__(
        self,
        user_agent: str = "LegalMCP/0.0.0 (Research/Education)",
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize the discovery service.

        Args:
            user_agent: User agent string for HTTP requests
            base_url: Base URL for gesetze-im-internet.de
        """
        self.user_agent = user_agent
        self.base_url = base_url

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content with proper encoding and headers."""
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=30) as response:
            # German law pages use ISO-8859-1 encoding
            return response.read().decode("iso-8859-1")

    def _parse_letter_page(self, html_content: str) -> list[LawInfo]:
        """Parse a letter index page to extract law information.

        HTML structure observed:
        - Each law is a link to ./abbrev/index.html with abbreviation as text
        - Title follows the abbreviation in parent element
        - URL is relative to base_url

        Example links:
        href="./bgb/index.html" text="BGB"
        href="./betrkv/index.html" text="BetrKV"
        """
        tree = HTMLParser(html_content)
        laws: list[LawInfo] = []

        # Find all links to law directories
        # Pattern: links that go to ./abbrev/index.html
        for link in tree.css("a"):
            href = link.attributes.get("href", "")

            # Skip non-law links (PDF, external, etc.)
            if not href or href.startswith("http") or href.endswith(".pdf"):
                continue

            # Law directory links look like "./bgb/index.html" or "./betrkv/index.html"
            # Pattern: ./abbrev/index.html where abbrev contains lowercase letters, numbers, underscores
            match = re.match(r"^\./([a-z0-9_]+)/index\.html$", href.lower())
            if not match:
                continue

            abbrev_lower = match.group(1)
            abbrev_text = link.text(strip=True)

            # Skip empty or non-abbreviation links
            if not abbrev_text or len(abbrev_text) > 50:
                continue

            # The abbreviation in the link text (may have different case)
            abbreviation = abbrev_text

            # Try to extract title from surrounding text
            # Title is typically in the parent element after the abbreviation
            parent = link.parent
            if parent:
                parent_text = parent.text(strip=True)
                # Remove the abbreviation and "PDF" suffix
                title = parent_text.replace(abbreviation, "").replace("PDF", "").strip()
            else:
                title = ""

            # Construct full URL
            url = urljoin(self.base_url + "/", f"{abbrev_lower}/")

            laws.append(
                LawInfo(
                    abbreviation=abbreviation,
                    title=title,
                    url=url,
                )
            )

        return laws

    def _parse_law_index_page(
        self, html_content: str, law_info: LawInfo
    ) -> list[NormInfo]:
        """Parse a law's index page to extract all norm URLs.

        HTML structure observed:
        - Norms listed as links in a table
        - Link text contains norm identifier (§ 1, Art 1, etc.)
        - URL is relative to the law directory

        Example:
        | § 1 | Betriebskosten |
        """
        tree = HTMLParser(html_content)
        norms: list[NormInfo] = []

        # Find all links to norm pages
        # Pattern: links ending in .html within the law directory
        for link in tree.css("a"):
            href = link.attributes.get("href", "")

            # Skip non-norm links
            if not href or not href.endswith(".html"):
                continue

            # Skip navigation and meta links
            if any(
                x in href.lower()
                for x in ["index", "gesamt", "pdf", "xml", "epub", "bjnr"]
            ):
                continue

            norm_text = link.text(strip=True)

            # Skip empty or non-norm links
            if not norm_text:
                continue

            # Norm identifiers start with § or Art or similar
            # Also accept numbered entries like "1", "2" for simple laws
            if not re.match(
                r"^(§|Art\.?|Artikel|Anlage|\d+)", norm_text, re.IGNORECASE
            ):
                continue

            # Construct full URL
            url = urljoin(law_info.url, href)

            norms.append(
                NormInfo(
                    law_abbreviation=law_info.abbreviation,
                    norm_id=norm_text,
                    url=url,
                )
            )

        return norms

    def discover_laws(self) -> Iterator[LawInfo]:
        """Discover all laws from the alphabetical index.

        Yields:
            LawInfo objects for each discovered law
        """
        for page in self.ALPHABET_PAGES:
            url = urljoin(self.base_url + "/", page)
            try:
                html_content = self._fetch_html(url)
                laws = self._parse_letter_page(html_content)
                yield from laws
            except Exception as e:
                # Log error but continue with other pages
                print(f"Error fetching {url}: {e}")
                continue

    def discover_norms(self, law: LawInfo) -> Iterator[NormInfo]:
        """Discover all norms within a specific law.

        Args:
            law: LawInfo object for the law to discover norms from

        Yields:
            NormInfo objects for each discovered norm
        """
        try:
            html_content = self._fetch_html(law.url)
            norms = self._parse_law_index_page(html_content, law)
            yield from norms
        except Exception as e:
            print(f"Error fetching {law.url}: {e}")

    def discover_all(self, max_laws: int | None = None) -> DiscoveryResult:
        """Discover all laws and their norms.

        Args:
            max_laws: Optional limit on number of laws to process (for testing)

        Returns:
            DiscoveryResult with all discovered laws for norms
        """
        result = DiscoveryResult()

        # First, discover all laws
        for law_count, law in enumerate(self.discover_laws(), 1):
            result.laws.append(law)
            if max_laws and law_count >= max_laws:
                break

        # Then, discover norms for each law
        for law in result.laws:
            try:
                for norm in self.discover_norms(law):
                    result.norms.append(norm)
            except Exception as e:
                result.errors.append(
                    f"Error discovering norms for {law.abbreviation}: {e}"
                )

        return result


# NOTE: Async HTTP discovery was removed in favor of using mcp-refcache's
# job/async_timeout feature. For long-running discovery operations:
#
# 1. Use @cache.cached(async_timeout=5.0) decorator on the MCP tool
# 2. The sync discovery runs in background via MemoryTaskBackend
# 3. Agent gets {"status": "processing", "ref_id": "..."} immediately
# 4. Agent polls with get_task_status(ref_id) until complete
# 5. Results are cached and can be retrieved by ref_id
#
# This is cleaner than async HTTP because:
# - No aiohttp dependency
# - Leverages mcp-refcache's job system
# - Results are automatically cached
# - Agent can do other work while discovery runs
#
# Example usage in MCP tool:
#
#   @mcp.tool()
#   @cache.cached(async_timeout=5.0)
#   async def discover_german_laws() -> dict:
#       result = discover_laws_sync()
#       return {"laws": len(result.laws), "norms": len(result.norms)}


def discover_laws_sync(max_laws: int | None = None) -> DiscoveryResult:
    """Discover all German federal laws and their norms.

    This is designed to be used with mcp-refcache's async_timeout feature
    for long-running operations. When called from an MCP tool with
    @cache.cached(async_timeout=5.0), this runs in the background and
    the agent gets a ref_id to poll for completion.

    Args:
        max_laws: Optional limit on number of laws to process (for testing)

    Returns:
        DiscoveryResult with discovered laws and norms

    Example:
        >>> # In MCP tool with mcp-refcache:
        >>> @mcp.tool()
        >>> @cache.cached(async_timeout=5.0)
        >>> async def discover_german_laws():
        ...     result = discover_laws_sync()
        ...     return {"laws": len(result.laws), "norms": len(result.norms)}
    """
    discovery = GermanLawDiscovery()
    return discovery.discover_all(max_laws=max_laws)
