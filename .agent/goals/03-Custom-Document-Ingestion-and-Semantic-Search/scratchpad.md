# Goal 03: Custom Document Ingestion and Semantic Search

Status: 🟡 In Progress  
Priority: High  
Owner: You + me  
Last Updated: 2026-01-25

## Objective

Enable users to ingest **custom documents** (e.g., case files, briefs, contracts, notes) into the Legal-MCP vector store and semantically search them with filter capabilities.

At the same time, keep German federal law ingestion and LLM-based “answering” out of the MCP tool surface:
- German law ingestion remains a **server/library development concern** (CLI/scripts), not an end-user tool.
- LLM answer synthesis should be done by client-side LLM agents using retrieval primitives from this server.

## Task Directories

- Task 01: Remove German law ingestion tool + remove LLM Q&A tool  
  `legal-mcp/.agent/goals/03-Custom-Document-Ingestion-and-Semantic-Search/Task-01-Remove-German-Law-Ingestion-Tool/`

- Task 02: Custom document ingestion (async goal; current implementation is synchronous)  
  `legal-mcp/.agent/goals/03-Custom-Document-Ingestion-and-Semantic-Search/Task-02-Custom-Document-Ingestion-Async/`

- Task 03: Semantic search custom docs with filters  
  `legal-mcp/.agent/goals/03-Custom-Document-Ingestion-and-Semantic-Search/Task-03-Semantic-Search-Custom-Docs-With-Filters/`

- Task 04: Tests, docs, and live validation  
  `legal-mcp/.agent/goals/03-Custom-Document-Ingestion-and-Semantic-Search/Task-04-Tests-Docs-And-Live-Validation/`

- Task 05: Ingest Markdown files from allowlisted directory (implemented, default root support added)  
  `legal-mcp/.agent/goals/03-Custom-Document-Ingestion-and-Semantic-Search/Task-05-Ingest-Markdown-Files/`

## Why

- German federal laws ingestion is operationally heavy and not something a chat client should trigger.
- The core user value is: “Given my case file(s), retrieve relevant items with citations/snippets.”
- LLM-based “answering” belongs in the agent layer; the server’s job is retrieval primitives + stable contracts.
- We also need a safe, isolated “workspace” model so custom documents don’t leak across users/sessions.

## Current State Summary (What’s Done)

### Tool surface (done)
- `ingest_german_laws` is removed from MCP registration and server instructions.
- `ask_legal_question` is removed from MCP registration and server instructions (LLM-powered tool removed).
- Custom documents tools exist and are registered:
  - `ingest_documents` (text ingestion)
  - `search_documents` (semantic search with filters)
  - `ingest_markdown_files` (ingest markdown from disk under allowlisted root)

### Custom documents storage + retrieval (done)
- Storage collection: `custom_documents` (separate from `german_laws`)
- Mandatory isolation: `tenant_id` required; optional scoping by `case_id`
- Deterministic chunking and IDs:
  - deterministic `document_id` if not provided
  - deterministic `chunk_id` as `{document_id}:{chunk_index}`
- Safe, structured responses: no raw document content in errors

### File-based markdown ingestion (done)
- `ingest_markdown_files` reads `.md`/`.markdown` under an allowlisted root with strict traversal protection.
- Default allowlisted root when env var is unset:
  - `{worktree_root}/.agent/tmp` (using server cwd as `{worktree_root}`)

### Tests (done)
- Added traversal/allowlist tests for file ingestion safety.
- All tests were passing after changes (exact command logs are in task scratchpads).

## Remaining Work (Next Steps)

### 1) True async ingestion jobs (not done)
We intended submit→background→poll ingestion, but the cache decorator in this codebase does not support an async timeout parameter.
Next step options:
- Add explicit submit/status/result tools for ingestion jobs, or
- Extend cache layer to support non-blocking background execution.

### 2) PDF ingestion workflow (not done)
We successfully converted PDFs to text/markdown in-session and ingested the extracted text, but no markdown files are auto-written.
If MarkItDown MCP cannot write files, next step is to add a Legal-MCP tool that:
- reads PDFs from allowlisted root
- converts to markdown/text using a library dependency
- ingests into custom docs

### 3) Task 04 (docs + live validation) (not done)
Update user-facing docs (`TOOLS.md`/README as appropriate) and capture a repeatable live validation checklist.

## Success Criteria (Updated Acceptance Checklist)

### A) Tool surface
- [x] `ingest_german_laws` is **not registered** as an MCP tool and is removed from tool instructions.
- [x] LLM-based `ask_legal_question` is **not registered** as an MCP tool and is removed from tool instructions.
- [x] Semantic search exists for custom documents with filter capabilities.
- [x] Custom document ingestion exists (text).
- [x] Markdown file ingestion exists (allowlisted root).
- [ ] Custom document ingestion is a true async job (submit + poll) for large ingestions.

### B) Behavior
- [x] Ingesting custom text creates embeddings and persists them in `custom_documents`.
- [x] Searching custom documents returns:
  - document identifiers
  - chunk identifiers
  - similarity score
  - metadata (tenant_id, case_id, source_name, ingested_at, tags fields)
  - bounded excerpts/snippets
- [x] Filter capabilities exist (tenant_id required; optional case_id/document_id/source_name/tag).

### C) Safety and isolation
- [x] No sensitive data is logged by the ingestion/search pipeline (errors are bounded/truncated).
- [x] Tenant isolation is enforced via required `tenant_id`.
- [x] Stored data is scoped to the tenant filter and not returned across tenants.
- [x] File ingestion reads only under allowlisted root, blocks traversal and symlink escape.

### D) Quality gates
- [x] Lint passes (`ruff check` + `ruff format`) during implementation.
- [x] Tests pass (`pytest`) during implementation; new tests added for file ingestion safety.
- [ ] Documentation updates and final live validation checklist (Task 04) completed.

## Architecture Decisions (Final)

### Storage strategy
- Separate Chroma collection for custom docs: `custom_documents`.
- Mandatory metadata per chunk includes:
  - `tenant_id` (required)
  - `case_id` (optional)
  - `document_id`, `chunk_id`, `source_name`, `ingested_at`
  - `tags_csv` plus `tag` (single-token optimization) to support simple filtering

### Chunking strategy
- Deterministic character-based chunking with conservative defaults.
- Token-aware chunking can be added later without breaking the ingestion API shape.

### File ingestion safety
- Default allowlisted root: `{worktree_root}/.agent/tmp`
- Optional override: `LEGAL_MCP_INGEST_ROOT`
- Reject absolute paths, `..` traversal, and symlink escapes.

## Manual testing plan (current)
1. Restart server in Zed.
2. Ingest a short sample case file via `ingest_documents(tenant_id, case_id, documents=[...])`.
3. Search via `search_documents(query, tenant_id, case_id, ...)`.
4. Place markdown under `{worktree_root}/.agent/tmp/<case>/...` and ingest via `ingest_markdown_files(...)`.
5. Confirm no cross-tenant leakage by searching with a different tenant_id.