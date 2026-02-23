# Goal 09: Jina v4 Multi-Task Embeddings + Pre-Seeded Corpus + Helm + Release

> **Status**: ⚪ Not Started
> **Priority**: P1 (High)
> **Created**: 2025-07-23
> **Updated**: 2025-07-23

---

## Overview

Upgrade the embedding pipeline from `jinaai/jina-embeddings-v2-base-de` (161M params, 768-dim, Apache 2.0) to `jinaai/jina-embeddings-v4` (4B params, 2048-dim, 32K context) served via **vLLM**. Pre-compute multi-task embeddings for the entire German federal law corpus (58,255 HTML files across 2,631 laws). Ship pre-seeded ChromaDB collections in the Docker image and git repo. Add a proper Helm chart with tiered autoscaling. Test locally (stdio + Docker SSE). Release as v0.1.0.

---

## Open Decisions (MUST Resolve Before Implementation)

### 1. Model Selection — License Problem

**All Jina v3+ models have restrictive licenses:**

| Model | Params | Dim | Context | License | Tasks |
|-------|--------|-----|---------|---------|-------|
| `jina-embeddings-v2-base-de` (current) | 161M | 768 | 8192 | **Apache 2.0** ✅ | Single-task |
| `jina-embeddings-v3` | 0.6B | 1024 | 8192 | **CC BY-NC 4.0** ❌ | 5 LoRA tasks |
| `jina-embeddings-v4` | 4B | 2048 | 32768 | **Qwen Research License** ❌ | 3 tasks + multimodal |

**User's stated preference**: v4 (32K context, multi-task, vLLM native support)

**User's concern**: Needs permissive for business use → v3 and v4 both fail this

**User mentioned "Vago Solutions"** as potential alternative — `huggingface.co/Vago-Solutions` returns 404. Need correct org name from user.

**Action needed**: User must decide:
- (a) Accept Jina v4 Qwen Research License (free for research, needs commercial license)
- (b) Accept Jina v3 CC BY-NC 4.0 (non-commercial)
- (c) Pay Jina AI for commercial license (available via Azure/AWS marketplace)
- (d) Find the correct "Vago Solutions" (or similar) permissively-licensed alternative
- (e) Stick with v2-base-de (Apache 2.0, but single-task, German-only, smaller)

### 2. vLLM vs TEI for Embedding Inference

**User prefers vLLM** for superior inference performance (continuous batching, etc.)

**Jina v4 has official vLLM support** with pre-merged adapter models:
- `jinaai/jina-embeddings-v4-vllm-retrieval`
- `jinaai/jina-embeddings-v4-vllm-text-matching`
- `jinaai/jina-embeddings-v4-vllm-code`

These are **separate model checkpoints** (adapters merged into base weights), so vLLM can serve them natively without `trust_remote_code`. Each task requires its own vLLM instance or model swap.

**Jina v3** does NOT have pre-merged vLLM variants. It uses LoRA adapters dynamically at inference. TEI handles this natively. vLLM would need custom code to handle `adapter_mask` tensors.

**Architecture implications for v4 + vLLM:**
- Need **2-3 separate vLLM instances** (one per task: retrieval, text-matching, code)
- OR a single vLLM instance that swaps models (not great for latency)
- Memory: 4B BF16 ≈ 8GB VRAM per instance → 16-24GB total for 2-3 tasks
- For pre-computation: can run sequentially (one task at a time)
- For runtime search: need at least the retrieval model running

**TEI alternative**: Single TEI instance serving v3 handles all tasks via the `task` parameter. Uses ~2GB VRAM. But user explicitly prefers vLLM.

**Recommendation**: Use vLLM for v4 if license is acceptable. Otherwise TEI for v3, or keep v2 with TEI.

### 3. Pre-Computed Embedding Storage Strategy

**Corpus stats:**
- 58,255 HTML files → ~50K+ document chunks after parsing
- Current multi-level chunking: law → norm → paragraph

**Storage math per task (50K docs × 2048-dim × float32):**
- ~400MB per task as raw numpy
- ~200MB per task as float16
- 3 tasks × 200MB = ~600MB total

**Options:**
| Strategy | Size | Git-Friendly | Startup Speed |
|----------|------|-------------|---------------|
| ChromaDB SQLite committed to repo | ~1-2GB (with HNSW index) | ⚠️ Large binary | ⚡ Instant (copy file) |
| Parquet files + seed script | ~600MB | ⚠️ LFS needed | 🐌 Build index at start |
| Git LFS for chroma.sqlite3 | ~1-2GB | ✅ LFS | ⚡ Instant |
| Docker image layer only | ~1-2GB | ✅ Not in git | ⚡ Instant in Docker |
| Download from release artifact | ~600MB | ✅ Clean repo | 🐌 Network at start |

