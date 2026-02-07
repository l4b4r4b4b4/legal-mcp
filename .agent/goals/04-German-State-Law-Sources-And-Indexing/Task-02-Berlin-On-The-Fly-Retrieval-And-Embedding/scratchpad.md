# Task 02: Berlin On-The-Fly Retrieval & Embedding (and Sitemap Catalog Tool)

Status: 🟡 In Progress  
Goal: [Goal 04 — German State Law Sources & Indexing](../scratchpad.md)  
Last Updated: 2026-01-25

## 0) Context

We have validated that `gesetze.berlin.de` is a SPA and requires JavaScript rendering for `/bsbe/document/<id>` content. Playwright-based extraction works for both:
- `jlr-...` (Landesnorm / norms)
- `NJRE...` (decisions)

We also confirmed a policy constraint:
- `robots.txt` disallows automated crawling for generic user agents (`User-agent: *` → `Disallow: /`), even though a sitemap is provided.

This task focuses on a **minimal functional** path to make Berlin support usable in the MCP server **without** implementing a bulk crawler, and to keep request volume bounded and user-initiated.

## 1) Objectives

### Primary Objective (Minimal Functional State)
Provide Berlin capabilities in MCP that enable:
- Users/agents to discover which Berlin documents exist (IDs + URLs) **without** fetching document pages.
- Users/agents to retrieve a **single** document on demand (bounded) and optionally embed it to grow a local Berlin subset incrementally.

### Secondary Objective
Ensure everything is documented, testable, and wired into the MCP server ergonomically with clear safety constraints.

## 2) Key Constraints & Decisions

### Constraints
- **No bulk crawling** on behalf of users/agents under a generic UA due to `robots.txt` constraints.
- Keep requests low-volume/polite; avoid storing or logging sensitive session data (cookies/tokens).
- Avoid heavy implicit work on server startup (no Playwright runs at startup).

### Architectural Direction
- Use a **generic, offline catalog** for discovery (not Berlin-specific).
- **Catalog metadata is preloaded** from a **repo-committed SQLite database** (IDs + canonical URLs + type prefix), queried locally at runtime.
- **Document content retrieval remains on-demand** (Playwright-backed initially) only when explicitly requested.
- Prefer storing large retrieved payloads by reference (RefCache) to keep tool outputs small.

## 3) Approved Work: Generic Offline Catalog (Repo-Committed SQLite) + Berlin Source

### Summary
Implement a **generic** MCP catalog tool (not Berlin-specific) backed by a **repo-committed SQLite database** containing “what exists” metadata (IDs + canonical URLs + derived type prefix). Berlin (`gesetze.berlin.de`, portal `bsbe`) is the first registered source.

Document content retrieval remains **on-demand** (lazy), bounded, and user-initiated.

### Tool: `list_available_documents` (approved direction)
**Purpose:** Expose a catalog of available document IDs and canonical URLs from a bundled, offline catalog store.

**Inputs (proposed):**
- `source: str`  
  - Example: `"de-state-berlin-bsbe"`
- `prefix: str | None`  
  - Source-specific prefix filter. For Berlin: `"jlr"` (or `"jlr-"`) and `"NJRE"`.
- `offset: int` (default 0)
- `limit: int` (default 50, max 200)

**Outputs (proposed):**
- `source`: which catalog source was used
- `catalog_version`: version identifier for the bundled catalog (e.g., build timestamp or schema version)
- `count_total`: total docs in the catalog for that source
- `count_filtered`: count after filter
- `prefix_counts`: counts by type (jlr, NJRE, other)
- `items`: list of:
  - `document_id`
  - `canonical_url`
  - `document_type_prefix` (derived: `jlr`, `NJRE`, `other`)

**Notes / non-goals:**
- Do **not** fetch document pages.
- Do **not** attempt to enrich with titles/courts/dates (not available from sitemap snapshot without loading pages).
- This is “what exists”, not “what we’ve ingested”.

### Catalog build/update workflow (approved)
- **Dev-time/manual**: fetch sitemap(s) to build/update a discovery snapshot (network IO, explicit operator action).
- **Offline build step**: convert snapshot → SQLite catalog database.
- **Runtime**: MCP server loads/validates SQLite at startup and serves catalog queries **offline**.

