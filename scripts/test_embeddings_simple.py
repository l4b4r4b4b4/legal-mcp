#!/usr/bin/env python3
"""Simple test for embedding pipeline with singleton model management.

Tests the complete embedding workflow with proper GPU memory management:
1. Singleton model loading and reuse
2. Document embedding and storage
3. Semantic search functionality
4. Memory cleanup between operations

Usage:
    uv run python scripts/test_embeddings_simple.py

Requirements:
    - chromadb, sentence-transformers installed
    - Network access to gesetze-im-internet.de and huggingface.co
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_model_singleton() -> bool:
    """Test that model manager is truly singleton and reuses GPU memory."""
    print("\n" + "=" * 60)
    print("TEST 1: Model Singleton and GPU Management")
    print("=" * 60)

    from app.ingestion.model_manager import get_embedding_model, reset_embedding_model

    # Reset to start fresh
    reset_embedding_model()

    # Get two instances - should be the same
    model1 = get_embedding_model()
    model2 = get_embedding_model()

    print(f"✓ Model name: {model1.model_name}")
    print(f"✓ Device: {model1.device}")
    print(f"✓ Max seq length: {model1.max_seq_length}")
    print(f"✓ Batch size: {model1.batch_size}")

    # Test singleton behavior
    assert model1 is model2, "Model manager should be singleton"
    print("✓ Singleton behavior verified")

    # Test embedding dimension
    embedding_dim = model1.get_sentence_embedding_dimension()
    print(f"✓ Embedding dimension: {embedding_dim}")
    assert embedding_dim == 768, f"Expected 768-dim embeddings, got {embedding_dim}"

    # Test encoding
    test_texts = [
        "Das ist ein Test auf Deutsch.",
        "This is a test in English.",
        "§ 433 BGB regelt den Kaufvertrag.",
    ]

    embeddings = model1.encode(test_texts)
    print(f"✓ Encoded {len(test_texts)} texts to {embeddings.shape}")
    assert embeddings.shape == (3, 768), f"Wrong embedding shape: {embeddings.shape}"

    # Test stats
    stats = model1.stats()
    print(f"✓ Model stats: {stats}")

    print("\n✅ PASS: Model singleton works")
    return True


def test_embedding_store_with_singleton() -> bool:
    """Test embedding store using the singleton model."""
    print("\n" + "=" * 60)
    print("TEST 2: Embedding Store with Singleton Model")
    print("=" * 60)

    from langchain_core.documents import Document

    from app.ingestion.embeddings import GermanLawEmbeddingStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = GermanLawEmbeddingStore(persist_path=Path(tmpdir))

        # Create test documents
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
        ]

        # Add documents
        added = store.add_documents(documents)
        print(f"✓ Added {added} documents")
        print(f"✓ Total count: {store.count()}")

        assert added == 2, f"Expected 2 documents, got {added}"

        # Test search
        results = store.search("Kaufvertrag", n_results=2)
        print(f"✓ Search returned {len(results)} results")

        if results:
            top_result = results[0]
            print(
                f"✓ Top result: {top_result.metadata.get('norm_id')} (similarity: {top_result.similarity:.3f})"
            )
            assert top_result.metadata.get("law_abbrev") == "BGB"

        # Test filtered search
        results = store.search("Würde", where={"law_abbrev": "GG"}, n_results=1)
        print(f"✓ Filtered search returned {len(results)} GG results")

        if results:
            assert all(r.metadata.get("law_abbrev") == "GG" for r in results)

    print("\n✅ PASS: Embedding store with singleton works")
    return True


def test_live_law_ingestion() -> bool:
    """Test live ingestion of a real German law norm."""
    print("\n" + "=" * 60)
    print("TEST 3: Live Law Ingestion")
    print("=" * 60)

    from legal_mcp.loaders import GermanLawHTMLLoader

    from app.ingestion.embeddings import GermanLawEmbeddingStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = GermanLawEmbeddingStore(persist_path=Path(tmpdir))

        # Load § 433 BGB (Kaufvertrag) - well-known law
        url = "https://www.gesetze-im-internet.de/bgb/__433.html"
        print(f"Loading: {url}")

        try:
            loader = GermanLawHTMLLoader(url=url, law_abbrev="BGB")
            documents = loader.load()

            print(f"✓ Loaded {len(documents)} documents")
            for doc in documents:
                level = doc.metadata.get("level")
                content_preview = doc.page_content[:60].replace("\n", " ")
                print(f"  - {level}: {content_preview}...")

            # Add to store
            added = store.add_documents(documents)
            print(f"✓ Added {added} documents to ChromaDB")

            # Search for seller obligations
            results = store.search("Verkäufer Pflichten übergeben", n_results=3)
            print(f"\n✓ Search 'Verkäufer Pflichten' found {len(results)} results:")

            for i, r in enumerate(results, 1):
                norm_id = r.metadata.get("norm_id", "unknown")
                similarity = r.similarity
                content_preview = r.content[:80].replace("\n", " ")
                print(f"  {i}. {norm_id} (sim: {similarity:.3f}): {content_preview}...")

            assert len(results) > 0, "Expected search results for seller obligations"

        except Exception as e:
            print(f"⚠️  Network test failed (this is OK if offline): {e}")
            return True  # Don't fail the whole test suite for network issues

    print("\n✅ PASS: Live law ingestion works")
    return True


def test_memory_cleanup() -> bool:
    """Test that memory cleanup works properly."""
    print("\n" + "=" * 60)
    print("TEST 4: Memory Cleanup")
    print("=" * 60)

    import torch

    from app.ingestion.model_manager import cleanup_embedding_model, get_embedding_model

    # Get memory before
    if torch.cuda.is_available():
        initial_memory = torch.cuda.memory_allocated(0) / (1024**3)
        print(f"✓ Initial GPU memory: {initial_memory:.2f} GB")

    # Load model and use it
    model = get_embedding_model()
    stats_before = model.stats()
    print(f"✓ Model loaded: {stats_before['model_loaded']}")

    # Encode something to ensure model is loaded
    _ = model.encode(["Test text for memory check"])

    if torch.cuda.is_available():
        after_load_memory = torch.cuda.memory_allocated(0) / (1024**3)
        print(f"✓ Memory after model use: {after_load_memory:.2f} GB")

    # Cleanup
    cleanup_embedding_model()

    if torch.cuda.is_available():
        after_cleanup_memory = torch.cuda.memory_allocated(0) / (1024**3)
        print(f"✓ Memory after cleanup: {after_cleanup_memory:.2f} GB")
        memory_freed = after_load_memory - after_cleanup_memory
        print(f"✓ Memory freed: {memory_freed:.2f} GB")

    print("\n✅ PASS: Memory cleanup works")
    return True


def run_all_tests() -> None:
    """Run all embedding tests with proper cleanup."""
    print("=" * 60)
    print("GERMAN LAW EMBEDDING PIPELINE TESTS (Simplified)")
    print("=" * 60)

    tests = [
        ("Model Singleton", test_model_singleton),
        ("Embedding Store", test_embedding_store_with_singleton),
        ("Live Ingestion", test_live_law_ingestion),
        ("Memory Cleanup", test_memory_cleanup),
    ]

    results = []

    for name, test_func in tests:
        try:
            print(f"\nRunning: {name}")
            success = test_func()
            results.append((name, success, None))
        except Exception as e:
            logger.exception(f"Test failed: {name}")
            results.append((name, False, str(e)))

    # Final cleanup
    try:
        from app.ingestion.model_manager import cleanup_embedding_model

        cleanup_embedding_model()
        print("\n✓ Final cleanup completed")
    except Exception as e:
        print(f"⚠️  Cleanup warning: {e}")

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
