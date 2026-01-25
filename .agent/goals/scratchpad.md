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
| 05 | (Reserved) | ⚪ Not Started | - | - |

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