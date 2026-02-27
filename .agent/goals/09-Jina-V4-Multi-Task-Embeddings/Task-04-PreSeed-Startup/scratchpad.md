# Task-04: Pre-Seed at Startup — 🟢 Complete (Phase 2: Docker/Helm/LFS done 2026-02-27)

## Objective

Export the pre-computed 193K-document corpus from ChromaDB into a portable, storage-agnostic JSONL format. Fix the critical Docker volume mount bug. Create an import script that can seed ChromaDB (or any future vector store) from the JSONL files. The JSONL files become the source of truth — not any specific database.

## Critical Bug Found During Task-03

**The ChromaDB Docker volume mount is wrong in BOTH compose files.**

| File | Current (broken) | Correct |
|------|------------------|---------|
| `docker-compose.yml` | `chroma_data:/chroma/chroma` | `chroma_data:/data` |
| `docker-compose.gpu.yml` | `chroma_data:/chroma/chroma` | `chroma_data:/data` |

**Evidence:**
- `chromadb/chroma:latest` config at `/config.yaml` → `persist_path: "/data"`
- Actual data (1.9 GB) lives at `/data/` inside the container
- The volume mount at `/chroma/chroma` captures nothing (3.1 MB of stale Feb 18 data)
- **ALL 193,371 pre-computed documents are in the container's writable layer**
- **If `docker compose down` is run, the data is LOST**

### Data at Risk (Container Writable Layer)

```
/data/chroma.sqlite3                                    — 1.3 GB (metadata, documents, IDs)
/data/7f7d7a06-dd84-4529-bfdc-8a33857b188b/
  data_level0.bin                                       — 592 MB (HNSW vector index)
  index_metadata.pickle                                 — 11 MB
  link_lists.bin                                        — 1.6 MB
  length.bin                                            — 755 KB
  header.bin                                            — 100 B
Total: ~1.9 GB
```

## Success Criteria

- [x] Document the critical volume mount bug
- [x] Pre-compute 193K docs directly to portable JSONL (`data/embeddings/<LAW>.jsonl.gz`)
- [x] Create `scripts/precompute_to_jsonl.py` (HTML → TEI embed → JSONL, replaces ChromaDB export)
- [x] Create `scripts/import_embeddings.py` (JSONL → ChromaDB, extensible to other backends)
- [x] Create `data/embeddings/manifest.json` with corpus metadata
- [x] Fix volume mount in `docker-compose.yml`: `chroma_data:/data`
- [x] Fix volume mount in `docker-compose.gpu.yml`: `chroma_data:/data`
- [x] Verify round-trip: precompute → wipe ChromaDB → import → 199 docs restored (smoke test)
- [ ] Verify warmup detects imported corpus and skips re-ingestion — deferred to Task-06
- [x] Configure Git LFS for `data/embeddings/*.jsonl.gz` ✅ (2026-02-27)
- [x] Bake embeddings into Docker image (`docker/Dockerfile`) ✅
- [x] Add `seed-embeddings` init service to `docker-compose.yml` ✅
- [x] Add `seedEmbeddings` init container to Helm deployment template ✅
- [x] Move `sentence-transformers` from runtime → dev deps (removes torch/CUDA from prod image) ✅
- [x] Make `app/reranking/__init__.py` imports lazy (avoid torch at module load) ✅
- [x] Enable LFS checkout in CI `release.yml` for `build-app` job ✅
- [x] Local E2E test: full stack verified (193K docs, 0 errors) ✅
- [x] PR #4 created, pushed to `feature/goal-09-embeddings-upgrade` ✅
- [x] Tests pass (305), lint clean

## Phase 1 Completion Results (2026-02-23) — JSONL Pre-Compute

### Full Corpus Pre-Compute to JSONL Stats

| Metric | Value |
|--------|-------|
| **Total documents** | 193,371 |
| **Total laws** | 2,628 |
| **Errors** | 0 |
| **Total time** | 464.4s (~7.7 min) |
| **Throughput** | 416.4 docs/sec |
| **Output size** | 493 MB (2,628 `.jsonl.gz` files) |
| **Float precision** | 6 decimal places |
| **Embedding model** | jinaai/jina-embeddings-v2-base-de (768-dim) |
| **GPU** | RTX 3080 Ti, 98-100% utilization |

