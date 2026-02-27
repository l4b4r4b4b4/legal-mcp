# Task-06: Docker + Local Testing

## Status: ⚪ Not Started → 🟡 Planning

---

## Objective

Validate the full Legal-MCP stack (TEI embeddings, ColBERT reranker, ChromaDB, JSONL import) works end-to-end in both Docker and local modes, and close the test coverage gap from 67.72% → ≥73%.

## Success Criteria

- [ ] All 305+ tests pass
- [ ] Test coverage ≥73% (currently 67.72%)
- [ ] `docker compose build` succeeds for production + dev images
- [ ] `docker compose up` → health endpoint returns 200
- [ ] `docker compose -f docker-compose.gpu.yml up chromadb tei-embeddings` → healthy
- [ ] `uv run legal-mcp streamable-http` starts locally without error
- [ ] Lint clean (`ruff check . --fix --unsafe-fixes && ruff format .`)

---

## Analysis: Coverage Gap

### Current State
- **305 tests pass**, 67.72% coverage (fail-under: 73%)
- Total: 3412 stmts + 820 branches = 4232 coverage items
- Need ~5.3 percentage points = ~225 more items covered

### Biggest Gaps (Sorted by Impact)

| File | Stmts | Miss | Branches | Cover | Missing Stmts |
|------|-------|------|----------|-------|---------------|
| `app/ingestion/local_pipeline.py` | 208 | 208 | 60 | **0%** | ALL |
| `app/warmup.py` | 191 | 135 | 32 | **26%** | 135 |
| `app/ingestion/embeddings.py` | 155 | 111 | 52 | **21%** | 111 |
| `app/custom_documents/embeddings.py` | 164 | 101 | 58 | **30%** | 101 |
| `app/custom_documents/conversion/markitdown_converter.py` | 70 | 32 | 26 | **52%** | 32 |
| `app/rag/reranker.py` | 80 | 27 | 14 | **64%** | 27 |
| `app/tools/cache.py` | 27 | 8 | 4 | **61%** | 8 |
| `app/tools/custom_documents.py` | 234 | 71 | 26 | **68%** | 71 |
| `app/tracing.py` | 250 | 73 | 38 | **72%** | 73 |

### Coverage Strategy (Target: +5.3% → 73%)

**Phase 1: `local_pipeline.py` (0% → ~55%) → ~+3.2%**
- `discover_local_laws()` — filesystem walk, easy to test with tmp_path
- `parse_local_html_file()` — HTML parsing, test with fixture HTML
- `get_corpus_status()` — ChromaDB query, mock the client
- `get_ingested_law_abbreviations()` — ChromaDB metadata query, mock
- `_parse_law_directory()` — internal helper, covered via integration
- Skip `ingest_from_local_html()` and `_embed_and_store()` (complex integration, mock-heavy)

**Phase 2: `warmup.py` (26% → ~50%) → ~+1.3%**
- `WarmupState` enum — trivial
- `get_warmup_status()` — returns state dict, no side effects
- `is_corpus_ready()` — simple bool check
- `_warmup_worker()` — mock ChromaDB/local_pipeline, test state transitions
- `_wait_for_chromadb()` — mock with retry logic

**Phase 3: `ingestion/embeddings.py` (21% → ~45%) → ~+1.0%**
- `SearchResult` dataclass + `.similarity` property
- `CorpusStats` dataclass
- `GermanLawEmbeddingStore.__init__()` — mock ChromaDB client
- `GermanLawEmbeddingStore.search()` — mock collection.query()
- `GermanLawEmbeddingStore.get_stats()` — mock collection.count()

**Total estimated gain: +5.5% → ~73.2%** (with margin)

---

## Implementation Plan

### 1. Test Files to Create

| Test File | Tests Target | Est. Tests |
|-----------|-------------|------------|
| `tests/test_local_pipeline.py` | `app/ingestion/local_pipeline.py` | 12-15 |
| `tests/test_warmup.py` | `app/warmup.py` | 8-10 |
| `tests/test_embedding_store.py` | `app/ingestion/embeddings.py` | 8-10 |

### 2. Test Details

