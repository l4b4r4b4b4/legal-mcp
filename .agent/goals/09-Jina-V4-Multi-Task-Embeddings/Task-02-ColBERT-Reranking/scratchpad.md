# Task-02: Add ColBERT Re-Ranking Layer — 🟢 Complete

## Objective

Add a local ColBERT re-ranking layer using `VAGOsolutions/SauerkrautLM-Reason-EuroColBERT` (Apache 2.0) to improve search precision. The reranker scores candidate documents retrieved from ChromaDB using late-interaction (MaxSim) scoring — no new PyPI deps needed.

## Success Criteria

- [x] `ColBERTReranker` class in `app/reranking/colbert_reranker.py` with same interface as `TEIReranker`
- [x] Lazy model loading, GPU/CPU auto-detection, idle timeout cleanup
- [x] MaxSim scoring: backbone → Dense projection → L2 normalize → MaxSim
- [x] Config toggles in `app/config.py` (disabled by default)
- [x] Integration into `pipeline.search_laws()` (sync path) — rerank after ChromaDB retrieval
- [x] Integration into `rag/pipeline.py` (async path) — config-driven choice of TEI vs ColBERT
- [x] Tests with mocked model (no 800MB download in CI) — 65 tests
- [x] Lint clean (ruff check + format), all 297 tests pass (232 existing + 65 new)

## Architecture

### Model Architecture (from HF repo inspection)

```
EuroBERT-210m backbone (768-dim token output)
  → Dense head: Linear(768→128, bias=False), Identity activation
  → L2 normalize per-token
  → MaxSim scoring
```

- Query prefix: `[Q] `, pad/truncate to 256 tokens
- Document prefix: `[D] `, pad/truncate to 2048 tokens
- Skiplist: punctuation token IDs filtered from query embeddings

### MaxSim Algorithm

```
score(query, document) =
  Σ_{i ∈ query_tokens \ skiplist} max_{j ∈ doc_tokens} (q_i · d_j)

where q_i, d_j are L2-normalized 128-dim projected token embeddings
```

### Integration Points

1. **`pipeline.search_laws()`** (sync) — ChromaDB top-N → ColBERT rerank → return top-K
2. **`rag/pipeline.py`** (async) — `reranker` property selects ColBERT or TEI based on config

### Sync/Async Strategy

- `ColBERTReranker._compute_scores()` — sync (CPU/GPU computation)
- `ColBERTReranker.rerank()` — async, wraps sync in `asyncio.to_thread()`
- `ColBERTReranker.rerank_sync()` — sync entry point for `pipeline.search_laws()`

## Files to Create

| File | Purpose |
|------|---------|
| `app/reranking/__init__.py` | Module init, `__all__`, re-exports |
| `app/reranking/colbert_reranker.py` | ColBERTReranker class, singleton, MaxSim |
| `tests/test_colbert_reranker.py` | Tests with mocked model |

## Files to Modify

| File | Changes |
|------|---------|
| `app/config.py` | Add 6 ColBERT settings to `Settings` class |
| `app/ingestion/pipeline.py` | Add optional reranking step in `search_laws()` |
| `app/rag/pipeline.py` | Update `reranker` property for config-driven choice |

## Config Settings (all opt-in, defaults = no behavior change)

```python
colbert_reranking_enabled: bool = False
colbert_reranking_model: str = "VAGOsolutions/SauerkrautLM-Reason-EuroColBERT"
colbert_reranking_top_k: int = 10
colbert_retrieval_candidates: int = 100
colbert_device: str = "auto"  # auto/cpu/cuda
colbert_batch_size: int = 32
```

## Dependencies

- NO new PyPI deps — uses `transformers`, `safetensors`, `torch` (from sentence-transformers)
- `huggingface_hub.hf_hub_download` for Dense head weights
- Model weights (~800MB) downloaded at first use, cached by HF Hub

## Test Strategy

- Mock `transformers.AutoModel.from_pretrained` and `AutoTokenizer.from_pretrained`
- Mock `huggingface_hub.hf_hub_download` and `safetensors.torch.load_file`
- Use deterministic tensor fixtures for MaxSim scoring verification
- Test config toggle, graceful fallback, empty input edge cases
- Test `RerankResult` compatibility with existing RAG pipeline

## Implementation Log