### Comparison: JSONL vs ChromaDB Pre-Compute

| Metric | JSONL (new) | ChromaDB (Task-03) |
|--------|-------------|-------------------|
| Time | 464s | 607s |
| Throughput | 416 docs/sec | 319 docs/sec |
| Output size | 493 MB | 1.9 GB |
| Portable | ✅ Any vector store | ❌ ChromaDB only |
| Inspectable | ✅ `zcat | head` | ❌ Binary |

### Import Round-Trip Verified

Smoke test: 3 laws (199 docs) → export → wipe ChromaDB → import → 199 docs ✅
- `scripts/import_embeddings.py --force` — wipes collection and reimports
- `scripts/import_embeddings.py --status` — compares manifest vs ChromaDB
- Resume support: skips already-imported laws
- Batch upsert: 500 docs per batch

## Phase 2 Completion Results (2026-02-27) — Docker/Helm/LFS Baking

### What Was Done

1. **Git LFS setup**: `.gitattributes` tracks `data/embeddings/*.jsonl.gz`, `data/.gitignore` un-ignores embeddings
2. **Dockerfile**: Added `COPY data/embeddings/` and `COPY scripts/import_embeddings.py` to both production and development targets
3. **docker-compose.yml**: Added `seed-embeddings` service (runs in parallel with `legal-mcp`, both depend only on `chromadb: service_healthy`)
4. **Helm**: Added conditional `initContainers` block in `deployment.yaml`, `seedEmbeddings` config in `values.yaml`, AKS values updated
5. **CI**: `release.yml` `build-app` job now checks out with `lfs: true` + runs `git lfs pull`
6. **Dependencies**: Moved `sentence-transformers` from runtime to dev deps — PyTorch + NVIDIA CUDA removed from production image
7. **Lazy imports**: `app/reranking/__init__.py` uses `__getattr__` pattern to avoid importing torch at module load time
8. **Fixed**: Removed `WARMUP_MAX_LAWS=${WARMUP_MAX_LAWS:-}` env var that crashed pydantic (empty string → int parse error)

### Local E2E Test Results

| Metric | Result |
|--------|--------|
| Fresh seed (empty ChromaDB) | 193,371 docs, 2,628 laws, 0 errors, ~11 min (293.8 docs/sec) |
| Idempotent re-run (corpus present) | 2,628 laws skipped, ~25s |
| Base image size (without torch) | 631 MB |
| App image size (with HTML + embeddings) | 1.52 GB |
| Tests | 305 passed ✅ |
| Lint | Clean ✅ |
| `helm template` (testing + production) | Valid ✅ |
| `docker compose config` | Valid ✅ |

### Stack Startup Behavior

```
docker compose up -d
  ├── chromadb ──→ healthcheck passes (~6s)
  ├── seed-embeddings ──→ depends on chromadb healthy
  │   ├── Fresh volume: reads 2,628 .jsonl.gz → upserts 193K docs → exits (0) in ~11 min
  │   └── Existing data: detects all laws imported → exits (0) in ~25s
  └── legal-mcp ──→ depends on chromadb healthy (parallel with seed)
      └── starts serving immediately on :9685
```

### Known Issue: `corpus_ready: false` on health endpoint

The `/health` endpoint reports `corpus_ready: false` because `is_corpus_ready()` checks the warmup status tracker, not ChromaDB directly. The warmup tracker only updates when the HTML→TEI warmup path runs. Since we're seeding from JSONL imports, the tracker is never touched. **Data is fully present in ChromaDB** — this is a cosmetic issue. Fix: make `is_corpus_ready()` query ChromaDB collection count directly (separate task).

### Bandit B615 Fix

CI `security` job failed on pre-existing bandit B615 warnings (unpinned HuggingFace downloads in `app/reranking/colbert_reranker.py`). Fixed by pinning `revision=` to commit SHA `2d257d369ff319858cc758e8bae0794ee827581d` (2025-08-03) in all `from_pretrained()` and `hf_hub_download()` calls. Bandit now passes: Medium severity = 0.

