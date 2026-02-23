# Goal 09: Pre-Seeded Embeddings + ColBERT Reranking + Helm + Release v0.1.0

> **Status**: 🟡 In Progress
> **Priority**: P0 (Critical)
> **Created**: 2025-07-23
> **Updated**: 2025-07-24

---

## Overview

Keep `jinaai/jina-embeddings-v2-base-de` (161M params, 768-dim, Apache 2.0) on HF-TEI for first-stage dense embeddings in ChromaDB. Add **SauerkrautLM-Reason-EuroColBERT** (210M, Apache 2.0) as a **ColBERT late-interaction re-ranker** on top for precision. Pre-compute embeddings for the entire German federal law corpus (58,255 HTML files across 2,631 laws) at 3 chunk granularities (law, norm, paragraph). Ship pre-seeded ChromaDB collections in the Docker image and git repo. Add proper Helm chart with tiered autoscaling. Test locally (stdio + Docker SSE). Release as v0.1.0.

---

## Resolved Decisions

### 1. Model Selection — RESOLVED ✅

**Decision**: Stay on `jinaai/jina-embeddings-v2-base-de` (Apache 2.0) for dense embeddings.
Add `VAGOsolutions/SauerkrautLM-Reason-EuroColBERT` (Apache 2.0) for ColBERT re-ranking.

**Rationale (from research session 2025-07-24):**
- Jina v3 (CC BY-NC 4.0) and v4 (Qwen Research) are both **non-permissive** — blocked for commercial use
- VAGOsolutions models (org: `VAGOsolutions` on HF) are Apache 2.0 ✅
- VAGOsolutions uses **ColBERT Late Interaction** (PyLate/Voyager) — fundamentally different from dense embeddings
  - Multi-vector per document (one 128-dim vector per token), not single-vector
  - Requires PyLate + Voyager HNSW, NOT compatible with ChromaDB
  - Would require replacing entire storage backend — too disruptive
- **Hybrid approach**: Keep ChromaDB + v2-base-de for fast ANN first-stage, add ColBERT re-ranking for precision
- v2-base-de is single-task (no retrieval/clustering/classification modes), but re-ranker compensates
- v2-base-de has 864K downloads/month, German-specialized, battle-tested

| Component | Model | Params | License | Role |
|-----------|-------|--------|---------|------|
| Dense embeddings | `jinaai/jina-embeddings-v2-base-de` | 161M | Apache 2.0 | First-stage ANN retrieval via ChromaDB |
| Re-ranker | `VAGOsolutions/SauerkrautLM-Reason-EuroColBERT` | 210M | Apache 2.0 | Second-stage precision re-ranking |

### 2. Serving Backend — RESOLVED ✅

**Decision**: HF-TEI for embeddings (current setup, no change). PyLate for ColBERT re-ranking (new).

**Rationale:**
- v2-base-de works perfectly on TEI — no reason to change
- ColBERT re-ranking uses PyLate library at query time (encode query + re-rank candidates)
- No vLLM needed for this iteration
- Re-ranker runs on CPU or GPU; only invoked on top-K candidates (not full corpus)

### 3. Pre-Computed Embedding Storage — RESOLVED ✅

**Decision**: Single ChromaDB collection with multi-level chunks. Git LFS for pre-built data.

**Rationale:**
- v2-base-de is single-task → one collection `german_laws` (not per-task collections)
- Differentiate by **chunk granularity** (law/norm/paragraph) via metadata, not separate collections
- Storage math: 50K docs × 768-dim × float32 ≈ 150MB raw + HNSW index ≈ 300-500MB total
- Git LFS for `data/chroma/` directory
- Docker COPY for instant startup
- Collection metadata encodes: model name, model version, embedding dimension, HNSW params
- Document metadata encodes: chunk_level, law_abbrev, norm_id, embedding_model

### 4. VAGOsolutions Research Notes

**Org**: `VAGOsolutions` on HuggingFace (not `Vago-Solutions` — that 404s)
**Focus**: German-specialized LLMs ("SauerkrautLM" series) and retrieval models
**Key retrieval models (all Apache 2.0):**

