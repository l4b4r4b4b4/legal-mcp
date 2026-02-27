# Goals Index & Tracking Scratchpad

> Central hub for tracking all goals in Legal-MCP

---

## Active Goals

| ID | Goal Name | Status | Priority | Last Updated |
|----|-----------|--------|----------|--------------|
| 01 | Initial Setup & Release v0.0.0 | 🟡 In Progress | Critical | 2025-01-01 |
| 02 | Legal-MCP Knowledge Base | 🟡 In Progress | P1 (High) | 2026-01-24 |
| 03 | Custom Document Ingestion and Semantic Search | 🟡 In Progress | P1 (High) | 2026-01-25 |
| 04 | German State Law Sources & Indexing | ⚪ Not Started | P1 (High) | 2026-01-25 |
| 05 | DevOps Tooling - Helm/K8s Environment | 🔴 Blocked | P1 (High) | 2026-02-07 |
| 06 | Multi-Jurisdiction Legal Framework | ⚪ Not Started | P1 (High) | 2026-02-07 |
| 07 | International Construction Law Corpus | ⚪ Not Started | P1 (High) | 2026-02-07 |
| 08 | Cross-Jurisdiction Comparison Tool | ⚪ Not Started | P2 (Medium) | 2026-02-07 |
| 09 | Pre-Seeded Embeddings + ColBERT Reranking + Helm + Release v0.1.0 | 🟡 In Progress | P0 (Critical) | 2025-07-24 |

---

## Status Legend

- 🟢 **Complete** — Goal achieved and verified
- 🟡 **In Progress** — Actively being worked on
- 🔴 **Blocked** — Waiting on external dependency or decision
- ⚪ **Not Started** — Planned but not yet begun
- ⚫ **Archived** — Abandoned or superseded

---

## Priority Levels

- **Critical** — Blocking other work or system stability
- **High** — Important for near-term objectives
- **Medium** — Should be addressed when time permits
- **Low** — Nice to have, no urgency

---

## Quick Links

- [00-Template-Goal](./00-Template-Goal/scratchpad.md) — Template for new goals
- [01-Initial-Setup-And-Release](./01-Initial-Setup-And-Release/scratchpad.md) — Complete setup and validate release pipeline
- [02-Legal-MCP](./02-Legal-MCP/scratchpad.md) — Legal knowledge base with hybrid retrieval (semantic + structured)
- [03-Custom-Document-Ingestion-and-Semantic-Search](./03-Custom-Document-Ingestion-and-Semantic-Search/scratchpad.md) — Ingest custom documents (case files, briefs, contracts) and semantically search them with filters
- [04-German-State-Law-Sources-And-Indexing](./04-German-State-Law-Sources-And-Indexing/scratchpad.md) — Add German state law (Landesrecht) sources, ingestion, normalization, and retrieval tools
- [05-DevOps-Helm-K8s-Environment](./05-DevOps-Helm-K8s-Environment/scratchpad.md) — Add Azure CLI, Helm, kubectl to flake.nix for Kubernetes deployment
- [06-Multi-Jurisdiction-Legal-Framework](./06-Multi-Jurisdiction-Legal-Framework/scratchpad.md) — Abstract jurisdiction adapters for international legal corpus
- [07-International-Construction-Law-Corpus](./07-International-Construction-Law-Corpus/scratchpad.md) — Build preprocessed legal corpora (1 country per continent) for construction law
- [08-Cross-Jurisdiction-Comparison-Tool](./08-Cross-Jurisdiction-Comparison-Tool/scratchpad.md) — Compare legislation across jurisdictions for compliance analysis
- [09-Jina-V4-Multi-Task-Embeddings](./09-Jina-V4-Multi-Task-Embeddings/scratchpad.md) — Pre-seeded v2-base-de embeddings, ColBERT re-ranking (SauerkrautLM-Reason-EuroColBERT), Helm autoscaling tiers, release v0.1.0

---

## Goal Creation Guidelines

1. **Copy from template:** Use `00-Template-Goal/` as starting point
2. **Follow numbering:** Goals are `01-10-*`, tasks are `Task-01-*`
3. **Update this index:** Add new goals to the table above
4. **Reference, don't duplicate:** Link to detailed scratchpads instead of copying content

---

## Notes

- Each goal has its own directory under `.agent/goals/`
- Goals contain a `scratchpad.md` and one or more `Task-XX/` subdirectories
- Tasks are atomic, actionable units of work within a goal
- Follow `.rules` workflow: Research → Plan → Pitch → Implement → Document

---

## Recent Activity

