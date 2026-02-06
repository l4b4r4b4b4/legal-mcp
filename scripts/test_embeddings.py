#!/usr/bin/env python3
"""Test script for embedding pipeline validation.

Validates the complete embedding workflow:
1. ChromaDB initialization and persistence
2. Embedding model loading (Jina German-English bilingual model)
3. Document ingestion from German law HTML
4. Semantic search with metadata filtering

Usage:
    uv run python scripts/test_embeddings.py

Requirements:
    - chromadb, sentence-transformers installed
    - Network access to gesetze-im-internet.de and huggingface.co

Note:
    Uses jinaai/jina-embeddings-v2-base-de which requires trust_remote_code=True
    and may need to download ~300MB on first run.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

import torch

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def cleanup_gpu_memory() -> None:
    """Clean up GPU memory between tests to avoid OOM."""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def test_embedding_store_init() -> bool:
    """Test ChromaDB and model initialization."""
    print("\n" + "=" * 60)
    print("TEST 1: Embedding Store Initialization")
    print("=" * 60)

    from app.ingestion.embeddings import GermanLawEmbeddingStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = GermanLawEmbeddingStore(persist_path=Path(tmpdir))

        # Test model loading
        print(f"✓ Model: {store.model_name}")
        embedding_dim = store.model.get_sentence_embedding_dimension()
        max_seq_len = store.model.max_seq_length
        print(f"✓ Embedding dimension: {embedding_dim}")
        print(f"✓ Max sequence length: {max_seq_len}")

        # Verify expected dimensions for Jina model
        assert embedding_dim == 768, f"Expected 768-dim embeddings, got {embedding_dim}"

        # Test collection creation
        print(f"✓ Collection: {store.collection_name}")
        print(f"✓ Document count: {store.count()}")

        # Test stats
        stats = store.stats()
        print(f"✓ Stats: {stats}")

    print("\n✅ PASS: Embedding store initialization works")
    cleanup_gpu_memory()
    return True


def test_document_embedding() -> bool:
    """Test embedding LangChain documents."""
    print("\n" + "=" * 60)
    print("TEST 2: Document Embedding")
    print("=" * 60)

    from langchain_core.documents import Document

    from app.ingestion.embeddings import GermanLawEmbeddingStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = GermanLawEmbeddingStore(persist_path=Path(tmpdir))

        # Create test documents
        documents = [
            Document(
                page_content="Durch den Kaufvertrag wird der Verkäufer einer Sache verpflichtet, dem Käufer die Sache zu übergeben und das Eigentum an der Sache zu verschaffen.",
                metadata={
                    "doc_id": "bgb_para_433",
                    "jurisdiction": "de-federal",
                    "level": "norm",
                    "law_abbrev": "BGB",
                    "law_title": "Bürgerliches Gesetzbuch",
                    "norm_id": "§ 433",
                    "norm_title": "Vertragstypische Pflichten beim Kaufvertrag",
                    "source_url": "https://www.gesetze-im-internet.de/bgb/__433.html",
                },
            ),
            Document(
                page_content="Der Käufer ist verpflichtet, dem Verkäufer den vereinbarten Kaufpreis zu zahlen und die gekaufte Sache abzunehmen.",
                metadata={
                    "doc_id": "bgb_para_433_abs_2",
                    "jurisdiction": "de-federal",
                    "level": "paragraph",
                    "law_abbrev": "BGB",
                    "law_title": "Bürgerliches Gesetzbuch",
                    "norm_id": "§ 433",
                    "paragraph_index": 2,
                    "parent_norm_id": "bgb_para_433",
                    "source_url": "https://www.gesetze-im-internet.de/bgb/__433.html",
                },
            ),
            Document(
                page_content="Die Würde des Menschen ist unantastbar. Sie zu achten und zu schützen ist Verpflichtung aller staatlichen Gewalt.",
                metadata={
                    "doc_id": "gg_art_1",
                    "jurisdiction": "de-federal",
                    "level": "norm",
                    "law_abbrev": "GG",
                    "law_title": "Grundgesetz für die Bundesrepublik Deutschland",
                    "norm_id": "Art 1",
                    "norm_title": "Menschenwürde",
                    "source_url": "https://www.gesetze-im-internet.de/gg/art_1.html",
                },
            ),
        ]

        # Add documents
        added = store.add_documents(documents)
        print(f"✓ Added {added} documents")
        print(f"✓ Total count: {store.count()}")

        assert added == 3, f"Expected 3 documents, got {added}"
        assert store.count() == 3, f"Expected count 3, got {store.count()}"

    print("\n✅ PASS: Document embedding works")
    cleanup_gpu_memory()
    return True


def test_semantic_search() -> bool:
    """Test semantic search functionality."""
    print("\n" + "=" * 60)
    print("TEST 3: Semantic Search")
    print("=" * 60)

    from langchain_core.documents import Document

    from app.ingestion.embeddings import GermanLawEmbeddingStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = GermanLawEmbeddingStore(persist_path=Path(tmpdir))

        # Add test documents
        documents = [
            Document(
                page_content="Durch den Kaufvertrag wird der Verkäufer verpflichtet, die Sache zu übergeben.",
                metadata={
                    "doc_id": "bgb_433",
                    "law_abbrev": "BGB",
                    "norm_id": "§ 433",
                    "level": "norm",
                },
            ),
            Document(
                page_content="Die Würde des Menschen ist unantastbar.",
                metadata={
                    "doc_id": "gg_art1",
                    "law_abbrev": "GG",
                    "norm_id": "Art 1",
                    "level": "norm",
                },
            ),
            Document(
                page_content="Mord ist die Tötung eines Menschen aus niedrigen Beweggründen.",
                metadata={
                    "doc_id": "stgb_211",
                    "law_abbrev": "StGB",
                    "norm_id": "§ 211",
                    "level": "norm",
                },
            ),
        ]

        store.add_documents(documents)

        # Test 1: General search
        print("\nQuery: 'Kaufvertrag Pflichten'")
        results = store.search("Kaufvertrag Pflichten", n_results=3)
        for r in results:
            print(
                f"  - {r.metadata.get('law_abbrev')} {r.metadata.get('norm_id')}: {r.similarity:.3f}"
            )

        assert len(results) > 0, "Expected search results"
        assert (
            results[0].metadata.get("law_abbrev") == "BGB"
        ), "Expected BGB as top result"
        print("✓ General search returns relevant results")

        # Test 2: Search with filter
        print("\nQuery: 'Würde Mensch' (filter: GG only)")
        results = store.search("Würde Mensch", where={"law_abbrev": "GG"}, n_results=3)
        for r in results:
            print(
                f"  - {r.metadata.get('law_abbrev')} {r.metadata.get('norm_id')}: {r.similarity:.3f}"
            )

        assert all(
            r.metadata.get("law_abbrev") == "GG" for r in results
        ), "Filter not applied"
        print("✓ Filtered search respects metadata constraints")

        # Test 3: Get by ID
        print("\nGet by ID: 'bgb_433'")
        result = store.get_by_id("bgb_433")
        if result:
            print(f"  - Found: {result.metadata.get('norm_id')}")
        assert result is not None, "Expected to find document by ID"
        assert result.metadata.get("norm_id") == "§ 433", "Wrong document returned"
        print("✓ Get by ID works")

    print("\n✅ PASS: Semantic search works")
    cleanup_gpu_memory()
    return True


def test_live_ingestion() -> bool:
    """Test live ingestion from gesetze-im-internet.de."""
    print("\n" + "=" * 60)
    print("TEST 4: Live Ingestion (Network Required)")
    print("=" * 60)

    from legal_mcp.loaders import GermanLawHTMLLoader

    from app.ingestion.embeddings import GermanLawEmbeddingStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = GermanLawEmbeddingStore(persist_path=Path(tmpdir))

        # Load a real norm (§ 433 BGB - Kaufvertrag)
        url = "https://www.gesetze-im-internet.de/bgb/__433.html"
        print(f"Loading: {url}")

        loader = GermanLawHTMLLoader(url=url, law_abbrev="BGB")
        documents = loader.load()

        print(f"✓ Loaded {len(documents)} documents")
        for doc in documents:
            level = doc.metadata.get("level")
            print(f"  - {level}: {doc.page_content[:60]}...")

        # Add to store
        added = store.add_documents(documents)
        print(f"✓ Added {added} documents to ChromaDB")

        # Search
        results = store.search("Verkäufer Pflichten", n_results=3)
        print("\nSearch: 'Verkäufer Pflichten'")
        for r in results:
            print(f"  - {r.metadata.get('norm_id')}: {r.similarity:.3f}")
            print(f"    {r.content[:80]}...")

        assert len(results) > 0, "Expected search results"

    print("\n✅ PASS: Live ingestion works")
    cleanup_gpu_memory()
    return True


def test_ingestion_pipeline() -> bool:
    """Test the full ingestion pipeline with a small sample."""
    print("\n" + "=" * 60)
    print("TEST 5: Full Ingestion Pipeline (2 laws)")
    print("=" * 60)

    import time

    from app.ingestion.pipeline import ingest_german_laws, search_laws

    with tempfile.TemporaryDirectory() as tmpdir:
        start = time.time()

        # Ingest just 2 laws for testing
        result = ingest_german_laws(
            max_laws=2,
            max_norms_per_law=3,  # Limit norms per law for speed
            persist_path=tmpdir,
        )

        elapsed = time.time() - start
        print(f"\nIngestion completed in {elapsed:.1f} seconds")
        print(f"  - Laws processed: {result.laws_processed}")
        print(f"  - Norms processed: {result.norms_processed}")
        print(f"  - Documents added: {result.documents_added}")
        print(f"  - Errors: {len(result.errors)}")

        if result.errors:
            print("  - First error:", result.errors[0])

        assert result.documents_added > 0, "Expected some documents to be added"

        # Test search on ingested data
        print("\nSearch on ingested data: 'Gesetz Regelung'")
        results = search_laws("Gesetz Regelung", n_results=3, persist_path=tmpdir)
        for r in results:
            print(
                f"  - {r.get('law_abbrev')} {r.get('norm_id')}: {r.get('similarity'):.3f}"
            )

    print("\n✅ PASS: Ingestion pipeline works")
    return True


def run_all_tests() -> None:
    """Run all embedding tests."""
    print("\n" + "=" * 60)
    print("GERMAN LAW EMBEDDING PIPELINE TESTS")
    print("=" * 60)

    tests = [
        ("Embedding Store Init", test_embedding_store_init),
        ("Document Embedding", test_document_embedding),
        ("Semantic Search", test_semantic_search),
        ("Live Ingestion", test_live_ingestion),
        ("Full Pipeline", test_ingestion_pipeline),
    ]

    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success, None))
        except Exception as e:
            logger.exception(f"Test failed: {name}")
            results.append((name, False, str(e)))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, success, error in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {name}")
        if error:
            print(f"       Error: {error}")
        if success:
            passed += 1
        else:
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