**User's requirement**: "pre-computed embeddings should be added and committed to the repo and chroma collection(s) pre-seeded at initial mcp app / server start"

**User's quality requirement**: "encode embedding model / task used as well as specific hnsw params for the respective collection in collection's and chunk's / document's metadata"

**Recommendation**: 
- Git LFS for a pre-built `data/chroma/` directory (the SQLite + WAL files)
- One ChromaDB collection per task (e.g., `german_laws_retrieval`, `german_laws_text_matching`)
- Each collection's metadata encodes: model name, model version, task, embedding dimension, HNSW params (M, efConstruction, efSearch, space)
- Each document's metadata encodes: embedding model, task, chunk level, law_abbrev, norm_id, etc.
- Docker COPY for instant startup

---

## Current Repository State (Critical Context)

### Uncommitted Changes on `main`
13 files modified, not committed:
```
M app/__main__.py          — warmup CLI command added
M app/config.py            — warmup settings, TEI config, LLM config
M app/custom_documents/embeddings.py
M app/ingestion/embeddings.py  — ChromaDB HttpClient support
M app/ingestion/pipeline.py
M app/ingestion/tei_client.py  — multi-endpoint round-robin
M app/server.py            — warmup endpoints, catalog tools
M docker-compose.yml       — TEI network, warmup env vars
M docker/Dockerfile        — HTML corpus COPY, warmup env
M docker/Dockerfile.base
M flake.lock
M pyproject.toml           — dependency updates
M uv.lock
```

Untracked files:
```
?? .agent/bug.md
?? app/ingestion/local_pipeline.py  — local HTML ingestion pipeline
?? app/warmup.py                    — background warmup system
```

**FIRST TASK of next session: commit all uncommitted work on a feature branch.**

### Stashed Work
```
stash@{0}: WIP on feature/goal-05-helm-k8s-devops: refactor: consolidate legal_mcp package under src/
```