| Model | Params | Architecture | German nDCG@10 | Notes |
|-------|--------|-------------|----------------|-------|
| SauerkrautLM-Reason-EuroColBERT | 210M | ColBERT/EuroBERT | 47.71 (NanoBEIR), 16.43 (BRIGHT) | Best German reasoning, beats 7B models |
| SauerkrautLM-Multi-ModernColBERT | 149M | ColBERT/ModernBERT | 51.21 (NanoBEIR) | Better general retrieval |
| SauerkrautLM-Multi-Reason-ModernColBERT | 149M | ColBERT/ModernBERT | — | Reasoning + multilingual |

**Why ColBERT as re-ranker, not primary retrieval:**
- ColBERT stores N vectors per document (one per token) — incompatible with ChromaDB
- Requires PyLate + Voyager HNSW index — completely different storage paradigm
- Re-ranking only runs on top-K candidates, so cost is manageable
- Gets us the quality benefit without replacing the entire storage layer

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

## Architecture (Revised Target State)

```
Query flow:
                                    ┌─ TEI (jina-v2-base-de) ──→ 768-dim embedding
                                    │
User query ──→ config ──────────────┤
                                    │
                                    └─ Local model manager (fallback)
                                               │
                                               ▼
                                    ChromaDB ANN search (top-100 candidates)
                                               │
                                               ▼
                                    ColBERT re-ranker (SauerkrautLM-Reason-EuroColBERT)
                                    PyLate MaxSim scoring on candidates
                                               │
                                               ▼
                                    Top-10 final results (high precision)

Pre-computation flow:
  58,255 HTML files ──→ local_pipeline.py (parse + chunk)
         │                  │
         │           law-level chunks
         │           norm-level chunks
         │           paragraph-level chunks
         │                  │
         │                  ▼
         │           TEI batch embed (jina-v2-base-de, 768-dim)
         │                  │
         │                  ▼
         └──────→ ChromaDB collection: `german_laws`
                    metadata: chunk_level, law_abbrev, norm_id, embedding_model
                    stored in: data/chroma/ (Git LFS)
```

---

## Jina v4 Technical Details (Historical Research — NOT USING)

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

## Task Breakdown (Revised)

### Task-00: Commit Uncommitted Work (Prerequisite) — 🟢 COMPLETE
- [x] Create feature branch `feature/goal-09-embeddings-upgrade`
- [x] Commit all 15 modified/untracked files (warmup, local pipeline, TEI multi-endpoint, config, Docker)
- [x] Commit Goal 09 scratchpad and goals index update
- [x] Delete stale `charts/` directory, add to `.gitignore`
- [x] Add `huggingface-hub` as dev dependency for model research
- [x] Run lint + tests (232 passed, ruff clean)
- [x] Push branch

**Commits:**
- `2471c2a6` feat: add warmup system, local ingestion pipeline, TEI multi-endpoint, and dev tooling
- `5c04d103` docs: add Goal 09 scratchpad and update goals index
- `d5a94f0a` chore: remove stale charts/ directory, add to .gitignore

### Task-01: Model Selection Resolution — 🟢 COMPLETE
- [x] Researched VAGOsolutions org (`VAGOsolutions` on HF, not `Vago-Solutions`)
- [x] Found SauerkrautLM ColBERT suite — Apache 2.0, German-specialized, excellent quality
- [x] Identified ColBERT architecture incompatibility with ChromaDB (multi-vector vs single-vector)
- [x] Proposed hybrid: v2-base-de (dense, ChromaDB) + EuroColBERT (ColBERT re-ranker)
- [x] User approved plan
- [x] Documented decisions in this scratchpad