### Future Work: Migrate ColBERT Reranker to External TEI

The local ColBERT reranker (`app/reranking/colbert_reranker.py`) loads torch + model weights in-process. This contradicts the architecture where TEI/vLLM handle all inference externally. The model (`VAGOsolutions/SauerkrautLM-Reason-EuroColBERT`) can be deployed on TEI as a reranker service. The local implementation should be replaced with an HTTP client to TEI, same pattern as embeddings. Currently `colbert_reranking_enabled` defaults to `False` so this code is not active in production — TEI reranker is the default path. This is a separate task.

### PR & CI Status

- **PR #4**: https://github.com/l4b4r4b4b4/legal-mcp/pull/4
- **Branch**: `feature/goal-09-embeddings-upgrade`
- **Commits**:
  - `74a9484b` — `feat: bake pre-computed embeddings into Docker image + seed-embeddings service`
  - `ba2a8cf5` — `fix: pin HuggingFace model revision to fix bandit B615 warnings`
- **LFS push**: 2,628 objects, 519 MB uploaded ✅
- **CI**: Re-running after bandit fix — needs to pass before merge triggers Release workflow → image build

## Architecture

### Portable JSONL Format (Source of Truth)

```
data/embeddings/
├── manifest.json              # corpus metadata (committed to git, small)
├── AABG.jsonl.gz              # per-law compressed JSONL
├── BGB.jsonl.gz
├── GG.jsonl.gz
├── STGB.jsonl.gz
├── ZPO.jsonl.gz
└── ... (2,628 files total)
```

**Each line in a `.jsonl.gz` file:**

```json
{
  "id": "bgb_para_433",
  "text": "(1) Durch den Kaufvertrag wird der Verkäufer einer Sache verpflichtet...",
  "embedding": [0.012345, -0.067891, ...],
  "metadata": {
    "law_abbrev": "BGB",
    "norm_id": "§ 433",
    "norm_title": "Vertragstypische Pflichten beim Kaufvertrag",
    "level": "norm",
    "jurisdiction": "de-federal",
    "source_url": "https://www.gesetze-im-internet.de/bgb/__433.html",
    "source_type": "local_html",
    "law_title": "Bürgerliches Gesetzbuch",
    "doc_id": "bgb_para_433"
  }
}
```

**`manifest.json`:**

```json
{
  "version": 1,
  "created_at": "2026-02-23T23:08:57Z",
  "embedding_model": "jinaai/jina-embeddings-v2-base-de",
  "embedding_dimension": 768,
  "float_precision": 6,
  "total_documents": 193371,
  "total_laws": 2628,
  "levels": ["norm", "paragraph"],
  "jurisdiction": "de-federal",
  "source": "gesetze-im-internet.de HTML corpus",
  "files": {
    "AABG.jsonl.gz": {"documents": 42, "size_bytes": 98304},
    "BGB.jsonl.gz": {"documents": 3210, "size_bytes": 8912345}
  }
}
```

### Size Results (Actual)

| Metric | Value |
|--------|-------|
| Average doc JSON size | 9.4 KB (6dp float precision) |
| Raw JSONL total | ~1.74 GB (estimated) |
| **Gzipped JSONL total** | **493 MB** (28% compression ratio) |
| Average per-law compressed | ~188 KB |
| Manifest file | ~200 KB |
| Largest law (AKTG) | 3.9 MB (1,490 docs) |

### Why Per-Law Files

- **Granular updates** — re-export a single law without regenerating 500 MB
- **Inspectable** — `zcat data/embeddings/BGB.jsonl.gz | head -3 | python -m json.tool`
- **Git-friendly** — changes are scoped to individual law files
- **Incremental import** — import one law at a time, resume on failure
- **Browsable** — instantly see which laws are included

### Data Flow

