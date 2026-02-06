#!/usr/bin/env python3
"""Test GermanLawHTMLLoader with real law pages."""

from legal_mcp.loaders import GermanLawBulkHTMLLoader, GermanLawHTMLLoader


def test_single_loader() -> None:
    """Test loading a single law norm."""
    print("\n" + "=" * 80)
    print("Test 1: Single Law Loader")
    print("=" * 80 + "\n")

    # Test BGB § 433
    loader = GermanLawHTMLLoader(
        url="https://www.gesetze-im-internet.de/bgb/__433.html",
        law_abbrev="BGB",
    )

    documents = loader.load()

    print(f"Documents created: {len(documents)}")
    print()

    for i, doc in enumerate(documents, 1):
        print(f"--- Document {i} ---")
        print(f"Level:        {doc.metadata['level']}")
        print(f"Doc ID:       {doc.metadata['doc_id']}")
        print(f"Law:          {doc.metadata['law_abbrev']}")
        print(f"Norm ID:      {doc.metadata['norm_id']}")
        print(f"Norm Title:   {doc.metadata['norm_title']}")
        print(f"Content Len:  {len(doc.page_content)} chars")
        print(f"Content:      {doc.page_content[:150]}...")
        print()


def test_multiple_laws() -> None:
    """Test loading multiple law norms."""
    print("\n" + "=" * 80)
    print("Test 2: Bulk Loader (3 laws)")
    print("=" * 80 + "\n")

    urls = [
        ("GG", "https://www.gesetze-im-internet.de/gg/art_1.html"),
        ("BGB", "https://www.gesetze-im-internet.de/bgb/__433.html"),
        ("StGB", "https://www.gesetze-im-internet.de/stgb/__211.html"),
    ]

    loader = GermanLawBulkHTMLLoader(urls)

    # Use lazy loading
    documents = []
    for i, doc in enumerate(loader.lazy_load(), 1):
        documents.append(doc)
        print(
            f"Loaded doc {i}: {doc.metadata['law_abbrev']} {doc.metadata['norm_id']} ({doc.metadata['level']})"
        )

    print(f"\nTotal documents: {len(documents)}")

    # Show summary by law
    print("\nSummary by law:")
    by_law: dict[str, list] = {}
    for doc in documents:
        law = doc.metadata["law_abbrev"]
        if law not in by_law:
            by_law[law] = []
        by_law[law].append(doc)

    for law, docs in by_law.items():
        norm_docs = [d for d in docs if d.metadata["level"] == "norm"]
        para_docs = [d for d in docs if d.metadata["level"] == "paragraph"]
        print(f"  {law}: {len(norm_docs)} norms, {len(para_docs)} paragraphs")


def test_metadata_structure() -> None:
    """Test that metadata structure matches design."""
    print("\n" + "=" * 80)
    print("Test 3: Metadata Structure Validation")
    print("=" * 80 + "\n")

    loader = GermanLawHTMLLoader(
        url="https://www.gesetze-im-internet.de/gg/art_1.html",
        law_abbrev="GG",
    )

    documents = loader.load()
    norm_doc = documents[0]

    print("Norm-level document metadata:")
    required_fields = [
        "jurisdiction",
        "law_abbrev",
        "law_title",
        "norm_id",
        "norm_title",
        "source_url",
        "source_type",
        "level",
        "doc_id",
        "paragraph_count",
    ]

    for field in required_fields:
        value = norm_doc.metadata.get(field, "MISSING")
        print(f"  {field:20s}: {value}")

    # Check paragraph document if exists
    if len(documents) > 1:
        print("\nParagraph-level document metadata:")
        para_doc = documents[1]
        para_fields = ["level", "paragraph_index", "parent_norm_id"]
        for field in para_fields:
            value = para_doc.metadata.get(field, "MISSING")
            print(f"  {field:20s}: {value}")


def test_performance() -> None:
    """Test loading performance."""
    import time

    print("\n" + "=" * 80)
    print("Test 4: Performance Test (10 loads)")
    print("=" * 80 + "\n")

    url = "https://www.gesetze-im-internet.de/bgb/__433.html"
    iterations = 10

    start = time.perf_counter()
    for _ in range(iterations):
        loader = GermanLawHTMLLoader(url=url, law_abbrev="BGB")
        loader.load()
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / iterations) * 1000
    print(f"Total time:    {elapsed:.4f}s")
    print(f"Average:       {avg_ms:.2f}ms per load")
    print(f"Throughput:    {iterations / elapsed:.1f} loads/sec")
    print(f"\nEstimate for 6,871 laws: {(6871 * avg_ms / 1000):.1f}s")


def main() -> None:
    """Run all tests."""
    tests = [
        test_single_loader,
        test_multiple_laws,
        test_metadata_structure,
        test_performance,
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
