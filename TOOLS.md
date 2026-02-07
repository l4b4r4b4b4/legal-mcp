# Legal-MCP - Available Tools

This document describes all MCP tools available in the Legal-MCP server.

Note: This server exposes **retrieval and ingestion primitives only**. LLM-based “answering” is expected to happen in the client/agent layer.

## Quick Reference

| Tool | Description | Caching |
|------|-------------|---------|
| `hello` | Simple greeting tool | No |
| `generate_items` | Generate a list of items | Yes (public namespace) |
| `store_secret` | Store a secret value | Yes (user namespace) |
| `compute_with_secret` | Compute with a secret without revealing it | No |
| `get_cached_result` | Retrieve or paginate cached results | N/A |
| `health_check` | Check server health status | No |
| `enable_test_context` | Enable/disable test context mode | No |
| `set_test_context` | Set test context values | No |
| `reset_test_context` | Reset test context to defaults | No |
| `get_trace_info` | Get Langfuse tracing status | No |
| `ingest_documents` | Ingest custom plain-text documents (tenant/case isolated) | Yes |
| `search_documents` | Semantic search over custom documents with filters | Yes |
| `ingest_markdown_files` | Ingest Markdown files from disk under an allowlisted root | Yes |
| `convert_files_to_markdown` | Convert allowlisted files (e.g., PDFs) on disk to Markdown/text | Yes |
| `ingest_pdf_files` | Ingest PDFs from disk (convert → ingest) | Yes |

---

## Demo Tools

### `hello`

A simple greeting tool that demonstrates basic MCP tool patterns.

**Parameters:**
- `name` (string, optional): The name to greet. Default: `"World"`

**Returns:**
```json
{
  "message": "Hello, World!",
  "server": "legal-mcp"
}
```

**Example:**
```
hello("Alice")
→ {"message": "Hello, Alice!", "server": "legal-mcp"}
```

---

### `generate_items`

Generate a list of items with caching support. Demonstrates reference-based caching for large results.

**Parameters:**
- `count` (integer, optional): Number of items to generate. Range: 1-10000. Default: `10`
- `prefix` (string, optional): Prefix for item names. Default: `"item"`

**Returns:**
For small results (≤64 tokens), returns the full data.
For large results, returns a reference with preview:

```json
{
  "ref_id": "public:abc123",
  "preview": [{"id": 0, "name": "item_0", "value": 0}, ...],
  "total_items": 100,
  "preview_strategy": "sample"
}
```

**Example:**
```
generate_items(count=100, prefix="widget")
→ Returns ref_id + preview; use get_cached_result to paginate
```

---

## Secret/Private Computation Tools

### `store_secret`

Store a secret value that agents can use in computations but cannot read.

This demonstrates the EXECUTE permission model - agents can orchestrate computations with the secret without ever seeing its value.

**Parameters:**
- `name` (string, required): Name for the secret (1-100 characters)
- `value` (float, required): The secret numeric value

**Returns:**
```json
{
  "ref_id": "user:secrets:secret_mykey",
  "name": "mykey",
  "message": "Secret 'mykey' stored. Use compute_with_secret.",
  "permissions": {
    "user": "FULL (can read, write, execute)",
    "agent": "EXECUTE only (can use in computation, cannot read)"
  }
}
```

**Example:**
```
store_secret("api_key_hash", 12345.0)
→ Returns ref_id for use with compute_with_secret
```

---

### `compute_with_secret`

Perform computation using a secret value without revealing it.

The agent orchestrates the computation but never sees the actual secret value. Only the result is returned.

**Parameters:**
- `secret_ref` (string, required): Reference ID from `store_secret`
- `multiplier` (float, optional): Value to multiply the secret by. Default: `1.0`

**Returns:**
```json
{
  "result": 24690.0,
  "multiplier": 2.0,
  "secret_ref": "user:secrets:secret_api_key_hash",
  "message": "Computed using secret value (value not revealed)"
}
```

**Example:**
```
# First store a secret
result = store_secret("my_secret", 100.0)

# Then compute with it (agent never sees 100.0)
compute_with_secret(result["ref_id"], multiplier=2.5)
→ {"result": 250.0, ...}
```

---

## Cache Tools

### `get_cached_result`

Retrieve a cached result with optional pagination support.

Use this to:
- Get a preview of a cached value
- Paginate through large lists
- Access the full value of a cached result

**Parameters:**
- `ref_id` (string, required): Reference ID to look up
- `page` (integer, optional): Page number (1-indexed)
- `page_size` (integer, optional): Items per page (1-100)
- `max_size` (integer, optional): Maximum preview size in tokens

