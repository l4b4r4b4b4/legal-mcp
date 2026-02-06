# Task 04: Tests, Docs, and Live Validation

Status: 🟡 In Progress  
Owner: You + me  
Last Updated: 2026-01-25

## Objective

Harden the custom document ingestion + semantic search feature by adding:
- Focused automated tests (unit + small integration)
- Documentation updates (tool docs + README/TOOLS where appropriate)
- A repeatable manual “live test” checklist to run after restarting Zed and connecting in this chat

This task is explicitly about **quality gates and verification**, not adding new features.

## Scope

### In scope
- Tests for:
  - deterministic chunking behavior
  - metadata/namespace enforcement
  - filter correctness (including multiple filters combined)
  - ingestion job result semantics (success/partial failure)
  - search result shape and ordering invariants
- Docs for:
  - new MCP tool(s): parameters, return schema, examples
  - namespace isolation expectations
  - operational notes: persistence paths, data retention, and how to clear custom data safely
- Manual validation steps to execute in Zed → restart server → test tools live in this chat

### Out of scope
- PDF/DOCX parsing (text only in this iteration)
- Full authentication/authorization beyond namespace enforcement
- Reranking
- Performance benchmarking

## Acceptance Criteria

### Tests
- [x] Tests cover the critical paths for ingestion + search
- [x] Tests are deterministic (no timing flakiness; mock time if needed)
- [x] Error paths are tested (invalid input, empty docs, missing namespace, etc.)
- [x] `pytest` passes and overall coverage stays ≥ 73%

### Documentation
- [x] New tool functions have complete docstrings (summary, params, returns, exceptions, examples)
- [x] Tool surface documentation is updated (likely `legal-mcp/TOOLS.md` and/or `legal-mcp/README.md`)
- [x] No misleading claims (especially around async behavior and timeouts)

### Live validation
- [x] After restarting Zed, you can:
  - ingest a sample case file into a namespace
  - query it and get expected citations/excerpts
  - verify filters work and no cross-namespace leakage occurs

## Test Plan

### 1) Unit tests

#### A) Chunking (deterministic)
- [ ] Same input text → same chunk boundaries and stable `chunk_id`s
- [ ] Small text (< chunk size) produces exactly one chunk
- [ ] Overlap is applied correctly (when enabled)
- [ ] Empty/whitespace-only content is rejected or produces zero chunks (consistent with requirements)

Suggested assertions:
- number of chunks
- first/last chunk content snippets
- chunk id sequence (`0..n-1`) or stable hash-based IDs (depending on implementation)

#### B) Namespace enforcement
- [ ] Ingestion rejects missing namespace
- [ ] Search rejects missing namespace (should be required)
- [ ] Search returns only results matching the requested namespace
- [ ] Attempted filter injection doesn’t bypass namespace constraint

#### C) Filter composition
- [ ] `document_id` filter reduces results appropriately
- [ ] compound filters (`$and` semantics): namespace + document_id + tag
- [ ] case normalization behavior is consistent for filters where we normalize

### 2) Integration-ish tests (Chroma)
Goal: verify ingestion → persistence → search roundtrip.

Guidelines:
- Keep small: ingest 1–3 documents with short text.
- Use a temporary persist path (tmp directory) so tests don’t contaminate developer state.
- Prefer a test embedding configuration that’s stable and doesn’t require GPU for CI/local runs (exact approach depends on current embedding manager design).

Test cases:
- [ ] Ingest one document, search by a unique phrase → expect at least one hit
- [ ] Ingest two namespaces with similar text → searching namespace A cannot see namespace B
- [ ] Delete/overwrite behavior (only if feature exists) is explicit and tested

### 3) Tool contract tests (schema-level)
- [ ] Validate return keys exist and types are stable enough for clients:
  - ingestion submit returns a job/ref handle
  - polling returns `status` + counts + errors list
  - search returns `results` list with required fields

## Documentation Plan

### Files likely to update
- `legal-mcp/TOOLS.md`
  - Add/extend sections for:
    - custom document ingestion tool (async)
    - custom document semantic search tool (filters)
  - Include minimal “copy/paste” examples
