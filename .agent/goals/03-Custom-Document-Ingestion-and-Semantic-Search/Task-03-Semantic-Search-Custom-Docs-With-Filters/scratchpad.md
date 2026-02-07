# Task 03: Semantic Search for Custom Documents with Filters

Status: 🟡 In Progress  
Priority: P1 (High)  
Goal: [03-Custom-Document-Ingestion-and-Semantic-Search](../scratchpad.md)  
Last Updated: 2026-01-25

## Objective

Implement a semantic search tool for **custom user-ingested documents** that supports **mandatory tenant isolation** and practical **filter capabilities**.

This task focuses on query-time retrieval only. Ingestion mechanics (chunking, embedding, persistence) are handled in Task 02.

## Success Criteria

- [x] Provide a tool function `search_documents` for semantic search over custom documents.
- [x] `tenant_id` is a **required** input and is **enforced** in the filter (no default tenant).
- [x] Optional filters work:
  - [x] `case_id` (exact match)
  - [x] `document_id` (exact match)
  - [x] `source_name` (exact match)
  - [x] `tag` (single token, exact match)
- [x] Tool returns ranked matches with:
  - [x] `chunk_id` and `document_id`
  - [x] `similarity`
  - [x] `excerpt` (bounded length; no full-doc returns by default)
  - [x] metadata including `tenant_id`, `case_id`, `source_name`, `ingested_at`, and tag fields
- [x] Result format is stable and suitable for downstream RAG (citations by `document_id` + `chunk_id`).
- [x] No sensitive content is logged.
- [ ] Lint/test gates pass in Task 04.

## Constraints / Notes

### Tenant isolation is non-negotiable
All searches must include `tenant_id` filtering in the vector store query. Never perform an unscoped search across all custom documents.

### Filter semantics: keep it simple
Chroma metadata filtering supports equality and boolean combinations but can be awkward for rich queries. Start with simple `"$eq"` constraints and a bounded `"$and"` list, mirroring the approach used by German law search.

We enforce tenant isolation by always including `{"tenant_id": {"$eq": tenant_id}}` in the filter builder.

### Tags: avoid complicated list membership queries initially
Chroma does not support “contains” queries over CSV strings. For v1 we implement a pragmatic filter:
- store `tags_csv` as a normalized CSV string (sorted, lowercased, unique) for debugging/inspection
- additionally store a single-token field `tag` *only when exactly one tag is provided* so equality filters work

As a result, `tag` filtering in v1 is best-effort and is primarily intended for the common “one tag per ingestion batch” workflow.

## Implemented Public API

### Tool: `search_documents`
Inputs (implemented):
- `query: str` (min length 2)
- `tenant_id: str` (required; isolation boundary)
- `case_id: str | None = None` (optional scoping)
- `n_results: int = 10` (1–50)
- `document_id: str | None = None` (exact match)
- `source_name: str | None = None` (exact match)
- `tag: str | None = None` (single tag token; best-effort v1)
- `excerpt_chars: int = 500`

Output (implemented):
- `query`
- `filters` (echo inputs for debugging)
- `count`
- `results: list[dict]` where each dict includes:
  - `chunk_id`
  - `document_id`
  - `similarity`
  - `excerpt`
  - `metadata` (subset) including: `tenant_id`, `case_id`, `source_name`, `ingested_at`, `tags_csv`, `tag`

### Pydantic model(s)
- `SearchDocumentsInput` for validation and tool schema generation.

## Implementation Plan

- [ ] Identify or create the custom document embedding store abstraction (expected from Task 02).
  - Ensure it has a `search(query, n_results, where=...)` method returning results with `content`, `similarity`, `metadata`.
- [ ] Add `SearchDocumentsInput` model with validations:
  - `query` length bounds
  - `namespace` required and non-empty
  - `n_results` bounds
- [ ] Build a Chroma `where` filter ensuring:
  - Always includes `{"namespace": {"$eq": namespace}}`
  - Adds optional constraints using `"$and"` if >1 condition.
- [ ] Add excerpt truncation in the tool response (e.g., 500 chars), returning `excerpt` instead of full `content`.
- [ ] Ensure robust error handling and structured error returns:
  - `{"error": "...", "message": "...", "query": ..., "filters": ...}`

## Files to Modify (Expected)

- `legal-mcp/app/tools/`:
  - Add a new module: `custom_documents.py` (preferred), or extend an existing module carefully.
  - Export factory function `create_search_documents(cache)` from `legal-mcp/app/tools/__init__.py`.
- `legal-mcp/app/server.py`:
  - Register new tool and update instructions (once Task 01 removes `ingest_german_laws` and Task 02 adds ingestion tool).
- `legal-mcp/tests/`:
  - Add tests in Task 04, but this task should define expected behavior and edge cases.

## Test Strategy (implemented in Task 04, defined here)

Core behavior tests:
- Searching within a namespace returns results from that namespace only.
- Searching with `document_id` filter returns only chunks from that document.
- Searching with `source_name` filter narrows results appropriately.
- Searching with `tag` filter works with the chosen storage strategy (`tags_csv` exact match).

Edge cases:
- Empty/too-short query rejected (Pydantic validation).
- Namespace missing rejected.
- No matches returns `count=0` and empty `results`.
- Large content returns truncated `excerpt` only.

## Manual Verification Plan (Live Chat after restart)

1. Ingest two documents into `tenant_id="t_demo"` with `case_id="c_001"` and one into `tenant_id="t_demo"` with `case_id="c_002"`.
2. Search for a phrase unique to `case_id="c_001"`:
   - Expect results only from `case_id="c_001"` when that filter is supplied.
3. Search with only `tenant_id="t_demo"`:
   - Expect results from both cases (still tenant-isolated).
4. Search with `document_id` and verify it narrows to the correct doc.
5. Add a single tag on ingestion (e.g., `tags=["medical"]`) and verify `tag="medical"` filter works.

## Dependencies

- Depends on Task 02 providing:
  - custom documents storage/collection (`custom_documents`)
  - deterministic `document_id` + `chunk_id` metadata
  - embeddings configured (prefer TEI when enabled)
  - tenant/case metadata stored per chunk (`tenant_id` required, `case_id` optional)

## Open Questions

- Do we need multi-field filtering (e.g., date range)? If yes, define the metadata format and confirm Chroma filter support.
- Do we want to support robust multi-tag queries (`tags_any` / `tags_all`)? Likely later; current v1 uses a pragmatic `tag` equality field.
- Do we want to expose an explicit “namespace” string in the tool API, or keep `tenant_id`/`case_id` as separate fields (current implementation uses separate fields for enforceable isolation)?

## Progress Log

- 2026-01-25: Task created and scoped.
- 2026-01-25: Implemented `search_documents` tool with enforced `tenant_id` isolation and optional filters (`case_id`, `document_id`, `source_name`, `tag`). Registered in `app/server.py`.