#### `tests/test_local_pipeline.py` (NEW)
- `test_discover_local_laws_finds_directories` — tmp_path with mock law dirs
- `test_discover_local_laws_skips_non_directories` — files in root ignored
- `test_discover_local_laws_skips_empty_directories` — dirs with no HTML
- `test_discover_local_laws_raises_on_missing_root` — FileNotFoundError
- `test_discover_local_laws_skips_index_and_gesamt` — SKIP_PATTERNS
- `test_discover_local_laws_skips_bjnr_substrings` — SKIP_SUBSTRINGS
- `test_parse_local_html_file_basic` — real HTML fixture → Document list
- `test_parse_local_html_file_empty` — empty/minimal HTML → empty list
- `test_parse_local_html_file_metadata` — verify metadata fields set correctly
- `test_get_corpus_status_with_mock_chroma` — mock client, verify response shape
- `test_get_corpus_status_error_handling` — ChromaDB unreachable → error key
- `test_get_ingested_law_abbreviations` — mock collection.get() → set of abbreviations
- `test_parse_law_directory` — via discover + parse integration

#### `tests/test_warmup.py` (NEW)
- `test_warmup_state_enum_values` — all states exist
- `test_get_warmup_status_initial` — returns idle/not-started state
- `test_is_corpus_ready_false_initially` — no warmup yet
- `test_is_corpus_ready_true_after_complete` — mock completed state
- `test_start_background_warmup_disabled` — WARMUP_ON_STARTUP=false → no-op
- `test_get_warmup_status_shape` — verify all expected keys present
- `test_warmup_state_transitions` — idle → running → complete
- `test_wait_for_chromadb_success` — mock immediate success
- `test_wait_for_chromadb_timeout` — mock persistent failure → None

#### `tests/test_embedding_store.py` (NEW)
- `test_search_result_similarity_property` — distance → similarity conversion
- `test_search_result_similarity_clamped` — negative distances clamped to 0
- `test_corpus_stats_dataclass` — construction and field access
- `test_embedding_store_init_http_client` — CHROMA_HOST set → HttpClient
- `test_embedding_store_init_persistent_client` — no CHROMA_HOST → PersistentClient
- `test_embedding_store_search_returns_results` — mock collection.query()
- `test_embedding_store_search_empty` — no results → empty list
- `test_embedding_store_get_stats` — mock collection.count() → CorpusStats

### 3. Docker Validation (Manual)

- [ ] `docker compose build legal-mcp` — verify Dockerfile builds
- [ ] `docker compose up chromadb` — verify ChromaDB healthy on :9720
- [ ] `docker compose up legal-mcp` — verify health endpoint on :9685
- [ ] `docker compose -f docker-compose.gpu.yml up chromadb tei-embeddings` — GPU stack
- [ ] Verify `CHROMA_HOST=chromadb` connectivity between containers
- [ ] Verify volume mount `chroma_data:/data` persists across restarts

### 4. Dockerfile Review

Current Dockerfiles look correct. Key checks:
- `docker/Dockerfile.base`: Multi-stage, non-root user, git for mcp-refcache ✅
- `docker/Dockerfile`: Copies `app/` and `data/html/` ✅
- `docker/Dockerfile.dev`: Standalone slim image with hot reload ✅
- **No changes needed** unless Docker build fails

### 5. Compose File Review

- `docker-compose.yml`: ChromaDB (:9720), legal-mcp (:9685), dev profile. Volume mount `chroma_data:/data` ✅
- `docker-compose.gpu.yml`: ChromaDB, TEI embeddings (:9721), TEI reranker (:9722), vLLM (:9723). Volume mount `chroma_data:/data` ✅
- CPU compose connects to external TEI via `tei_network` (docproc-platform) — correct design
- **Potential improvement**: Add `COLBERT_RERANKING_ENABLED` env var to compose services

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| `local_pipeline.py` tests need real HTML fixtures | Create minimal fixture HTML in `tests/fixtures/` |
| ChromaDB mock complexity | Use `unittest.mock.patch` on chromadb client constructor |
| Warmup module uses threading | Test state functions directly, mock thread internals |
| Coverage math is approximate | Build in ~0.5% margin, measure after Phase 1 |

---

## Execution Order

1. Create HTML test fixtures for `local_pipeline.py` tests
2. Write `tests/test_local_pipeline.py` → run coverage → check gap
3. Write `tests/test_warmup.py` → run coverage → check gap
4. Write `tests/test_embedding_store.py` → run coverage → verify ≥73%
5. Run `ruff check . --fix --unsafe-fixes && ruff format .`
6. Docker build + compose up validation (manual)
7. Update scratchpad with results