**Returns:**
```json
{
  "ref_id": "public:abc123",
  "preview": [...],
  "preview_strategy": "sample",
  "total_items": 100,
  "page": 2,
  "total_pages": 5
}
```

**Example:**
```
# Get page 2 with 20 items per page
get_cached_result("public:abc123", page=2, page_size=20)
```

---

## Health & Status Tools

### `health_check`

Check server health status and configuration.

**Parameters:** None

**Returns:**
```json
{
  "status": "healthy",
  "server": "legal-mcp",
  "cache": "legal-mcp",
  "langfuse_enabled": true,
  "test_mode": false
}
```

---

## Custom Document Tools

These tools support ingesting and searching your own case files / documents. Isolation is enforced via `tenant_id` (required). Optionally scope further with `case_id`.

### Allowlisted ingest root (file-based tools)

File-based tools (`ingest_markdown_files`, `convert_files_to_markdown`, `ingest_pdf_files`) can only read files under an allowlisted root directory.

- Override via environment variable: `LEGAL_MCP_INGEST_ROOT`
- Default (when unset): `{worktree_root}/.agent/tmp` (server `cwd` is `{worktree_root}` in Zed)

Security constraints:
- Absolute paths are rejected
- `..` traversal is rejected
- Symlink escapes are rejected (resolved real path must stay within root)
- Errors must never include raw document content

---

### `ingest_documents`

Ingest custom plain-text documents into the custom documents vector store.

**Parameters:**
- `tenant_id` (string, required): Tenant identifier for isolation
- `documents` (array, required): List of documents:
  - `source_name` (string, required): Human-friendly label (e.g., filename)
  - `text` (string, required): Plain text content to ingest
  - `document_id` (string, optional): Stable ID; if omitted, derived deterministically
  - `metadata` (object[string,string], optional): Shallow string metadata
- `case_id` (string, optional): Case identifier for scoping
- `tags` (array[string], optional): Tags applied to all documents
- `chunking` (object, optional): Chunking configuration:
  - `chunk_size_chars` (int)
  - `chunk_overlap_chars` (int)
  - `max_chunks_per_document` (int|null)

**Returns:**
A structured result with totals and per-document summaries (may be cached as a reference depending on size).

**Example:**
```
ingest_documents(
  tenant_id="t_123",
  case_id="c_001",
  tags=["mietrecht"],
  documents=[
    {
      "source_name": "notes.txt",
      "text": "Tenant reports mold in bathroom.",
      "metadata": {"document_type": "case_notes"}
    }
  ]
)
```

---

### `search_documents`

Semantic search over custom documents with filter capabilities.

**Parameters:**
- `query` (string, required): Search query
- `tenant_id` (string, required): Tenant identifier for isolation
- `case_id` (string, optional): Scope search to a case
- `n_results` (int, optional): 1-50 (default: 10)
- `document_id` (string, optional): Exact match filter
- `source_name` (string, optional): Exact match filter
- `tag` (string, optional): Single tag filter
- `excerpt_chars` (int, optional): Returned excerpt length

**Returns:**
A structured result with `results[]` containing `chunk_id`, `document_id`, `similarity`, `excerpt`, and metadata fields.

**Example:**
```
search_documents(
  query="mold bathroom",
  tenant_id="t_123",
  case_id="c_001",
  n_results=5
)
```

---

### `ingest_markdown_files`

Ingest Markdown files from disk under the allowlisted ingest root.

**Parameters:**
- `tenant_id` (string, required)
- `paths` (array[string], required): Relative paths under allowlisted root (e.g., `"case_a/notes.md"`)
- `case_id` (string, optional)
- `tags` (array[string], optional)
- `chunking` (object, optional)
- `max_chars_per_file` (int|null, optional): Safety cap (default: 2_000_000)

**Returns:**
Structured result with totals and per-file summaries.

**Example:**
```
ingest_markdown_files(
  tenant_id="t_123",
  case_id="c_001",
  paths=["case_a/converted/contract.md"],
  tags=["contract"]
)
```

---

### `convert_files_to_markdown`

Convert allowlisted files (e.g., PDFs) from disk to Markdown/text.

Primary behavior:
- Reads allowlisted input files under the ingestion root
- Converts them to Markdown/text server-side
- Writes the converted Markdown back to disk under the allowlisted ingest root as a `.md` sidecar file

**Parameters:**
- `paths` (array[string], required): Relative paths under allowlisted root
- `max_chars_per_file` (int|null, optional): Safety cap for converted text size (default: 5_000_000)
- `overwrite` (boolean, optional): Whether to overwrite an existing output `.md` file (default: `true`)