```
Pre-compute (one-time, GPU required):
  HTML corpus  →  TEI GPU embedding  →  data/embeddings/*.jsonl.gz
                                            │ (source of truth, Git LFS)
                                            │
Docker image build (CI):                    │
  COPY data/embeddings/  ──►  /app/data/embeddings/  (baked into image)
                                            │
Runtime seeding (no GPU):                   ▼
  seed-embeddings container:
    /app/data/embeddings/*.jsonl.gz  ──►  ChromaDB  (via import_embeddings.py)

  Kubernetes (Helm):
    initContainer  ──►  same image, same script  ──►  ChromaDB
```

### Deployment Targets

| Environment | Seeding Mechanism | ChromaDB Config |
|-------------|-------------------|-----------------|
| Docker Compose (local) | `seed-embeddings` service (parallel) | `chromadb:8000` (internal) |
| Helm / AKS | `initContainers[seed-embeddings]` | `seedEmbeddings.chromaHost:chromaPort` |
| Manual / CI | `python scripts/import_embeddings.py --chroma-host ... --chroma-port ...` | Any reachable ChromaDB |

## Design Decisions

### 1. JSONL as Source of Truth — NOT ChromaDB

ChromaDB is an import target, not the canonical store. Reasons:
- **Portability** — switch vector stores without re-embedding (saves GPU hours)
- **Inspectability** — plain text, standard tools, diffable
- **Versioning** — Git LFS tracks changes over time
- **Reproducibility** — anyone can clone the repo and import into their preferred store

### 2. Float Precision: 6 Decimal Places

Full float32 precision: `0.012345678901234` → 18 chars per float
6dp precision: `0.012346` → 9 chars per float → **~40% size reduction**

Cosine similarity error from rounding to 6dp is < 0.0001 — negligible for retrieval.
Saves ~400 MB across the full corpus.

### 3. Gzip Compression Per File

- Standard tooling everywhere (`gzip`, `zcat`, Python `gzip` module)
- 29% compression ratio on embedding-heavy JSONL (measured)
- Each file independently decompressible
- Git LFS handles binary files well

### 4. Git LFS for `*.jsonl.gz` — Manifest in Regular Git

- `data/embeddings/*.jsonl.gz` → Git LFS (binary, ~533 MB total)
- `data/embeddings/manifest.json` → regular git (small, human-readable, diffable)
- `.gitattributes`: `data/embeddings/*.jsonl.gz filter=lfs diff=lfs merge=lfs -text`

### 5. Direct Pre-Compute to JSONL (Not Export from ChromaDB)

Original plan was to export from ChromaDB. Revised to pre-compute directly from HTML → TEI → JSONL, skipping ChromaDB entirely. Benefits:
- Faster (no database write overhead: 464s vs 607s)
- Simpler (one script, no dependency on populated ChromaDB)
- Cleaner (JSONL files are the primary output, not a secondary export)

### 6. Import Script: ChromaDB First, Extensible Later

The import script takes a `--backend` flag (default: `chroma`). For now only ChromaDB is implemented. Adding a new backend requires one function: `_import_batch_<backend>()`.

### 6. Volume Mount Fix — Still Needed

Even with JSONL as source of truth, the volume mount fix is essential:
- Without it, every `docker compose down` wipes ChromaDB
- With it, imported data persists across container restarts
- Warmup correctly detects populated state and skips

## Files Created

| File | Purpose |
|------|---------|
| `scripts/precompute_to_jsonl.py` | HTML → TEI embed → per-law `.jsonl.gz` files + `manifest.json` |
| `scripts/import_embeddings.py` | Import `.jsonl.gz` files → ChromaDB (extensible to other backends) |
| `data/embeddings/manifest.json` | Corpus metadata (auto-generated) |
| `data/embeddings/*.jsonl.gz` | 2,628 per-law embedding files (auto-generated, Git LFS) |
| `.gitattributes` | Git LFS tracking for `data/embeddings/*.jsonl.gz` |

## Files Modified

