# Task-03: Pre-Compute Embeddings (GPU-Accelerated) — 🟡 In Progress

## Objective

Pre-compute embeddings for the entire German federal law corpus (2,631 laws, 58,255 HTML files) using GPU-accelerated HF-TEI. All inference runs on CUDA via the CDI device pattern already established in `docker-compose.gpu.yml`. Output stored in ChromaDB for instant startup.

## Success Criteria

- [ ] `docker-compose.gpu.yml` updated: includes ChromaDB, single TEI replica for embedding, CDI GPU access
- [ ] `scripts/precompute_embeddings.py` created: orchestrates full corpus embedding via TEI GPU
- [ ] TEI client batch-size cap raised for GPU scenarios (current hard cap of 8 is CPU-conservative)
- [ ] Full corpus embedded: ~58K documents in ChromaDB `german_laws` collection
- [ ] Progress tracking, resumability, graceful Ctrl+C handling
- [ ] ColBERT reranking uses CUDA when available (`colbert_device=auto` → cuda)
- [ ] Tests pass, lint clean

## Architecture

```
docker-compose.gpu.yml stack:
  ┌─────────────────────────────────────────────────┐
  │  chromadb (chromadb/chroma:latest)               │
  │    port 8001 → 8000                              │
  │    volume: chroma_data                           │
  ├─────────────────────────────────────────────────┤
  │  tei-embeddings (ghcr.io/.../text-embeddings-   │
  │    inference:86-1.6)                             │
  │    model: jinaai/jina-embeddings-v2-base-de      │
  │    port 8013 → 8080                              │
  │    CDI GPU: nvidia.com/gpu=all                   │
  │    flash attention, 128 max_client_batch_size    │
  ├─────────────────────────────────────────────────┤
  │  tei-reranker (same image)                       │
  │    model: BAAI/bge-reranker-v2-m3                │
  │    port 8020 → 8080                              │
  │    CDI GPU: nvidia.com/gpu=all                   │
  ├─────────────────────────────────────────────────┤
  │  vllm (vllm/vllm-openai:latest) — optional      │
  │    port 7373 → 80                                │
  │    CDI GPU: nvidia.com/gpu=all                   │
  └─────────────────────────────────────────────────┘

Pre-compute flow:
  scripts/precompute_embeddings.py
    → checks TEI healthy (GET /health)
    → checks ChromaDB healthy (heartbeat)
    → calls: warmup --max-laws N (or all)
      → local_pipeline.py parses HTML
      → TEIEmbeddingClient.encode() → GPU TEI /embed endpoint
      → ChromaDB upsert (HttpClient)
    → reports timing, throughput, document counts
```

## Key Design Decisions

### 1. Reuse `warmup` command — don't duplicate ingestion logic

The existing `warmup` CLI + `local_pipeline.py` + `TEIEmbeddingClient` already handles:
- HTML discovery and parsing (concurrent, 8 workers)
- Chunking at 3 levels (law, norm, paragraph)
- Batch embedding via TEI (with round-robin load balancing)
- ChromaDB upsert with full metadata
- Resume support (skip already-ingested laws)
- Progress tracking

The pre-compute script orchestrates this pipeline, adding:
- Infrastructure health checks (TEI + ChromaDB)
- GPU-tuned batch sizes
- Timing reports and throughput stats
- Graceful signal handling

### 2. Raise TEI client batch cap for GPU

Current state in `tei_client.py`:
- Hard cap: `safe_limit = min(safe_limit, 8)` — designed for CPU TEI
- GPU TEI with `--max-client-batch-size 128` can handle much more
- The auto-detection reads `/info` but then clamps to 8

Fix: respect the server's reported `max_client_batch_size` when it's high (GPU indicator).
Keep the conservative cap only for low-capacity servers (CPU/single-replica).

### 3. CDI GPU device pattern (not `--gpus` runtime flag)

From `docker-compose.gpu.yml`:
```yaml
devices:
  - nvidia.com/gpu=all
device_cgroup_rules:
  - "c 195:* rmw"
  - "c 236:* rmw"
environment:
  - NVIDIA_VISIBLE_DEVICES=all
  - NVIDIA_DRIVER_CAPABILITIES=compute,utility
```

This is the Container Device Interface (CDI) pattern — works without nvidia-container-toolkit CLI. Already proven working in the existing gpu compose file.