**Returns:**
Structured conversion result, including per-file `metadata` and output file paths (converted Markdown is written to disk under the allowlisted ingest root; raw markdown is not returned inline).

**Example:**
```
convert_files_to_markdown(
  paths=["case_a/uploads/notice.pdf"],
  max_chars_per_file=200000,
  overwrite=true
)
```

---

### `ingest_pdf_files`

Ingest PDFs from disk under the allowlisted ingest root (convert → write markdown → ingest).

**Parameters:**
- `tenant_id` (string, required)
- `paths` (array[string], required): Relative `.pdf` paths under allowlisted root
- `case_id` (string, optional)
- `tags` (array[string], optional)
- `chunking` (object, optional)
- `max_chars_per_file` (int|null, optional): Safety cap for converted text (default: 5_000_000)
- `replace` (boolean, optional): Whether to delete existing chunks for the same document (scoped by tenant and optional case) before upserting new vectors. Default: `true`

**Returns:**
Structured result with totals and per-file summaries (chunks created/added).

**Example:**
```
ingest_pdf_files(
  tenant_id="t_123",
  case_id="c_001",
  paths=["case_a/uploads/notice.pdf"],
  tags=["notice"],
  replace=true
)
```

---

## Context Management Tools

These tools are for testing and demonstrating Langfuse tracing with user/session attribution.

### `enable_test_context`

Enable or disable test context mode for Langfuse attribution demos.

When enabled, all traces will include user_id, session_id, and metadata from MockContext.

**Parameters:**
- `enabled` (boolean, optional): Whether to enable test context mode. Default: `true`

**Returns:**
```json
{
  "test_mode": true,
  "context": {
    "user_id": "demo-user",
    "org_id": "demo-org",
    "agent_id": "demo-agent"
  },
  "langfuse_enabled": true,
  "message": "Test context mode enabled..."
}
```

---

### `set_test_context`

Set test context values for Langfuse attribution demos.

**Parameters:**
- `user_id` (string, optional): User identity (e.g., "alice", "bob")
- `org_id` (string, optional): Organization identity (e.g., "acme", "globex")
- `session_id` (string, optional): Session identifier for grouping traces
- `agent_id` (string, optional): Agent identity (e.g., "claude", "gpt4")

**Returns:**
```json
{
  "context": {
    "user_id": "alice",
    "org_id": "acme",
    "agent_id": "demo-agent"
  },
  "langfuse_attributes": {
    "user_id": "alice",
    "session_id": "chat-001",
    "metadata": {...},
    "tags": [...]
  },
  "message": "Context updated..."
}
```

**Example:**
```
set_test_context(user_id="alice", org_id="acme", session_id="chat-001")
```

---

### `reset_test_context`

Reset test context to default demo values.

**Parameters:** None

**Returns:**
```json
{
  "context": {
    "user_id": "demo-user",
    "org_id": "demo-org",
    "agent_id": "demo-agent"
  },
  "message": "Context reset to default demo values."
}
```

---

### `get_trace_info`

Get information about current Langfuse tracing configuration and context.

**Parameters:** None

**Returns:**
```json
{
  "langfuse_enabled": true,
  "langfuse_host": "https://cloud.langfuse.com",
  "public_key_set": true,
  "secret_key_set": true,
  "test_mode_enabled": true,
  "current_context": {...},
  "langfuse_attributes": {
    "user_id": "alice",
    "session_id": "chat-001",
    "metadata": {...},
    "tags": [...]
  },
  "message": "Traces are being sent to Langfuse..."
}
```

---

## Admin Tools

Admin tools are registered but require admin privileges (disabled by default).

| Tool | Description |
|------|-------------|
| `admin_list_references` | List cached references with filtering |
| `admin_get_reference_info` | Get detailed info about a cached reference |
| `admin_get_cache_stats` | Get cache statistics |
| `admin_delete_reference` | Delete a specific cached reference |
| `admin_clear_namespace` | Clear all references in a namespace |

To enable admin access, override the `is_admin` function in `app/server.py` with your authentication logic.

---

## MCP Prompts

The server also provides two prompts for guidance:

### `template_guide`

Comprehensive guide for using this MCP server template, including:
- Quick start instructions
- Langfuse tracing setup
- Caching examples
- Private computation patterns

### `langfuse_guide`

Detailed guide for Langfuse tracing integration:
- Environment variable setup
- Context propagation
- Viewing traces in Langfuse dashboard
- Best practices
