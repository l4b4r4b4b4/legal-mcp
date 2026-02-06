# Task 05: Ingest Markdown Files from Allowlisted Directory

Status: 🟢 Complete  
Priority: P1 (High)  
Goal: [03-Custom-Document-Ingestion-and-Semantic-Search](../scratchpad.md)  
Last Updated: 2026-01-25

## Objective

Add a new MCP tool that can ingest **Markdown files** from disk into the custom
documents vector store, without requiring the user/agent to paste or transmit
raw extracted text inline.

This supports the workflow:
1. Convert PDFs → Markdown via an external converter (e.g., MarkItDown MCP).
2. Save Markdown files under a known, allowlisted directory.
3. Call Legal-MCP tool to ingest the Markdown files by path.

## Why

- Keeps Legal-MCP focused on retrieval/storage primitives; PDF parsing remains optional.
- Avoids huge request payloads (copy/paste), reduces timeouts and client friction.
- Enables reproducible “case folder” ingestion workflows.
- Makes it easier to benchmark and iterate on chunking/search behaviors.

## Scope

### In scope
- New MCP tool: `ingest_markdown_files`
- Allowlisted root directory enforcement (security)
- Path normalization and validation (no path traversal)
- Reading `.md` (and optionally `.markdown`) files
- Feeding file contents into existing custom document ingestion pipeline:
  - deterministic chunking
  - metadata (`tenant_id`, optional `case_id`, `source_name`, `document_id`, etc.)
- Structured tool responses (no sensitive data leakage)

### Out of scope (for this task)
- PDF parsing inside Legal-MCP
- Globbing patterns and recursive directory ingestion (v1 can require explicit paths)
- Auth/permissions integration (tenant isolation is enforced by parameters; we can layer auth later)

## Proposed Tool API

### Tool name
`ingest_markdown_files`

### Inputs
- `tenant_id: str` (required; isolation boundary)
- `case_id: str | None` (optional)
- `paths: list[str]` (required; file paths relative to allowlisted root)
- `tags: list[str] | None` (optional; applied to all files)
- `chunking: dict | None` (optional; same shape as text ingestion)

### Output
- `status: "complete" | "failed"`
- `tenant_id`, `case_id`
- `totals`: counts (files_received, files_ingested, chunks_created, chunks_added, errors)
- `files`: per-file summary:
  - `path`
  - `source_name` (derived from file name)
  - `document_id`
  - `chunks_created`, `chunks_added`
  - `errors` (safe, truncated; no raw content)

## Security / Safety Requirements (Hard)

- Only allow reading files under a single allowlisted root directory.
- Disallow:
  - absolute paths
  - `..` traversal segments
  - symlink tricks (resolve real path and ensure it remains under root)
- Do not log file contents.
- Error messages must not include file contents.

## Configuration

### Environment variable
- `LEGAL_MCP_INGEST_ROOT`
  - If set: tool only reads files under this directory.
  - If not set: tool fails fast with a clear error instructing to set it.

Decision (v1): fail-fast when `LEGAL_MCP_INGEST_ROOT` is unset.

## Implementation Plan

### Step 1: Add settings/config
- Extend `app/config.py` Settings with:
  - `ingest_root_path: str | None` (from `LEGAL_MCP_INGEST_ROOT`)
- Validate at startup (or at tool call time) that root exists.

### Step 2: Implement safe path resolver
- New helper function (module-level, testable) to:
  - accept `root: Path` and `relative_path: str`
  - compute `candidate = (root / relative_path).resolve()`
  - ensure:
    - `relative_path` is not absolute
    - `candidate.is_file()`
    - `candidate` is under `root.resolve()`
  - return `candidate` or raise a safe ValueError

### Step 3: Implement ingestion pipeline wrapper
- For each file path:
  - read text as UTF-8 (lossy with `errors="replace"` if needed)
  - call existing `ingest_custom_documents(...)` with:
    - `source_name` = basename
    - `text` = file contents
    - optional metadata: original relative path (safe), file size bytes (optional)
- Aggregate results into a single response.

### Step 4: Add MCP tool factory + server registration
- Add `create_ingest_markdown_files(cache)` in a new module or in `app/tools/custom_documents.py`
- Register in `app/server.py` and add to server instructions under “Custom Document Tools”.

### Step 5: Manual test
- Place a small `.md` file under the allowlisted root.
- Call `ingest_markdown_files(...)`.
- Verify search hits via `search_documents(...)`.

### Step 6: Tests (Task 04 style, but minimal here)
- Unit tests for path validation:
  - rejects absolute
  - rejects `..`
  - rejects outside-root
  - rejects directory path
- Integration-ish test:
  - write a temporary `.md` in a tmp root
  - ingest it
  - search within the same tenant and confirm hit

## Files to Create / Modify (expected)

Create (likely):
- `legal-mcp/app/custom_documents/file_ingestion.py` (safe path + file reading helpers)
- or extend `legal-mcp/app/custom_documents/pipeline.py` with file wrapper functions

Modify:
- `legal-mcp/app/config.py` (new env var setting)
- `legal-mcp/app/tools/custom_documents.py` (new tool factory)
- `legal-mcp/app/tools/__init__.py` (export factory)
- `legal-mcp/app/server.py` (register tool + update instructions)
- `legal-mcp/tests/` (new unit tests)

## Acceptance Criteria

- [x] `ingest_markdown_files` tool exists and is registered in server.
- [x] Tool enforces allowlisted root path and blocks traversal.
- [x] Tool ingests Markdown files into `custom_documents` collection with correct metadata.
- [x] Tool produces safe structured output (no content leakage).
- [x] Lint + tests pass; coverage remains ≥ 73%.
- [ ] Manual live test works after restarting Zed.

## Completion Notes

### What was implemented
- Added a new tool `ingest_markdown_files` that reads Markdown from disk under an allowlisted root path (`LEGAL_MCP_INGEST_ROOT`) and ingests the content via the existing custom document ingestion pipeline.
- Added strict path validation:
  - rejects absolute paths
  - rejects `..` traversal
  - rejects symlink escapes by validating the resolved path remains under the resolved root
  - restricts to Markdown extensions (e.g. `.md`, `.markdown`)
- Added a new config setting `ingest_root_path` (from `LEGAL_MCP_INGEST_ROOT`) with fail-fast behavior when unset/misconfigured.
- Added unit tests covering traversal protection, suffix allowlist enforcement, and symlink escape rejection.

### Verification steps
1. Set `LEGAL_MCP_INGEST_ROOT` to the case directory containing your Markdown files.
2. Restart the server so the environment variable is picked up.
3. Call `ingest_markdown_files(tenant_id=..., case_id=..., paths=[...])` with paths relative to the allowlisted root.
4. Run `search_documents(query=..., tenant_id=..., case_id=...)` to verify hits.
5. Confirm the tool rejects:
   - absolute paths
   - `..` traversal
   - non-Markdown extensions

## Notes / Progress Log

- 2026-01-25: Task created after successful PDF→markdown conversion + text ingestion flow.
  Next step: implement file-based ingestion to avoid manual copy/paste / huge payloads.