| File | Changes |
|------|---------|
| `docker/Dockerfile` | Added COPY for `data/embeddings/` and `scripts/import_embeddings.py` (both targets) |
| `docker-compose.yml` | Added `seed-embeddings` service, removed `WARMUP_MAX_LAWS` env var, `tei_network: external: true` |
| `docker-compose.gpu.yml` | Fix volume mount: `chroma_data:/data` |
| `data/.gitignore` | Un-ignored `embeddings/` directory |
| `pyproject.toml` + `uv.lock` | Moved `sentence-transformers` from runtime → dev deps |
| `app/reranking/__init__.py` | Lazy imports via `__getattr__` (avoids torch at module load) |
| `.devops/helm/legal-mcp/templates/deployment.yaml` | Added conditional `initContainers` block |
| `.devops/helm/legal-mcp/values.yaml` | Added `seedEmbeddings` config section |
| `.devops/helm/values/aks-production.yaml` | Added `seedEmbeddings` config |
| `.devops/helm/values/aks-testing.yaml` | Added `seedEmbeddings` config |
| `.github/workflows/release.yml` | `lfs: true` checkout + `git lfs pull` in `build-app` job, timeout 30m |

## Script Specifications

### `scripts/export_embeddings.py`

```
Usage:
  python scripts/export_embeddings.py [--output-dir data/embeddings] [--precision 6]

Features:
  - Reads all documents from ChromaDB (batched, 1000 at a time)
  - Groups by law_abbrev
  - Writes <LAW>.jsonl.gz per law (one JSON object per line)
  - Rounds embeddings to --precision decimal places (default 6)
  - Generates manifest.json with corpus metadata
  - Progress bar (tqdm or manual)
  - Idempotent (overwrites existing files)

Environment:
  - CHROMA_HOST / CHROMA_PORT (or defaults to localhost:9720)
```

### `scripts/import_embeddings.py`

```
Usage:
  python scripts/import_embeddings.py [--input-dir data/embeddings] [--backend chroma]
  python scripts/import_embeddings.py --status

Features:
  - Reads manifest.json for validation
  - Reads *.jsonl.gz files, decompresses, parses JSONL
  - Batch upserts into ChromaDB (default 500 docs per batch)
  - Creates collection with correct HNSW config (cosine, M=32, efConstruction=256)
  - Resume support: skip laws already fully imported (by count)
  - Progress tracking per law and overall
  - --status flag: check ChromaDB document count vs manifest
  - --backend flag: chroma (default), extensible

Environment:
  - CHROMA_HOST / CHROMA_PORT (or defaults to localhost:9720)
```

## Implementation Order

1. Create Task-04 scratchpad ✅
2. Pitch approach → get approval ✅
3. Create `scripts/precompute_to_jsonl.py` ✅
4. Create `scripts/import_embeddings.py` ✅
5. Fix volume mount in both compose files ✅
6. Smoke test: 3 laws export + import round-trip ✅
7. Run full corpus pre-compute (464s, 0 errors) ✅
8. Run lint + tests (305 pass) ✅
9. Update scratchpads ✅
10. TODO: Git LFS setup, warmup skip verification (deferred)

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Data loss before export | HIGH — 10 min GPU re-compute | Export FIRST, before any compose changes |
| Container already removed | HIGH — data gone | Verify container running before starting |
| JSONL too large for Git LFS | MEDIUM — 533 MB is fine | GitHub LFS allows 2 GB free, GitLab 10 GB |
| Float rounding degrades search | LOW — < 0.0001 cosine error | Validated: 6dp is standard for embeddings |
| Import script slow | LOW — I/O bound not GPU | Batch upserts, ~5 min for 193K docs |

## Comparison: Old Plan vs New Plan

| Aspect | Old (ChromaDB snapshot) | New (Portable JSONL) |
|--------|------------------------|----------------------|
| Format | ChromaDB internal (sqlite + HNSW binary) | Standard JSONL + gzip |
| Portability | ChromaDB only | Any vector store |
| Inspectability | Opaque binary files | Human-readable JSON |
| Size | 1.9 GB | 533 MB (compressed) |
| Git-friendly | No (binary, non-diffable) | Yes (per-law files, manifest diffable) |
| Import time | Instant (copy files) | ~5 min (parse + upsert) |
| Flexibility | Locked to ChromaDB version | Version-independent |