### Branch State
- `main` — 4 commits, tag `v0.0.0`, + 13 uncommitted files
- `feature/goal-05-helm-k8s-devops` — Helm/K8s work (PR #1, blocked on test coverage 52% < 73%)
- `feature/helm-hpa-improvements` — HPA/PDB improvements (PR #2, #3 merged)

### Duplicate Helm Chart Locations
- `charts/legal-mcp/templates/` — **EMPTY**, stale from initial template
- `.devops/helm/legal-mcp/` — **REAL chart** with full templates (deployment, HPA, PDB, ingress, etc.)
- `.devops/helm/values/` — Environment-specific values (aks-testing, aks-production, example)

**Action**: Delete stale `charts/` directory, consolidate to `.devops/helm/` only.

### Current Model Config
```python
# app/config.py
embedding_model: str = "jinaai/jina-embeddings-v2-base-de"
use_tei: bool = False  # default; docker-compose sets True
tei_url: str = "http://localhost:8011"
```

### Current Embedding Pipeline Architecture
```
                        ┌─ TEI server (HTTP) ──→ embeddings
                        │   (jina-v2-base-de)
User query ──→ config ──┤
                        │
                        └─ Local model manager ──→ embeddings
                            (sentence-transformers)
                            (GPU/CPU with idle timeout)
```

Both backends implement `.encode()` and `.get_sentence_embedding_dimension()`.
ChromaDB stores: documents, embeddings, metadata (law_abbrev, norm_id, level, etc.)
Single collection: `german_laws`

---

## Jina v4 Technical Details

### Model Card Summary
- **Base**: Qwen2.5-VL-3B-Instruct
- **Size**: 4B params, BF16
- **Embedding dim**: 2048 (Matryoshka: 128, 256, 512, 1024, 2048)
- **Max sequence**: 32,768 tokens
- **Tasks**: `retrieval` (query/passage), `text-matching`, `code`
- **Multi-vector**: Supports ColPali-style late interaction (dim 128)
- **Attention**: FlashAttention2
- **License**: Qwen Research License (NOT permissive for commercial use)

### vLLM Support (Official)
Jina provides pre-merged adapter checkpoints per task:
- `jinaai/jina-embeddings-v4-vllm-retrieval`
- `jinaai/jina-embeddings-v4-vllm-text-matching`
- `jinaai/jina-embeddings-v4-vllm-code`

Each is a standalone model loadable by vLLM's `--task embed` mode without `trust_remote_code`. Serves via OpenAI-compatible `/v1/embeddings` endpoint.

**docker-compose.gpu.yml vLLM config would look like:**
```yaml
vllm-embeddings-retrieval:
  image: vllm/vllm-openai:latest
  command:
    - "--host" "0.0.0.0"
    - "--port" "80"
    - "--model" "jinaai/jina-embeddings-v4-vllm-retrieval"
    - "--task" "embed"
    - "--max-model-len" "32768"
    - "--dtype" "bfloat16"
    - "--gpu-memory-utilization" "0.85"
  ports:
    - "8011:80"
```

### Key Differences from Current v2 Setup

| Aspect | v2-base-de (current) | v4 (target) |
|--------|---------------------|-------------|
| Architecture | XLM-RoBERTa | Qwen2.5-VL |
| Params | 161M | 4B |
| Dim | 768 | 2048 (Matryoshka) |
| Context | 8192 | 32768 |
| Tasks | Single | 3 (retrieval, text-matching, code) |
| VRAM | ~1.5GB | ~8GB per task |
| Inference | TEI or local | vLLM (official support) |
| License | Apache 2.0 | Qwen Research |
| `trust_remote_code` | Yes | No (pre-merged for vLLM) |

---

## Task Breakdown

### Task-00: Commit Uncommitted Work (Prerequisite)
- Create feature branch `feature/goal-09-jina-v4-embeddings`
- Commit all 13 modified + 2 untracked files with descriptive message
- Run lint + tests to verify nothing is broken
- Push branch

### Task-01: Model Selection Resolution
- Present license comparison to user
- Research "Vago Solutions" or alternatives if user needs permissive license
- Alternatives to research: `BAAI/bge-m3`, `intfloat/multilingual-e5-large-instruct`, `Alibaba-NLP/gte-Qwen2-7B-instruct`
- Decide final model + serving backend
- Document decision in this scratchpad

### Task-02: Embedding Pipeline Refactor
- Add vLLM embedding client (OpenAI-compatible `/v1/embeddings` endpoint)
- Support task-specific endpoint routing (different ports/URLs per task)
- Refactor `GermanLawEmbeddingStore` for multi-collection architecture
- One collection per task: `german_laws_retrieval`, `german_laws_text_matching`, etc.
- Collection metadata: `embedding_model`, `model_version`, `task`, `embedding_dimension`, `hnsw_M`, `hnsw_efConstruction`, `hnsw_space`
- Document metadata per chunk: all existing fields + `embedding_model`, `embedding_task`, `embedding_dim`
- Matryoshka dimension configurable (default 1024 for storage efficiency, or 2048 for max quality)
- Update `app/config.py` with new settings

### Task-03: Pre-Compute Embeddings Script
- New script: `scripts/precompute_embeddings.py`
- Reads corpus from `data/html/` (58,255 files, 2,631 laws)
- Parses using existing `local_pipeline.py` logic
- Embeds via vLLM endpoints (one task at a time for GPU efficiency)
- Stores into ChromaDB with full metadata
- Progress tracking, resumability (skip already-embedded docs)
- Outputs to `data/chroma/` directory
- Must run on GPU machine with vLLM serving the models

### Task-04: Pre-Seed at Startup
- If ChromaDB collections are empty AND `data/chroma/` has pre-built data → copy/import
- For Docker: COPY `data/chroma/` at build time, mount as volume
- For local dev: `data/chroma/` in git (LFS if >100MB)
- Modify `app/warmup.py` to detect pre-seeded state and skip re-ingestion
- Health endpoint reports which collections are loaded + document counts

### Task-05: Helm Chart Consolidation + Autoscaling Tiers
- Delete stale `charts/legal-mcp/` directory
- Enhance `.devops/helm/legal-mcp/` chart with:
  - Tiered autoscaling profiles in values:
    - **Small** (dev/testing): 1 replica, no HPA, 512Mi-1Gi
    - **Medium** (staging): 1-3 replicas, HPA on CPU 70%, 1-2Gi
    - **Large** (production): 2-10 replicas, HPA on CPU+memory, PDB, 2-4Gi
  - GPU node affinity/tolerations for embedding inference pods
  - Separate deployment for vLLM embedding server (optional sidecar or standalone)
  - ChromaDB as optional dependency (external service or embedded)
  - Pre-seeded data volume (PVC or emptyDir with init container)
  - Probes using HTTP `/health` endpoint (already implemented)
- Values files: `values/small.yaml`, `values/medium.yaml`, `values/large.yaml`

### Task-06: Docker + Local Testing
- Update `docker-compose.gpu.yml` with vLLM embedding config (per task)
- Update `docker-compose.yml` for non-GPU (TEI fallback or pre-seeded only)
- Test stdio mode: `uv run legal-mcp stdio` → verify search works with pre-seeded data
- Test Docker SSE: `docker compose up` → verify search, health endpoint
- Run test suite, ensure ≥73% coverage

### Task-07: Version Bump + Release
- Bump `pyproject.toml` version to `0.1.0`
- Bump `app/__init__.py` (auto from `importlib.metadata`)
- Bump `.devops/helm/legal-mcp/Chart.yaml` appVersion
- Update CHANGELOG.md
- Update README.md (model info, architecture diagram)
- Tag `v0.1.0`
- Create GitHub release

---

## Architecture (Target State)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Legal-MCP Server (FastMCP)                       │
├─────────────────────────────────────────────────────────────────────┤
│  MCP Tools                                                          │
│  • search_laws(query, task="retrieval")                             │
│  • get_law_by_id(law_abbrev, norm_id)                              │
│  • get_law_stats()                                                  │
│  • search_documents(query, tenant_id)                               │
│  • ingest_documents(...) / ingest_pdf_files(...)                   │
├─────────────────────────────────────────────────────────────────────┤
│  Embedding Layer (task-aware)                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │ vLLM: retrieval  │  │ vLLM: matching   │  │ vLLM: code      │  │
│  │ :8011            │  │ :8012            │  │ :8013           │  │
│  │ jina-v4-vllm-    │  │ jina-v4-vllm-    │  │ jina-v4-vllm-  │  │
│  │ retrieval        │  │ text-matching    │  │ code            │  │
│  └──────────────────┘  └──────────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│  ChromaDB (per-task collections with rich metadata)                 │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐  │
│  │ german_laws_retrieval    │  │ german_laws_text_matching      │  │
│  │ metadata:                │  │ metadata:                      │  │
│  │   model: jina-v4         │  │   model: jina-v4               │  │
│  │   task: retrieval        │  │   task: text-matching          │  │
│  │   dim: 2048              │  │   dim: 2048                    │  │
│  │   hnsw:space: cosine     │  │   hnsw:space: cosine           │  │
│  │   hnsw:M: 16             │  │   hnsw:M: 16                   │  │
│  │   hnsw:efConstruction:200│  │   hnsw:efConstruction: 200     │  │
│  └──────────────────────────┘  └────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│  Pre-Seeded Data (shipped in Docker image + git LFS)               │
│  data/chroma/ → copied to ChromaDB persist path at startup         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Files to Modify / Create

### Modify
- `app/config.py` — vLLM embedding endpoints, task-specific URLs, Matryoshka dim
- `app/ingestion/embeddings.py` — multi-collection, per-task, rich metadata
- `app/ingestion/tei_client.py` → rename/generalize to `embedding_client.py` (or add `vllm_client.py`)
- `app/ingestion/model_manager.py` — add vLLM backend option
- `app/ingestion/local_pipeline.py` — multi-task embedding during ingestion
- `app/ingestion/pipeline.py` — task-aware search
- `app/tools/german_laws.py` — expose task parameter in search
- `app/warmup.py` — detect pre-seeded data, skip if populated
- `app/server.py` — update tool descriptions
- `docker-compose.gpu.yml` — vLLM embedding services (per task)
- `docker-compose.yml` — non-GPU fallback
- `docker/Dockerfile` — COPY pre-seeded chroma data
- `.devops/helm/legal-mcp/values.yaml` — autoscaling tiers
- `.devops/helm/legal-mcp/templates/deployment.yaml` — GPU tolerations
- `pyproject.toml` — version bump, any new deps
- `CHANGELOG.md`
- `README.md`

### Create
- `app/ingestion/vllm_embedding_client.py` — OpenAI-compatible embedding client for vLLM
- `scripts/precompute_embeddings.py` — offline corpus embedding script
- `.devops/helm/values/small.yaml` — dev tier
- `.devops/helm/values/medium.yaml` — staging tier
- `.devops/helm/values/large.yaml` — production tier

### Delete
- `charts/legal-mcp/` — stale empty duplicate (real chart is `.devops/helm/`)

---

## HNSW Configuration Reference

For legal text embeddings (high dimensionality, recall-critical):

```python
collection_metadata = {
    "description": "German federal law documents — retrieval task",
    "embedding_model": "jinaai/jina-embeddings-v4",
    "embedding_model_version": "2025-06",
    "embedding_task": "retrieval",
    "embedding_dimension": 2048,  # or truncated Matryoshka dim
    "hnsw:space": "cosine",       # cosine similarity
    "hnsw:M": 16,                 # connections per node (default 16, higher = better recall, more memory)
    "hnsw:construction_ef": 200,  # build-time search width (default 100, higher = better index quality)
    "hnsw:search_ef": 100,        # query-time search width (default 10, higher = better recall)
}
```

Per-document metadata:
```python
document_metadata = {
    # Existing fields
    "law_abbrev": "BGB",
    "norm_id": "§ 433",
    "level": "norm",           # law | norm | paragraph
    "title": "Vertragstypische Pflichten beim Kaufvertrag",
    "jurisdiction": "DE-Federal",
    # New fields
    "embedding_model": "jinaai/jina-embeddings-v4",
    "embedding_task": "retrieval",
    "embedding_dimension": 2048,
    "chunk_token_count": 1234,
    "source_file": "data/html/bgb/__433.html",
}
```

---

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| v4 license blocks commercial use | High | Certain (Qwen Research) | Resolve model choice FIRST (Task-01) |
| 4B model needs 8GB+ VRAM per task | Medium | Certain | Use Matryoshka (1024-dim) to reduce VRAM; serve tasks sequentially for pre-compute |
| Pre-computed data too large for git | Medium | Likely (~1-2GB) | Git LFS; or download from release |
| "Vago Solutions" org doesn't exist on HF | Medium | Confirmed (404) | Ask user for correct name / URL |
| vLLM embedding support is newer/less battle-tested than TEI | Medium | Possible | Keep TEI as fallback backend |
| ChromaDB migration (768-dim → 2048-dim) breaks existing data | Low | Certain | New collection names per task; old collection left in place |
| Test coverage below 73% blocks CI | High | Current state | Must add tests as part of this goal |

---

## Permissively-Licensed Alternatives to Research

If Jina license is a blocker, investigate these multi-task / multilingual options:

| Model | Params | Dim | Context | License | Multi-Task | German |
|-------|--------|-----|---------|---------|-----------|--------|
| `BAAI/bge-m3` | 568M | 1024 | 8192 | MIT ✅ | Dense+Sparse+ColBERT | 100+ langs |
| `intfloat/multilingual-e5-large-instruct` | 560M | 1024 | 512 | MIT ✅ | Instruction-based | 100+ langs |
| `Alibaba-NLP/gte-Qwen2-7B-instruct` | 7B | 3584 | 131072 | Apache 2.0 ✅ | Instruction-based | Multi |
| `Alibaba-NLP/gte-Qwen2-1.5B-instruct` | 1.5B | 1536 | 131072 | Apache 2.0 ✅ | Instruction-based | Multi |
| `nomic-ai/nomic-embed-text-v2-moe` | ~600M | 768 | 8192 | Apache 2.0 ✅ | Task prefixes | Multi |
| `sentence-transformers/all-MiniLM-L12-v2` | 33M | 384 | 512 | Apache 2.0 ✅ | Single | English-primary |

**BGE-M3** is particularly interesting: MIT license, 1024-dim, 8192 context, supports dense + sparse + ColBERT multi-vector retrieval. Strong MTEB scores. vLLM support confirmed.

**GTE-Qwen2-1.5B-instruct**: Apache 2.0, 1536-dim, 131K context (!), instruction-based task routing. Reasonable VRAM (~3GB). vLLM compatible.

---

## References

- [jina-embeddings-v4 model card](https://huggingface.co/jinaai/jina-embeddings-v4)
- [jina-embeddings-v4-vllm-retrieval](https://huggingface.co/jinaai/jina-embeddings-v4-vllm-retrieval)
- [jina-embeddings-v3 model card](https://huggingface.co/jinaai/jina-embeddings-v3)
- [BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3)
- [ChromaDB HNSW tuning](https://docs.trychroma.com/docs/collections/configure)
- [vLLM embedding support](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html#embedding-api)
- Goal 05 scratchpad: `.agent/goals/05-DevOps-Helm-K8s-Environment/scratchpad.md`
- Goal 02 scratchpad: `.agent/goals/02-Legal-MCP/scratchpad.md`
```

Now let me update the goals index: