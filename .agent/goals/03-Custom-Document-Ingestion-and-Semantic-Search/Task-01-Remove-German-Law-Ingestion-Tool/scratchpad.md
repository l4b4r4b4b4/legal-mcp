# Task 01: Remove German law ingestion from MCP tool surface

Status: 🟢 Complete  
Owner: You + me  
Last Updated: 2026-01-25

## Follow-up (scoped change): Remove LLM-based legal Q&A tool

Status: 🟢 Complete

You approved removing `ask_legal_question` from the MCP tool surface because it requires an LLM behind the server. The MCP server should expose retrieval primitives (search/lookup), and the client-side LLM agent should perform answer synthesis over retrieved norms.

## Context

German federal law ingestion is operationally heavy and not appropriate as an end-user MCP tool. It should remain part of **server development and the library** (CLI/scripts), while MCP exposes tools for querying and for ingesting **custom documents** (case files, briefs, contracts).

This task removes `ingest_german_laws` from the MCP server tool registration and from any user-facing tool instructions/docs exposed through MCP.

## Objective

- Remove `ingest_german_laws` from the registered MCP tools.
- Remove any mention of `ingest_german_laws` from the MCP server instructions so clients won’t see it.
- Keep ingestion capabilities available to developers via library/CLI/scripts (no breaking of internal ingestion modules).

## Success Criteria (Acceptance Checklist)

- [x] `ingest_german_laws` is not registered as an MCP tool.
- [x] MCP server instructions no longer advertise `ingest_german_laws`.
- [x] Server continues to start and existing law tools still work:
  - [x] `search_laws`
  - [x] `get_law_by_id`
  - [x] `get_law_stats`
- [x] No tests relying on `ingest_german_laws` MCP tool remain; tests are updated accordingly.
- [x] Lint + tests pass; coverage stays ≥ 73%.

### Follow-up acceptance (ask_legal_question removal)

- [x] `ask_legal_question` is not registered as an MCP tool.
- [x] MCP server instructions no longer advertise `ask_legal_question`.
- [x] Tests that exercised `ask_legal_question` as a tool are removed or rewritten to validate retrieval primitives instead.
- [x] Lint + tests pass; coverage stays ≥ 73%.

## Plan

### 1) Identify tool registration and instructions to update
- In the server entry point, remove wiring/registration of the ingestion tool:
  - Stop importing the factory for ingestion tool creation.
  - Stop creating the bound tool function.
  - Stop registering it on the FastMCP instance.
- In the server instructions string, remove the bullet/description for German law ingestion.

### 2) Keep ingestion accessible via dev workflows
- Ensure the underlying ingestion pipeline remains reachable via:
  - CLI commands (if present)
  - scripts under `scripts/`
  - direct library usage (imports from `app.ingestion.*`)
- If docs currently recommend using the ingestion MCP tool, adjust those docs to point at dev scripts/CLI instead.

### 3) Update tests
- If there are tests validating tool registration or tool lists:
  - Update snapshots/expectations to reflect removal.
- If there are tests calling the ingestion tool via MCP:
  - Replace with library-level ingestion tests (or remove if redundant).

### 4) Manual validation steps (after implementation)
- Start server.
- Confirm `ingest_german_laws` does not appear in tool list in the client.
- Confirm `get_law_stats` and `search_laws` still operate.

## Files Modified

- `legal-mcp/app/server.py`
  - Removed import/binding/registration of `ingest_german_laws`
  - Removed the ingestion bullet from MCP instructions
- `legal-mcp/app/tools/__init__.py`
  - Removed export of `create_ingest_german_laws` from the public tools package surface
- `legal-mcp/app/tools/german_laws.py`
  - Clarified in module docstring that federal ingestion is dev-only
  - Removed `create_ingest_german_laws` from `__all__` exports (kept implementation intact)

## Risks / Tradeoffs

- Some local workflows may have been relying on triggering ingestion from MCP. This will stop working by design.
- If any docs/tutorials reference ingestion as a tool, they will become misleading unless updated.
- If the ingestion tool is currently used for “demo bootstrap,” we should provide a clear dev command/script alternative.

## Notes / Decisions

- This task intentionally **does not** change the ingestion implementation itself.
- This task intentionally **does not** add custom document ingestion; that’s handled in Task 02.
- Observed: filtering on law abbreviations may be case-sensitive in semantic search; normalization is separate but should be addressed in Task 03 or adjacent work.

## Test Strategy

- Unit/registration tests:
  - Verify tool registration excludes `ingest_german_laws`.
- Smoke tests (manual/live):
  - Call `health_check`, `get_law_stats`, `search_laws`, `get_law_by_id`.

## Completion Notes

- What changed:
  - [x] Removed `ingest_german_laws` from MCP tool registration and instructions.
  - [x] Removed ingestion tool factory from public tool exports while keeping ingestion code available for dev workflows.
- Commands run:
  - [x] `ruff check . --fix --unsafe-fixes && ruff format .`
  - [x] `pytest` (121 passed)
- Manual validation:
  - [ ] Pending: restart Zed server and confirm tool list does not include `ingest_german_laws`.