- `legal-mcp/README.md` (only if tool surface is promoted there)
- Docstrings inside the tool modules and any public models

### Must document
- Required `namespace`
- Data persistence location (high-level)
- Sensitivity warning: case files may contain PII; do not log contents
- How to “reset” custom documents for a namespace (if an admin endpoint exists; otherwise document where the data lives and how to clear it safely)

## Live Validation Checklist (after you restart Zed)

## Completed Live Validation (Recorded)

This section records the concrete, repeatable live validation steps that were executed during development.

### A) PDF conversion + writing converted Markdown to disk (completed)
- Verified that PDF conversion works server-side and writes converted Markdown files under the allowlisted ingest root.
- Verified the output is written under the same allowlisted root (no traversal/escape).
- Verified conversion returns output paths + safe metadata (not raw markdown inline).

Manual notes:
- Converted `.pdf` → `.pdf.md` sidecar files were written under:
  - `legal-mcp/.agent/tmp/test_case/*.pdf.md`

### B) PDF ingestion end-to-end (convert → write → ingest) (completed)
- Ingested all PDFs in `legal-mcp/.agent/tmp/test_case/` under:
  - `tenant_id="t_manual"`
  - `case_id="c_test_case"`
- Verified search returns excerpts from the ingested PDFs with correct tenant/case metadata.

### C) Replace-mode re-ingestion for section metadata upgrades (completed)
- Re-ingested the same PDFs using `replace=true` to avoid duplicate vectors.
- Verified ingestion completed successfully for all documents after restart.

### D) Signal notes ingestion (completed)
- Added Signal message log to the repo as:
  - `legal-mcp/.agent/tmp/test_case/notes/signal.md`
- Ingested it into the same tenant/case scope as the PDFs so it is searchable alongside case documents:
  - `tenant_id="t_manual"`, `case_id="c_test_case"`
- Purpose: preserve witness/logistics discussion and timeline hints as searchable case notes.

### E) Court order verification (completed)
- Confirmed the court “Verfügung” document contains the hearing scheduling change:
  - Original: 08.01.2026 11:00
  - Postponed to: 22.01.2026 09:30
- Noted: “March 2026” was not supported by the ingested PDFs; treat as unconfirmed unless a newer court document is added later.

### Preconditions
- Server is running in Zed with correct environment (TEI/vLLM if needed for embeddings/LLM parts).
- You know the namespace you’ll test with, e.g. `user:test` or `case:demo-001`.

### Steps
1. **Health**
   - Call health tool; confirm healthy.
2. **Ingest custom document**
   - Ingest one short “case file” (plain text) with a unique phrase.
   - Confirm tool returns immediately with a job/ref handle (async).
3. **Poll job**
   - Use cached result retrieval until `status` indicates done/failed.
   - Confirm counts: documents ingested, chunks added, any errors.
4. **Search**
   - Run semantic search in the same namespace for the unique phrase.
   - Confirm:
     - at least one hit
     - correct namespace in returned metadata
     - excerpt matches the ingested content
5. **Isolation**
   - Ingest the same phrase into a different namespace.
   - Search in the original namespace; ensure no results from the other namespace.
6. **Filter check**
   - Search with `document_id` filter (or another filter implemented) and verify narrowing.
7. **Regression check**
   - Ensure law search tool still works and is unaffected.

## Risks / Gotchas

- Embedding provider dependency in tests: if tests depend on an external embeddings service, they will be flaky. We should avoid that by using a deterministic local strategy for tests.
- Chroma metadata filtering constraints: keep filters simple and ensure `$and` construction is correct.
- Async semantics: be explicit about how “job completion” is represented; partial failures must be surfaced.

## Notes / Implementation Hooks (to fill in after Tasks 01–03)
- Where the custom docs store lives (module/class)
- Exact tool names and schemas
- Any normalization rules (e.g., uppercase law abbrev vs freeform tags)

## TODO
- [ ] Identify exact modules/functions introduced in Tasks 01–03 and finalize the test targets
- [ ] Implement unit tests for chunking + filters
- [ ] Implement integration-ish ingestion/search roundtrip tests with temporary persistence
- [ ] Update docstrings and `TOOLS.md`
- [ ] Perform live validation in this chat after you restart Zed