### Compatibility
- Keep `berlin_list_available_documents` as a compatibility alias (optional) mapped to `list_available_documents(source="de-state-berlin-bsbe", ...)` to avoid breaking existing agents.

### Rationale
- Provides immediate, deterministic discovery without runtime network IO.
- Keeps content retrieval bounded and user-initiated (lazy).
- Scales better than large JSON at startup (SQLite index + query), and avoids loading all rows into memory.

## 4) Planned Work (Next Steps in This Task)

### 4.1 Implement the Generic Catalog Tool + SQLite Store (MCP)
TODO:
- [ ] Add SQLite-backed catalog store (offline at runtime) and a registry of sources.
- [ ] Add generic MCP tool `list_available_documents` (inputs: `source`, `prefix`, `offset`, `limit`).
- [ ] Register Berlin source (`de-state-berlin-bsbe`) backed by a repo-committed SQLite catalog DB.
- [ ] Add strict input validation for `limit`, `offset`, `prefix`, and `source`.
- [ ] Add tests for:
  - deterministic ordering + pagination
  - prefix filtering correctness (Berlin: jlr vs NJRE)
  - missing/corrupt catalog DB returns structured error
- [ ] Ensure tool is documented in server tool listing (description includes safety/limitations).
- [ ] (Optional) Keep `berlin_list_available_documents` as a compatibility alias.

### 4.2 On-the-Fly Retrieval Tool (future, same Task)
(Proposed; not yet implemented in this scratchpad’s approved scope)
- `berlin_get_document`:
  - input: `document_id` or canonical URL
  - Playwright render + safe extraction (reuse existing extraction logic)
  - returns metadata + text; store large payloads by reference
  - strict timeouts; caps on output size
- `berlin_ingest_document` (optional):
  - retrieval + chunk + embed + upsert into Berlin collection
  - idempotent key: `gesetze-berlin:<document_id>`
  - allows incremental local corpus expansion

## 5) Files / Artifacts Involved

### Existing scripts (R&D tooling, not production ingestion)
- `scripts/berlin/extract_from_sitemap_playwright.py` — bounded Playwright extraction
- `scripts/berlin/normalize_playwright_batch.py` — normalize batch JSONL into ingestion-ready JSONL
- `scripts/berlin_portal_discovery.py` — generates local discovery snapshots (sitemap-based)

### Seed artifacts (examples already exist)
- Discovery snapshots:
  - `data/raw/de-state/berlin/discovery/berlin_sitemap_discovery_mixed_sample_20260125.json`
- Batch artifacts:
  - `data/raw/de-state/berlin/playwright-batch/berlin_playwright_batch_*.jsonl`

## 6) Safety Checklist (Hard Requirements)
- [ ] No cookies/tokens logged or stored.
- [ ] No automatic bulk crawling behavior from MCP tools.
- [ ] Catalog tool reads local files only by default.
- [ ] Clear documentation: catalog is “what exists”, not “what we’ve ingested”.
- [ ] Deterministic output ordering and pagination semantics.

## 7) Acceptance Criteria

### For the Sitemap Catalog Tool
- [ ] MCP tool returns a paginated list of `document_id` + `canonical_url`.
- [ ] Filtering by prefix works.
- [ ] Tool returns counts and the snapshot path used.
- [ ] Tool does not perform any network IO.
- [ ] Tests cover pagination/filtering/error paths.

## 8) Session Handoff Prompt (next session)

Continue Task 02 by implementing the Berlin sitemap catalog MCP tool.

Context: See this scratchpad and Goal 04 scratchpad for Berlin portal constraints and prior artifacts. Robots policy discourages bulk crawling; approved approach is local snapshot-based catalog + on-demand retrieval.

What Was Done:
- Documented approved plan for `berlin_list_available_documents` MCP tool.
- Defined inputs/outputs, constraints, acceptance criteria, and test strategy.

Current Task:
- Locate MCP server entrypoint and existing tool registration.
- Implement `berlin_list_available_documents` reading latest snapshot in `data/raw/de-state/berlin/discovery/`.
- Add unit tests for pagination/filtering and structured error handling.

Guidelines:
- Keep volume low and avoid network IO in the catalog tool.
- Do not log cookies/tokens (not applicable here but keep consistent discipline).
- Keep changes small and reviewable; follow existing MCP tool patterns.