### 4. Single TEI replica for pre-compute

The existing gpu compose has 4 replicas for concurrent serving. For pre-compute we only need 1 replica (sequential batch processing, no concurrent user queries). This maximizes GPU memory available per instance.

### 5. ColBERT on CUDA — already handled

`colbert_device=auto` in config → `ColBERTReranker` auto-detects CUDA and uses it. No changes needed for the reranker — it will use GPU when available during `search_laws` at query time. Pre-compute only does embedding (not reranking).

## Files to Create

| File | Purpose |
|------|---------|
| `scripts/precompute_embeddings.py` | Orchestration script for GPU-accelerated corpus embedding |

## Files to Modify

| File | Changes |
|------|---------|
| `docker-compose.gpu.yml` | Add chromadb service, consolidate TEI to single replica on port 8013, clean up |
| `app/ingestion/tei_client.py` | Raise batch-size cap for GPU TEI (respect server's max_client_batch_size) |

## docker-compose.gpu.yml Plan

Services to keep/add:
- `chromadb` — copy from docker-compose.yml (latest, healthcheck, port 8001)
- `tei-embeddings` — single replica, port 8013, CDI GPU, jina-v2-base-de
- `tei-reranker` — single replica, port 8020, CDI GPU, BAAI/bge-reranker-v2-m3
- `vllm` — keep as-is (optional, for RAG LLM)
- `test` — keep (nvidia-smi smoke test)

Remove/change:
- Reduce TEI embeddings from 4 replicas to 1 (pre-compute doesn't need concurrency)
- Fix port mapping: use 8013:8080 (match .zed/settings.json TEI_URL)
- Add `hf_cache` volume mount for model caching
- Add `chroma_data` volume

## TEI Client Batch Size Fix

Current (`tei_client.py` L144):
```python
# Hard cap: never exceed 8 per request for legal workloads
if safe_limit is not None:
    safe_limit = min(safe_limit, 8)
```

Proposed:
```python
# For high-capacity servers (GPU with large batch budgets), allow
# up to the server's reported max_client_batch_size.
# For low-capacity servers (CPU / small token budget), keep conservative cap.
GPU_BATCH_THRESHOLD = 32  # servers reporting >= 32 are likely GPU-backed
if safe_limit is not None:
    if client_limit is not None and client_limit >= GPU_BATCH_THRESHOLD:
        # GPU server — respect its limit (typically 64-128)
        safe_limit = min(safe_limit, client_limit)
    else:
        # CPU/low-capacity — conservative cap for legal texts
        safe_limit = min(safe_limit, 8)
```

Also raise the concurrency threshold for GPU:
- Current: `max_workers = 1 if server_capacity <= 8 else min(3, len(batches))`
- GPU TEI with `max_concurrent_requests=1024` → allow more concurrent batches

## Pre-Compute Script Plan

```
scripts/precompute_embeddings.py

Usage:
  # Start infrastructure first:
  docker compose -f docker-compose.gpu.yml up -d chromadb tei-embeddings

  # Then run pre-compute:
  python scripts/precompute_embeddings.py [--max-laws N] [--batch-size 256]

Features:
  - Health checks: TEI + ChromaDB with retry/wait
  - Corpus status check: skip if already complete
  - Delegates to warmup's run_warmup_sync()
  - Timing: total time, docs/sec, laws/sec
  - Signal handling: Ctrl+C reports partial progress
  - Exit codes: 0 success, 1 failure, 2 partial
```

## Throughput Estimates

With GPU TEI (RTX 3080 Ti, 12GB VRAM):
- jina-v2-base-de: ~161M params, ~650MB VRAM
- Batch size 32-64 texts per request
- Estimated: ~200-500 docs/sec embedding throughput
- 58,255 docs → ~2-5 minutes for full corpus (vs 4-8 hours on CPU)

## Implementation Order

1. Create Task-03 scratchpad (this file) ✅
2. Pitch approach → get approval
3. Update `docker-compose.gpu.yml` (add chromadb, consolidate TEI)
4. Fix TEI client batch cap for GPU
5. Create `scripts/precompute_embeddings.py`
6. Test: start GPU stack, run pre-compute, verify corpus
7. Run lint + tests
8. Commit and push