#!/usr/bin/env python3
"""Test HTML-only discovery pipeline for German laws."""

import time

from legal_mcp.loaders import (
    GermanLawDiscovery,
    LawInfo,
)


def test_discover_laws_from_one_letter() -> None:
    """Test discovering laws from a single letter page."""
    print("\n" + "=" * 80)
    print("Test 1: Discover Laws from Letter 'B'")
    print("=" * 80 + "\n")

    discovery = GermanLawDiscovery()

    # Fetch just the B page
    url = "https://www.gesetze-im-internet.de/Teilliste_B.html"
    html_content = discovery._fetch_html(url)
    laws = discovery._parse_letter_page(html_content)

    print(f"Found {len(laws)} laws starting with 'B'")
    print()

    # Show first 10 laws
    print("First 10 laws:")
    for i, law in enumerate(laws[:10], 1):
        print(f"  {i:2}. {law.abbreviation:20s} - {law.title[:50]}...")

    # Check for known laws
    known_laws = ["BGB", "BBG", "BDSG", "BetrVG"]
    found = [law for law in laws if law.abbreviation in known_laws]
    print(f"\nKnown laws found: {[found_law.abbreviation for found_law in found]}")


def test_discover_norms_from_law() -> None:
    """Test discovering norms from a specific law."""
    print("\n" + "=" * 80)
    print("Test 2: Discover Norms from BetrKV (small law)")
    print("=" * 80 + "\n")

    discovery = GermanLawDiscovery()

    # Create a LawInfo for BetrKV
    law = LawInfo(
        abbreviation="BetrKV",
        title="Verordnung über die Aufstellung von Betriebskosten",
        url="https://www.gesetze-im-internet.de/betrkv/",
    )

    # Discover norms
    norms = list(discovery.discover_norms(law))

    print(f"Found {len(norms)} norms in {law.abbreviation}")
    print()

    for norm in norms:
        print(f"  - {norm.norm_id:20s} → {norm.url}")


def test_discover_norms_from_gg() -> None:
    """Test discovering norms from Grundgesetz."""
    print("\n" + "=" * 80)
    print("Test 3: Discover Norms from GG (Grundgesetz)")
    print("=" * 80 + "\n")

    discovery = GermanLawDiscovery()

    law = LawInfo(
        abbreviation="GG",
        title="Grundgesetz für die Bundesrepublik Deutschland",
        url="https://www.gesetze-im-internet.de/gg/",
    )

    norms = list(discovery.discover_norms(law))

    print(f"Found {len(norms)} norms in {law.abbreviation}")
    print()

    # Show first 10 and last 5
    print("First 10 norms:")
    for norm in norms[:10]:
        print(f"  - {norm.norm_id}")

    if len(norms) > 15:
        print(f"\n... ({len(norms) - 15} more) ...\n")
        print("Last 5 norms:")
        for norm in norms[-5:]:
            print(f"  - {norm.norm_id}")


def test_full_discovery_limited() -> None:
    """Test full discovery with limit."""
    print("\n" + "=" * 80)
    print("Test 4: Full Discovery (limited to 5 laws)")
    print("=" * 80 + "\n")

    discovery = GermanLawDiscovery()

    start = time.perf_counter()
    result = discovery.discover_all(max_laws=5)
    elapsed = time.perf_counter() - start

    print(f"Discovery completed in {elapsed:.2f}s")
    print(f"Laws found: {len(result.laws)}")
    print(f"Norms found: {len(result.norms)}")
    print(f"Errors: {len(result.errors)}")
    print()

    print("Laws discovered:")
    for law in result.laws:
        norm_count = len(
            [n for n in result.norms if n.law_abbreviation == law.abbreviation]
        )
        print(f"  - {law.abbreviation:15s}: {norm_count:3} norms")

    if result.errors:
        print("\nErrors:")
        for error in result.errors[:5]:
            print(f"  - {error}")


def test_estimate_full_corpus() -> None:
    """Estimate time for full corpus discovery."""
    print("\n" + "=" * 80)
    print("Test 5: Estimate Full Corpus Discovery Time")
    print("=" * 80 + "\n")

    discovery = GermanLawDiscovery()

    # Time fetching one letter page
    start = time.perf_counter()
    url = "https://www.gesetze-im-internet.de/Teilliste_A.html"
    html_content = discovery._fetch_html(url)
    laws = discovery._parse_letter_page(html_content)
    letter_time = time.perf_counter() - start

    print(f"Letter page fetch+parse: {letter_time * 1000:.1f}ms")
    print(f"Laws on 'A' page: {len(laws)}")

    # Time fetching one law index
    if laws:
        start = time.perf_counter()
        norms = list(discovery.discover_norms(laws[0]))
        law_time = time.perf_counter() - start
        print(f"Law index fetch+parse: {law_time * 1000:.1f}ms")
        print(f"Norms in {laws[0].abbreviation}: {len(norms)}")

    # Estimate for full corpus
    estimated_laws = 6871
    estimated_letter_pages = 35

    # Sync estimate
    sync_time = (estimated_letter_pages * letter_time) + (estimated_laws * law_time)
    print(f"\nEstimated sync discovery time: {sync_time / 60:.1f} minutes")

    # Async estimate (10 concurrent)
    async_factor = 10
    async_time = sync_time / async_factor
    print(
        f"Estimated async discovery time (10 concurrent): {async_time / 60:.1f} minutes"
    )


def main() -> None:
    """Run all tests."""
    tests = [
        test_discover_laws_from_one_letter,
        test_discover_norms_from_law,
        test_discover_norms_from_gg,
        test_full_discovery_limited,
        test_estimate_full_corpus,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"\n❌ Test failed: {test.__name__}")
            print(f"Error: {e}")
            import traceback

            traceback.print_exc()

    print("\n" + "=" * 80)
    print("✅ All tests completed!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