### Files Created
- `app/reranking/__init__.py` — Module init with re-exports (`ColBERTReranker`, `get_colbert_reranker`, `cleanup_colbert_reranker`, `reset_colbert_reranker`)
- `app/reranking/colbert_reranker.py` — 794 lines, full implementation:
  - `ColBERTReranker` class with lazy model loading, idle timeout (5min), GPU/CPU auto-detection
  - `_load_projection_head()` downloads `1_Dense/model.safetensors` via `huggingface_hub.hf_hub_download`
  - `_map_projection_keys()` static method handles various HF state dict key naming conventions
  - `_build_skiplist()` constructs punctuation token ID set for MaxSim filtering
  - `_encode_tokens()` — tokenize → backbone forward → Dense projection → L2 normalize
  - `_compute_maxsim()` — per-query-token max cosine sim over doc tokens, sum (excluding skiplist)
  - `_compute_scores()` — batched document encoding + MaxSim scoring
  - `rerank_sync()` — sync entry point for `pipeline.search_laws()`
  - `rerank()` — async wrapper via `asyncio.to_thread()`
  - `health_check()`, `cleanup()`, `close()`, `stats()` — match TEIReranker interface
  - Singleton: `get_colbert_reranker()`, `cleanup_colbert_reranker()`, `reset_colbert_reranker()`
- `tests/test_colbert_reranker.py` — 65 tests across 12 test classes:
  - `TestColBERTRerankerInit` (3) — config defaults, explicit overrides, stats before loading
  - `TestDeviceSelection` (6) — cpu explicit, cuda explicit, cuda unavailable fallback, auto modes
  - `TestProjectionKeyMapping` (8) — weight/linear.weight/0.weight/dense.weight/projection.weight/shape fallback/errors
  - `TestSkiplistBuilding` (4) — punctuation tokens, pad token, content exclusion, no tokenizer
  - `TestMaxSimScoring` (5) — identical/orthogonal embeddings, skiplist filtering, all-skiplist, multi-doc
  - `TestRerankSync` (5) — empty docs, result types, top_k, return_text=False, model failure
  - `TestRerankAsync` (2) — sync/async parity, empty docs
  - `TestHealthCheck` (2) — success and failure
  - `TestIdleTimeoutAndCleanup` (3) — cleanup, idle timeout reload, async close
  - `TestSingletonLifecycle` (4) — singleton, reset, cleanup-not-reset, args-ignored
  - `TestRerankResultCompatibility` (2) — to_dict, field access
  - `TestConfigToggle` (6) — all 6 config defaults verified
  - `TestPipelineSearchLawsIntegration` (3) — disabled/enabled behavior, graceful fallback
  - `TestRAGPipelineRerankerSelection` (4) — ColBERT selection, TEI selection, fallback, disabled
  - `TestModuleExports` (2) — __all__, direct imports
  - `TestEdgeCases` (6) — single doc, top_k > docs, top_k=None, stats after load, double cleanup, reload after cleanup

### Files Modified
- `app/config.py` — Added 6 ColBERT settings to `Settings` class:
  - `colbert_reranking_enabled: bool = False`
  - `colbert_reranking_model: str = "VAGOsolutions/SauerkrautLM-Reason-EuroColBERT"`
  - `colbert_reranking_top_k: int = 10`
  - `colbert_retrieval_candidates: int = 100`
  - `colbert_device: str = "auto"`
  - `colbert_batch_size: int = 32`
- `app/ingestion/pipeline.py` — Added:
  - Over-retrieval logic in `search_laws()`: when colbert enabled, retrieves `max(n_results, colbert_retrieval_candidates)` candidates
  - `_colbert_rerank_results()` helper: calls reranker, normalizes scores to 0-1 distance, graceful fallback
- `app/rag/pipeline.py` — Updated `reranker` property:
  - Config-driven selection: ColBERT when `colbert_reranking_enabled=True`, TEI otherwise
  - Graceful fallback to TEI if ColBERT init fails
- `tests/test_ingestion_pipeline.py` — Added `colbert_reranking_enabled` and `colbert_retrieval_candidates` to existing `_FakeSettings` stub

### Key Design Decisions
1. **No new PyPI deps** — Uses `transformers`, `safetensors`, `torch` (from sentence-transformers) and `huggingface_hub` (dev dep)
2. **Dual sync/async interface** — `rerank_sync()` for sync `pipeline.search_laws()`, `async rerank()` via `asyncio.to_thread()` for RAG pipeline
3. **Reuses `RerankResult`** from `app.rag.reranker` — no dataclass duplication
4. **Graceful fallback everywhere** — model load failure, reranking failure, all caught and fall back to original ordering
5. **Patching strategy in tests** — `patch.object(torch, "cuda", ...)` for device selection tests (avoids breaking `torch.manual_seed`); deterministic fake backbone using modular arithmetic instead of `torch.manual_seed`

### Test Results
- **297 tests pass** (232 existing + 65 new)
- **Lint clean** (ruff check + ruff format)
- **ColBERT module coverage: 91%** (`app/reranking/colbert_reranker.py`)
- **Overall coverage: 67%** (pre-existing gap from `local_pipeline.py` 0%, `warmup.py` 26%, `embeddings.py` 21%)