- **2025-07-24:** Goal 09 Task-00 COMPLETE + Task-01 COMPLETE — decisions resolved
  - Task-00: Committed 15 files on `feature/goal-09-embeddings-upgrade` (3 commits), pushed
  - Task-01: Model selection resolved — stay on jina-v2-base-de + add ColBERT re-ranking
  - Deleted stale `charts/` dir, added to `.gitignore`
  - Added `huggingface-hub` as dev dependency for model research via CLI/API
  - **Decision**: Keep `jinaai/jina-embeddings-v2-base-de` (Apache 2.0) on HF-TEI for dense embeddings
  - **Decision**: Add `VAGOsolutions/SauerkrautLM-Reason-EuroColBERT` (210M, Apache 2.0) as ColBERT re-ranker
  - VAGOsolutions org found (`VAGOsolutions` on HF) — ColBERT Late Interaction models, not dense
  - ColBERT incompatible with ChromaDB (multi-vector per doc) → use as re-ranker only
  - Jina v3/v4 both non-permissive (CC BY-NC / Qwen Research) — ruled out
  - Next: Task-02 (add ColBERT re-ranking layer), Task-03 (pre-compute embeddings)
- **2025-07-23:** Goal 09 created — research and planning session
  - Full repo review: 4 commits on main + 13 uncommitted files + 1 stash
  - Corpus: 58,255 HTML files, 2,631 laws (448MB), ChromaDB empty (168K)
  - Researched Jina v3/v4, BGE-M3, GTE-Qwen2-1.5B as alternatives
  - Helm chart at `.devops/helm/`, stale empty `charts/` to delete
  - Target release: v0.1.0
- **2026-02-07 01:00:** Goal 05 PR #1 blocked on test coverage
  - All Helm/DevOps work complete (chart, CD pipeline, values files)
  - CI: 169 tests pass, but coverage is 52.81% (required: 73%)
  - PR: https://github.com/l4b4r4b4b4/legal-mcp/pull/1
  - Branch: `feature/goal-05-helm-k8s-devops`
  - Next: Add tests to reach 73% coverage, then merge and deploy to AKS
- **2026-02-07:** Goals 05-08 created — Consulate Building Compliance Initiative
  - **Goal 05:** DevOps Helm/K8s Environment (~4-5h) — Add K8s tooling to flake.nix, create Helm chart
  - **Goal 06:** Multi-Jurisdiction Framework (~20h) — Abstract adapters, unified schema, EU proof-of-concept
  - **Goal 07:** International Construction Law Corpus (~90-100h) — 1 country per continent (USA, Brazil, Japan, South Africa, Australia)
  - **Goal 08:** Cross-Jurisdiction Comparison Tool (~50h) — Semantic similarity, gap analysis, compliance matrices
  - Business context: German consulates need AI-assisted tooling to assess building compliance with German standards worldwide
  - Dependency chain: Goal 05 (independent) → Goal 06 → Goal 07 → Goal 08
- **2026-01-25:** Goal 04 created — German State Law Sources & Indexing
  - Goal: extend Legal-MCP beyond federal law by adding ingest + indexing + retrieval for German state law (Landesrecht) and related downloadable public sources (e.g., decisions), modeled after the existing federal pipeline.
  - Next: author `04-German-State-Law-Sources-And-Indexing/scratchpad.md` with research notes (source portals, formats, licensing), architecture decisions, task breakdown, and success criteria.
- **2025-01-24:** Goal 02 Task-02 COMPLETE - All 5 Phases Done! 🎉
  - Phase 5: Bulk download (58,255 HTML files) + ChromaDB ingestion (193,371 documents)
  - HTTP/2 multiplexing bypassed rate limits (293 norms/sec download)
  - TEI backend with 2 GPU replicas for fast embedding
  - Semantic search verified (GG, BGB, StGB returning correct results)
  - All 76 tests passing
- **2025-01-24:** Goal 02 Task-02 Phases 1-4 Complete
  - Phase 1: HTML ingestion with selectolax (5-25x faster than BeautifulSoup)
  - Phase 2: Embedding pipeline with Jina German model + ChromaDB
  - Phase 3: MCP tools (search_laws, get_law_by_id, ingest_german_laws, get_law_stats)
  - Phase 4: TEI integration + concurrent processing
  - GPU-optimized singleton model manager with auto-cleanup
- **2026-01-23:** Goal 02 major architecture redesign
  - Pivoted from web scraping (dejure.org) to official XML bulk downloads
  - Added hybrid retrieval: semantic search + structured queries
  - Multi-level chunking strategy for different query granularities
  - Version tracking for law amendments over time
  - Comprehensive task breakdown (~40h for MVP)
- **2025-01-01:** Project generated with custom template variant
- **2025-01-01:** Goal 01 created - Initial Setup & Release v0.0.0
  - Critical first goal to validate entire development and release pipeline
  - Ensures project works out of the box and all workflows function
  - Publishing v0.0.0 validates packaging before real development begins