### Task-02: Add ColBERT Re-Ranking Layer — ⚪ Not Started
- Add `pylate` dependency for ColBERT inference
- Create `app/reranking/colbert_reranker.py`:
  - Load `VAGOsolutions/SauerkrautLM-Reason-EuroColBERT` via PyLate
  - `rerank(query: str, candidates: list[dict]) -> list[dict]` function
  - Encode query as ColBERT query embedding, encode candidates as document embeddings
  - Score via MaxSim, return re-ordered candidates with scores
  - Lazy model loading with configurable GPU/CPU
- Create `app/reranking/__init__.py`
- Update `app/config.py` with reranking settings:
  - `reranking_enabled: bool = True`
  - `reranking_model: str = "VAGOsolutions/SauerkrautLM-Reason-EuroColBERT"`
  - `reranking_top_k: int = 10` (final results after re-ranking)
  - `retrieval_top_k: int = 100` (candidates from ChromaDB before re-ranking)
- Integrate into search tools: ChromaDB → top-100 → ColBERT re-rank → top-10
- Config toggle to disable re-ranking (fallback to current behavior)

### Task-03: Pre-Compute Embeddings Script — ⚪ Not Started
- New script: `scripts/precompute_embeddings.py`
- Reads corpus from `data/html/` (58,255 files, 2,631 laws)
- Parses using existing `local_pipeline.py` logic
- Embeds via TEI (jina-v2-base-de, 768-dim)
- Stores into ChromaDB single collection `german_laws` with full metadata:
  - Collection metadata: `embedding_model`, `model_version`, `embedding_dimension`, `hnsw_M`, `hnsw_efConstruction`, `hnsw_space`
  - Document metadata: `chunk_level` (law/norm/paragraph), `law_abbrev`, `norm_id`, `embedding_model`
- 3 chunk granularities: law-level summary, norm-level, paragraph-level
- Progress tracking, resumability (skip already-embedded docs)
- Outputs to `data/chroma/` directory

### Task-04: Pre-Seed at Startup — ⚪ Not Started
- If ChromaDB collection is empty AND `data/chroma/` has pre-built data → copy/import
- For Docker: COPY `data/chroma/` at build time, mount as volume
- For local dev: `data/chroma/` in git (LFS if >100MB)
- Modify `app/warmup.py` to detect pre-seeded state and skip re-ingestion
- Health endpoint reports collection status + document counts

### Task-05: Helm Chart Consolidation + Autoscaling Tiers — ⚪ Not Started
- Enhance `.devops/helm/legal-mcp/` chart with:
  - Tiered autoscaling profiles in values:
    - **Small** (dev/testing): 1 replica, no HPA, 512Mi-1Gi
    - **Medium** (staging): 1-3 replicas, HPA on CPU 70%, 1-2Gi
    - **Large** (production): 2-10 replicas, HPA on CPU+memory, PDB, 2-4Gi
  - TEI sidecar or standalone deployment for embedding inference
  - ChromaDB as optional dependency (external service or embedded)
  - Pre-seeded data volume (PVC or emptyDir with init container)
  - Probes using HTTP `/health` endpoint (already implemented)
- Values files: `values/small.yaml`, `values/medium.yaml`, `values/large.yaml`

### Task-06: Docker + Local Testing — ⚪ Not Started
- Update `docker-compose.yml` for TEI + ColBERT re-ranker
- Update `docker-compose.gpu.yml` for GPU-accelerated re-ranking
- Test stdio mode: `uv run legal-mcp stdio` → verify search + re-ranking with pre-seeded data
- Test Docker SSE: `docker compose up` → verify search, health endpoint
- Run test suite, ensure ≥73% coverage

### Task-07: Version Bump + Release — ⚪ Not Started
- Bump `pyproject.toml` version to `0.1.0`
- Bump `app/__init__.py` (auto from `importlib.metadata`)
- Bump `.devops/helm/legal-mcp/Chart.yaml` appVersion
- Update CHANGELOG.md
- Update README.md (model info, architecture diagram, re-ranking documentation)
- Tag `v0.1.0`
- Create GitHub release

---

## Architecture (Original Plan — SUPERSEDED)

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

### Modify (Revised)
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

## Risks & Mitigations (Revised)

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

## Completed Research: Permissive Alternatives

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