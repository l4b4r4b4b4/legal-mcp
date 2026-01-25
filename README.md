# Legal-MCP

A comprehensive legal research knowledge base as an MCP server, built with [FastMCP](https://github.com/jlowin/fastmcp) and [mcp-refcache](https://github.com/l4b4r4b4b4/mcp-refcache). Provides AI assistants with powerful hybrid retrieval (semantic search + structured queries) across legal documents from multiple jurisdictions.

## Key Features

- **📦 Git-Tracked Legal Corpus** — All legal documents version-controlled in git for full traceability
- **🔍 Hybrid Retrieval** — Combine semantic search with structured queries (filters, date ranges, boolean logic)
- **📊 Multi-Level Chunking** — Documents embedded at Law → Book → Section → Paragraph levels
- **📅 Version Tracking** — Track law amendments over time, query "law as of date X"
- **💾 Pre-Seeded Data** — Ships with pre-processed corpus, works offline immediately
- **🔗 RefCache Integration** — Large legal texts returned as references, reducing context window usage

## Supported Jurisdictions

| Jurisdiction | Source | Format | Status |
|--------------|--------|--------|--------|
| 🇩🇪 German Federal Law | [gesetze-im-internet.de](https://www.gesetze-im-internet.de) | XML | 🟡 In Development |
| 🇩🇪 German Court Decisions | [rechtsprechung-im-internet.de](https://www.rechtsprechung-im-internet.de) | XML | ⚪ Planned |
| 🇩🇪 German State Law (Berlin) | [gesetze.berlin.de](https://gesetze.berlin.de) (portal: `bsbe`) | Offline catalog (SQLite) + on-demand retrieval | 🟡 In Development |
| 🇩🇪 German State Law (Other) | Landesrecht portals | XML | ⚪ Planned |
| 🇪🇺 EU Law | [EUR-Lex](https://eur-lex.europa.eu) | API/XML | ⚪ Planned |
| 🇺🇸 US Federal Law | [Congress.gov](https://congress.gov) | API | ⚪ Planned |
| 🇺🇸 US Case Law | [CourtListener](https://courtlistener.com) | API | ⚪ Planned |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Legal-MCP Server (FastMCP)                   │
├─────────────────────────────────────────────────────────────────┤
│  MCP Tools                                                       │
│  • search_laws()      - Semantic search (embeddings)            │
│  • query_laws()       - Structured queries (SQL-like filters)   │
│  • get_law()          - Direct citation lookup                  │
│  • hybrid_search()    - Combined semantic + structured          │
│  • find_related()     - Similar documents                       │
│  • law_history()      - Version tracking                        │
├─────────────────────────────────────────────────────────────────┤
│  Hybrid Retrieval Layer                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐               │
│  │  Semantic Search    │  │  Structured Queries │               │
│  │  (Vector/Cosine)    │  │  (SQL/Filters/BM25) │               │
│  └─────────────────────┘  └─────────────────────┘               │
├─────────────────────────────────────────────────────────────────┤
│  Storage Layer                                                   │
│  • SQLite (metadata, structured queries, FTS)                   │
│  • ChromaDB (vector embeddings)                                 │
│  • RefCache (full document text)                                │
├─────────────────────────────────────────────────────────────────┤
│  Git-Tracked Data (data/)                                        │
│  • raw/          - Original XML downloads                       │
│  • processed/    - Parsed & chunked documents                   │
│  • embeddings/   - Pre-computed vectors                         │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Installation

```bash
# Clone the repository (includes pre-seeded legal corpus)
git clone https://github.com/l4b4r4b4b4/legal-mcp
cd legal-mcp

# Install dependencies
uv sync

# Run the server (stdio mode for Claude Desktop)
uv run legal-mcp

# Run the server (SSE mode for deployment)
uv run legal-mcp --transport sse --port 8000
```

### Using with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "legal-mcp": {
      "command": "uv",
      "args": ["run", "legal-mcp"],
      "cwd": "/path/to/legal-mcp"
    }
  }
}
```

## MCP Tools

### Available Document Catalog (Offline)

List *available* documents from a bundled offline catalog (no network IO). This is intended for discovery (IDs + canonical URLs), not content retrieval.

Tools:
- `list_available_documents(source, prefix=None, offset=0, limit=50)`
- `berlin_list_available_documents(...)` (compatibility alias)

Examples:

```python
# List first 25 Berlin documents (IDs + canonical URLs)
list_available_documents(
    source="de-state-berlin-bsbe",
    offset=0,
    limit=25,
)

# Filter Berlin norms ("jlr") vs decisions ("NJRE")
list_available_documents(
    source="de-state-berlin-bsbe",
    prefix="jlr",
    offset=0,
    limit=25,
)

list_available_documents(
    source="de-state-berlin-bsbe",
    prefix="NJRE",
    offset=0,
    limit=25,
)

# Compatibility alias (delegates to list_available_documents with Berlin source)
berlin_list_available_documents(prefix="jlr", offset=0, limit=25)
```

### Semantic Search

Natural language queries across the legal corpus:

```python
search_laws(
    query="employee protection against unfair dismissal",
    jurisdiction=["DE-Federal"],
    chunk_level="section",  # law, book, title, section, paragraph
    limit=10
)
```

### Structured Queries

SQL-like filtering for precise lookups:

```python
query_laws(
    filters={
        "law_id": "bgb",
        "contains": "Schadensersatz",
        "effective_after": "2020-01-01",
        "status": "active"
    }
)
```

### Citation Lookup

Direct lookup by legal citation:

```python
get_law(
    citation="BGB § 823 Abs. 1",
    date="2023-06-15"  # Law as of this date
)
```

### Hybrid Search

Combine semantic search with structured filters:

```python
hybrid_search(
    semantic_query="consumer protection in online purchases",
    filters={"jurisdiction": "EU", "law_type": "Richtlinie"},
    full_text="Widerruf OR Rücktritt"
)
```

### Related Documents

Find similar or referenced laws:

```python
find_related(
    document_id="bgb-823",
    relation_type="similar"  # similar, cites, cited_by, amends
)
```

### Version History

Track amendments over time:

```python
law_history(law_id="bgb", section_ref="§ 823")

compare_versions(
    law_id="bgb",
    section_ref="§ 823",
    version_a="2020-01-01",
    version_b="2024-01-01"
)
```

## Offline Catalog Build (Berlin)

Berlin’s availability catalog is built from the sitemap(s) and committed as a SQLite database so the server can list available document IDs offline.

Build/update workflow:

1) Generate a discovery snapshot (manual network IO; bounded/polite):

```bash
uv run python scripts/berlin_portal_discovery.py
```

2) Convert snapshot → bundled SQLite catalog (offline):

```bash
uv run python scripts/catalog/build_catalog_sqlite.py --replace
```

The resulting file is:

- `app/catalog_data/de_state_berlin_bsbe.sqlite`

Notes:
- The catalog stores metadata only (document ID, canonical URL, derived prefix).
- Document content retrieval remains on-demand and is handled separately.
- If you see a “Catalog not found” error from the tool, ensure the SQLite file exists at the path above (and is not a Git LFS pointer file).

## Data Architecture

### Git-Tracked Legal Corpus

All legal documents are version-controlled in git:

```
data/
├── raw/                          # Original XML downloads
│   └── de-federal/
│       ├── bgb.xml               # Bürgerliches Gesetzbuch
│       ├── stgb.xml              # Strafgesetzbuch
│       ├── gg.xml                # Grundgesetz
│       └── ...
├── processed/                    # Parsed & chunked (JSON/Parquet)
│   └── de-federal/
│       ├── documents.parquet     # All chunks with metadata
│       └── index.json            # Law index
└── embeddings/                   # Pre-computed embeddings
    └── de-federal/
        └── embeddings.parquet
```

**Benefits of git-tracking:**
- 📜 **Full traceability** — Every law change is a git commit
- 🔍 **Diffable** — `git diff` shows exactly what changed in an amendment
- 🔄 **Reproducible** — Clone and get the exact same corpus
- 📦 **Pre-seeded** — Works offline immediately after install
- 📋 **Audit trail** — When was each law version ingested?

### Multi-Level Chunking

Documents are embedded at multiple granularity levels for optimal retrieval:

| Level | Use Case | Example |
|-------|----------|---------|
| Law | "What is contract law?" | Returns BGB overview |
| Book | "What are obligation rules?" | Returns BGB Book 2 |
| Title | "What are lease termination rules?" | Returns BGB §§ 542-580a |
| Section | "What is § 823 BGB?" | Returns specific section |
| Paragraph | "Define Vorsatz" | Returns specific paragraph |

### Configuration

```python
from legal_mcp import Config

config = Config(
    # Pre-seeded data (shipped with package)
    use_bundled_data=True,
    bundled_data_path="data/",
    
    # Custom data directory (override bundled)
    custom_data_path=None,
    
    # Live updates (download new laws)
    enable_live_updates=False,
    update_check_interval_days=7,
    
    # Embedding model
    embedding_model="paraphrase-multilingual-mpnet-base-v2",
    # Or use API: "openai:text-embedding-3-large"
)
```

## Project Structure

```
legal-mcp/
├── app/
│   ├── __init__.py
│   ├── __main__.py              # CLI entry point
│   ├── server.py                # Main MCP server
│   ├── config.py                # Configuration
│   ├── ingestion/               # Data ingestion pipeline
│   │   ├── download.py          # Bulk XML download
│   │   ├── parse.py             # XML parsing
│   │   ├── chunk.py             # Multi-level chunking
│   │   └── embed.py             # Embedding generation
│   ├── retrieval/               # Search & query layer
│   │   ├── semantic.py          # Vector search
│   │   ├── structured.py        # SQL/filter queries
│   │   └── hybrid.py            # Combined retrieval
│   ├── storage/                 # Storage backends
│   │   ├── documents.py         # SQLite document store
│   │   └── vectors.py           # ChromaDB vectors
│   └── tools/                   # MCP tool definitions
│       ├── search.py
│       ├── query.py
│       └── history.py
├── data/                        # Git-tracked legal corpus
│   ├── raw/
│   ├── processed/
│   └── embeddings/
├── tests/
├── pyproject.toml
└── README.md
```

## Development

### Setup

```bash
uv sync
uv run pre-commit install --install-hooks
```

### Running Tests

```bash
uv run pytest
uv run pytest --cov
```

### Linting

```bash
uv run ruff check . --fix --unsafe-fixes
uv run ruff format .
```

### Updating the Legal Corpus

```bash
# Download latest laws from gesetze-im-internet.de
uv run legal-mcp ingest download --jurisdiction de-federal

# Process and embed
uv run legal-mcp ingest process
uv run legal-mcp ingest embed

# Commit changes
git add data/
git commit -m "Update: BGBl. I Nr. 123/2026"
```

## Data Sources

### Official Sources (No Web Scraping!)

| Source | Data | License |
|--------|------|---------|
| [gesetze-im-internet.de](https://www.gesetze-im-internet.de) | German federal laws (XML) | Public domain |
| [rechtsprechung-im-internet.de](https://www.rechtsprechung-im-internet.de) | Court decisions (XML) | Public domain |
| [EUR-Lex](https://eur-lex.europa.eu) | EU legislation (API) | CC BY 4.0 |
| [Congress.gov](https://congress.gov) | US legislation (API) | Public domain |
| [CourtListener](https://courtlistener.com) | US case law (API) | CC0 |

All data is downloaded from official government sources in structured formats (XML/API). No web scraping required.

## Roadmap

- [x] **Phase 0**: Architecture design and planning
- [ ] **Phase 1**: German federal law (gesetze-im-internet.de XML)
- [ ] **Phase 2**: Version tracking and incremental updates
- [ ] **Phase 3**: EU law (EUR-Lex API)
- [ ] **Phase 4**: US law (Congress.gov, CourtListener)
- [ ] **Phase 5**: German state laws, court decisions

## License

MIT License - see [LICENSE](LICENSE) for details.

## Related Projects

- [mcp-refcache](https://github.com/l4b4r4b4b4/mcp-refcache) — Reference-based caching for MCP servers
- [FastMCP](https://github.com/jlowin/fastmcp) — High-performance MCP server framework
- [deutschland](https://github.com/bundesAPI/deutschland) — German government APIs (inspiration)

## Acknowledgments

- [gesetze-im-internet.de](https://www.gesetze-im-internet.de) — Federal Ministry of Justice
- [EUR-Lex](https://eur-lex.europa.eu) — Publications Office of the EU
- [Free Law Project](https://free.law) — CourtListener and open legal data