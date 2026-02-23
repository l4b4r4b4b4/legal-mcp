# Bug: legal-mcp Docker Image Missing `chromadb` Dependency

> **Severity**: High (all German law tools are broken)
> **Component**: `ghcr.io/l4b4r4b4b4/legal-mcp:latest`
> **Image built**: 2026-02-08T04:20:06Z
> **FastMCP version**: 2.14.2
> **MCP version**: 1.25.0
> **Discovered**: Session 161, 2026-02-18
> **Related**: Goal 55 Task-02 (legal-mcp integration testing)

---

## Problem

All three German law tools (`search_laws`, `get_law_by_id`, `get_law_stats`) fail at runtime with `ModuleNotFoundError: No module named 'chromadb'`. The `chromadb` package is not installed in the Docker image, but is imported at module level in `app/ingestion/embeddings.py`.

The MCP server starts fine and advertises the tools, but every tool invocation crashes on the lazy import chain.

## Error Trace

```
legal-mcp  | Error calling tool 'get_law_by_id'
legal-mcp  | ╭─────────── Traceback (most recent call last) ────────────╮
legal-mcp  | │ /app/app/tools/german_laws.py:321 in get_law_by_id      │
legal-mcp  | │   ❱ 321 │   │   from app.ingestion.embeddings import    │
legal-mcp  | │          │   │   GermanLawEmbeddingStore                 │
legal-mcp  | │                                                          │
legal-mcp  | │ /app/app/ingestion/embeddings.py:26 in <module>          │
legal-mcp  | │   ❱  26 import chromadb                                  │
legal-mcp  | ╰──────────────────────────────────────────────────────────╯
legal-mcp  | ModuleNotFoundError: No module named 'chromadb'
```

## Root Cause

`app/ingestion/embeddings.py` has a **top-level** `import chromadb` (line 26) and `from chromadb.config import Settings as ChromaSettings` (line 27). Although the tool functions in `german_laws.py` use lazy imports (`from app.ingestion.embeddings import GermanLawEmbeddingStore` inside the function body), the module itself unconditionally imports `chromadb` at parse time.

The Docker image has only 85 installed packages. `chromadb` (and presumably `sentence-transformers`) are **not** among them.

### Affected Import Chain

All three tools follow the same path:

| Tool | File | Line | Import |
|------|------|------|--------|
| `search_laws` | `german_laws.py` | via `pipeline.py:27` | `from app.ingestion.embeddings import GermanLawEmbeddingStore` |
| `get_law_by_id` | `german_laws.py` | 321 | `from app.ingestion.embeddings import GermanLawEmbeddingStore` |
| `get_law_stats` | `german_laws.py` | 259 | `from app.ingestion.embeddings import GermanLawEmbeddingStore` |

All roads lead to `embeddings.py:26` → `import chromadb` → 💥

### Custom document tools (`search_documents`, `ingest_documents`, etc.) are NOT affected

They do not import from `app.ingestion.embeddings`.

## Verification

```bash
# Confirm chromadb is missing
docker exec legal-mcp python3 -c "import chromadb" 2>&1
# ModuleNotFoundError: No module named 'chromadb'

# Confirm all law tools fail
docker exec legal-mcp python3 -c "
from app.ingestion.embeddings import GermanLawEmbeddingStore
" 2>&1
# ModuleNotFoundError: No module named 'chromadb'

# Confirm server is otherwise healthy
curl -s http://localhost:8002/health
# {"status":"healthy","server":"Legal-MCP","cache":{"name":"legal-mcp"}}
```

## Fix Options

### Option A: Add `chromadb` to the Docker image (recommended)

Add `chromadb` (and `sentence-transformers` if needed for embeddings) to the image's dependencies. This is the straightforward fix — the code expects these packages.

```dockerfile
# In Dockerfile or requirements.txt
RUN pip install chromadb sentence-transformers
```

**Caveat**: `chromadb` pulls in heavy deps (SQLite, hnswlib, etc.) which will increase image size significantly. If the image was intentionally kept lean, consider Option B.

### Option B: Make `chromadb` import conditional

Guard the import in `embeddings.py` so the module can be imported without `chromadb` installed (tools would still fail gracefully with a clear error message):

```python
# app/ingestion/embeddings.py
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

class GermanLawEmbeddingStore:
    def __init__(self, *args, **kwargs):
        if not HAS_CHROMADB:
            raise RuntimeError(
                "chromadb is required for German law search. "
                "Install with: pip install chromadb"
            )
        # ... existing init code
```

### Option C: Split image variants

Create a `legal-mcp:latest` (lightweight, custom doc tools only) and `legal-mcp:full` (with chromadb + embedding model for German law tools). This keeps the lean image for users who only need custom document search.

## Additional Issue: Health Check

The Docker image also lacks `curl`, causing the health check in `docker-compose.yml` to report `(unhealthy)` even though the server is running fine. This was already fixed in the platform repo by switching to a `python3 urllib` based health check. Consider adding `curl` to the image or documenting the Python-based health check pattern.

```yaml
# Fixed health check (no curl needed)
healthcheck:
  test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 20s
```

## Environment Details

```
Container: legal-mcp
Image: ghcr.io/l4b4r4b4b4/legal-mcp:latest
Built: 2026-02-08T04:20:06Z
Python: 3.12
FastMCP: 2.14.2
MCP: 1.25.0
mcp-refcache: 0.1.0
uvicorn: 0.40.0
Total packages: 85 (chromadb NOT among them)
```
