# Task 02: Custom Document Ingestion (Async)

Status: 🟡 In Progress  
Owner: You + me  
Last Updated: 2026-01-25

## Goal Link

- Goal: `03-Custom-Document-Ingestion-and-Semantic-Search`
- Goal scratchpad: `legal-mcp/.agent/goals/03-Custom-Document-Ingestion-and-Semantic-Search/scratchpad.md`

## Objective

Implement an ingestion tool for **custom, user-provided documents** (e.g., case files) that:

- ingests plain text documents into a dedicated vector store/collection
- enforces **isolation** (tenant-scoped; optional case scoping)
- supports a **polling-based** workflow for large results via caching

Important:
- We start with **text-only ingestion**. PDF extraction will be a follow-up (client-side extraction or a dedicated extractor tool), feeding extracted text into the same ingestion pipeline.

This task covers ingestion (payload validation → chunking → embedding → persistence → result structure). Search is handled in Task 03.

## Scope

### In scope
- New tool factory: `create_ingest_documents(...)`
- New Pydantic input models for ingestion payload
- Chunking and metadata strategy for custom docs
- Persist embeddings to ChromaDB in a separate collection from `german_laws`
- Deterministic identifiers: `document_id`, `chunk_id`

### Out of scope (handled elsewhere)
- Semantic search tool for custom docs (Task 03)
- PDF/DOCX parsing/OCR (text-only for now)
- Auth/permission wiring (mcp-refcache actor/permission system) — to be layered on top
- Reranking

## Success Criteria

- [x] A new MCP tool exists for custom ingestion: `ingest_documents`.
- [x] Isolation inputs exist and are stored on every chunk:
  - [x] `tenant_id` (required)
  - [x] `case_id` (optional)
- [x] Ingestion writes vectors into a dedicated custom-docs collection (not the federal-law corpus): `custom_documents`.
- [x] Result includes:
  - [x] `status` (`complete`/`failed`)
  - [x] totals: documents received/ingested, chunks created/added, error count
  - [x] per-document summaries (document_id, source_name, chunks created/added, errors)
  - [x] `tenant_id` and `case_id` echoed back
- [x] No sensitive document content is logged or returned in errors.
- [x] Public APIs have docstrings with examples.

## Implementation Notes (what was actually built)

### Storage module
- Added `app/custom_documents/embeddings.py`:
  - `CustomDocumentEmbeddingStore` using ChromaDB persistent storage
  - Collection: `custom_documents`
  - Uses TEI embeddings when configured (`USE_TEI=true`), otherwise local model manager
  - Metadata encoding is Chroma-compatible (str/int/float/bool; lists encoded)

### Ingestion pipeline
- Added `app/custom_documents/pipeline.py`:
  - Deterministic character chunking (`chunk_text_deterministic`)
  - Deterministic document IDs when caller doesn’t provide one (`doc_<sha256prefix>`)
  - Per-chunk metadata includes: `tenant_id`, `case_id`, `document_id`, `chunk_id`, `source_name`, `ingested_at`, `tags_csv`, `tag` (single-token optimization)
  - Returns safe structured results (no raw text)

### MCP tool
- Added `app/tools/custom_documents.py`:
  - `create_ingest_documents(cache)` tool factory (cached)
  - Input schema models: `IngestDocumentItem`, `IngestDocumentsInput`, `IngestChunkingOptions`
  - Registered in `app/server.py` and exported via `app/tools/__init__.py`

## Async / job model (current limitation)

This task originally targeted “true async jobs” (submit → immediate return → background processing).

Current reality:
- The cache decorator in this codebase does **not** accept an `async_timeout` parameter.
- Therefore, `ingest_documents` currently runs inline and may still take time for large ingestions.

What we still get today:
- Results are cached and may be returned as a `ref_id` depending on size/config, and can be retrieved/paginated via `get_cached_result(ref_id=...)`.

Recommended follow-up to meet the original requirement:
- Implement an explicit submit/status API:
  - `ingest_documents_submit(...)` → returns `job_id`/`ref_id` immediately
  - `ingest_documents_status(job_id)` / `ingest_documents_result(job_id)`
- Or extend the caching layer to support a non-blocking job mode for long tasks.

## Data model updates vs original plan

- Replaced `namespace` with explicit isolation fields:
  - `tenant_id` (required, hard isolation boundary)
  - `case_id` (optional, search narrowing)
This matches the desired scheme like `tenant:<t_id>` + `case:<id>` while keeping filtering correct and enforceable.

## Files Created / Modified

Created:
- `legal-mcp/app/custom_documents/embeddings.py`
- `legal-mcp/app/custom_documents/pipeline.py`
- `legal-mcp/app/tools/custom_documents.py`

Modified:
- `legal-mcp/app/tools/__init__.py` (export tool factories/models)
- `legal-mcp/app/server.py` (register tools + update instructions)

## Edge Cases / Safety Checks (implemented)

- Empty/whitespace text is rejected per-document (no ingestion).
- Chunking parameter validation prevents invalid overlap/size.
- Metadata is restricted to shallow string maps.
- Error messages are truncated and never include raw document content.

## Notes / Progress Log

- 2026-01-25: Implemented custom document ingestion pipeline + embedding store + MCP tools. Tests and linting pass. Identified that true async job semantics require a follow-up (explicit submit/status API or cache-layer support).
