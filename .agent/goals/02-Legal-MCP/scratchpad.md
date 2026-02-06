# Goal 02: Legal-MCP - Comprehensive Legal Research Knowledge Base

> **Status**: 🟡 In Progress
> **Priority**: P1 (High)
> **Created**: 2025-01-07
> **Updated**: 2025-01-23

---

## Overview

Build a comprehensive legal research knowledge base as an MCP server using FastMCP + mcp-refcache. The system downloads official legal documents in bulk (XML format), preprocesses and embeds them at multiple aggregation levels, stores them in a hybrid database supporting both semantic search AND structured queries, and provides AI assistants with powerful legal research capabilities.

**Key Insight**: This is NOT a web scraping project. This is a **legal knowledge base with hybrid retrieval** (semantic + structured) that:
1. Downloads official XML bulk data from government sources
2. **Git-tracks all legal documents** for full traceability and reproducibility
3. Parses, structures, and embeds legal texts at multiple granularity levels
4. Supports both natural language queries AND complex structured queries (SQL-like)
5. Tracks document versions over time (effective dates, amendments, superseded laws)
6. Ships with **pre-seeded data** — works offline immediately after install
7. Configurable for custom data sources and live updates

**Why Git-Tracked Legal Corpus?**
- 📜 **Full traceability** — Every law change is a git commit
- 🔍 **Diffable** — `git diff` shows exactly what changed in an amendment
- 🔄 **Reproducible** — Anyone can clone and get the exact same corpus
- 📦 **Pre-seeded releases** — Download once during dev, publish as part of package
- 💾 **Offline-first** — Works without network after initial clone
- 📋 **Audit trail** — When did we ingest which version of which law?

---

## Success Criteria

### MVP (Phase 1)
- [ ] Bulk download German federal law XML from gesetze-im-internet.de
- [ ] Git-track raw XML in `data/raw/de-federal/`
- [ ] Parse XML into structured document hierarchy
- [ ] Multi-level chunking (Law → Book → Section → Paragraph)
- [ ] Git-track processed data in `data/processed/`
- [ ] Embed documents at each aggregation level (local model)
- [ ] Git-track embeddings in `data/embeddings/`
- [ ] Hybrid retrieval: semantic search + structured queries
- [ ] Version tracking with effective dates
- [ ] MCP tools for search and retrieval
- [ ] RefCache integration for large document storage
- [ ] Configurable data sources (bundled vs custom)
- [ ] CLI for data ingestion (`legal-mcp ingest`)
- [ ] ≥73% test coverage

### Full System
- [ ] German state laws (16 Länder)
- [ ] EU law via EUR-Lex API
- [ ] Court decisions (rechtsprechung-im-internet.de)
- [ ] US federal law (Congress.gov, CourtListener)
- [ ] Incremental update mechanism
- [ ] Citation graph (laws referencing other laws)
- [ ] Multi-language support

---

## Future Features (Post-MVP)

### Citation Graphs
- **Explicit citations**: Regex extraction of references like "§ 433 BGB", "Art. 1 GG"
- **Implicit citations**: Semantic similarity between legal texts (embedding-based)
- **Graph structure**: Laws as nodes, citations as directed edges
- **Query capabilities**: "What laws reference § 823 BGB?", "Citation chain analysis"
- **Visualization**: Interactive graph explorer for legal research

### Database Evolution Path
| Stage | Technology | Use Case |
|-------|------------|----------|
| MVP | SQLite + ChromaDB | Simple, portable, offline-first |
| If citations core | Neo4j | Native graph queries, citation traversal |
| At scale | PostgreSQL + pgvector | Production-ready, ACID, extensions |

**Migration strategy**: Abstract storage layer, swap backends without API changes

### User Document Injection
- **Runtime ingestion**: Users can add their own documents (contracts, case files)
- **Access control**: Per-user document isolation, tenant separation
- **Storage backends**: Local filesystem, S3, cloud storage
- **Indexing**: Same embedding pipeline as public corpus
- **Privacy**: User documents never mixed with shared corpus

### Live Updates via Git
- **Public corpus**: Periodic `git pull` from upstream repository
- **User data**: Separate `user_data/` directory (gitignored)
- **Update detection**: Compare `gii-toc.xml` against stored version
- **Incremental processing**: Only re-embed changed documents
- **Notifications**: Webhook/RSS for law amendments

### Multi-Tenant Support (with mcp-refcache)
- **Tenant isolation**: Separate vector stores per tenant
- **Permission model**: Read-only public corpus, read-write user docs
- **Caching**: Shared cache for public data, per-tenant for private
- **Quotas**: Storage and query limits per tenant

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Legal-MCP Server (FastMCP)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  MCP Tools Layer                                                             │
│  ┌────────────────┐ ┌─────────────────┐ ┌──────────────────┐                │
│  │ search_laws()  │ │ query_laws()    │ │ get_law()        │                │
│  │ (semantic)     │ │ (structured)    │ │ (by ID/citation) │                │
│  └────────────────┘ └─────────────────┘ └──────────────────┘                │
│  ┌────────────────┐ ┌─────────────────┐ ┌──────────────────┐                │
│  │ find_related() │ │ law_history()   │ │ compare_versions │                │
│  │ (similar docs) │ │ (versions)      │ │ (diff view)      │                │
│  └────────────────┘ └─────────────────┘ └──────────────────┘                │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Hybrid Retrieval Layer                                                      │
│  ┌─────────────────────────────────┐ ┌─────────────────────────────────┐    │
│  │      Semantic Search            │ │      Structured Queries         │    │
│  │  • Vector similarity (cosine)   │ │  • Field filtering              │    │
│  │  • Multi-level embeddings       │ │  • Date range queries           │    │
│  │  • Cross-lingual (future)       │ │  • Boolean logic (AND/OR/NOT)   │    │
│  └─────────────────────────────────┘ │  • Citation lookups             │    │
│                                       │  • Jurisdiction filtering       │    │
│                                       │  • Full-text search (BM25)      │    │
│                                       └─────────────────────────────────┘    │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Storage Layer                                                               │
│  ┌─────────────────────────────────┐ ┌─────────────────────────────────┐    │
│  │      Vector Database            │ │      Document Store             │    │
│  │  • ChromaDB / pgvector          │ │  • SQLite / PostgreSQL          │    │
│  │  • Multi-level embeddings       │ │  • Full document metadata       │    │
│  │  • Metadata filtering           │ │  • Version history              │    │
│  └─────────────────────────────────┘ │  • RefCache for full texts      │    │
│                                       └─────────────────────────────────┘    │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Data Ingestion Pipeline                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │ 1. Download  │→│ 2. Parse XML │→│ 3. Chunk     │→│ 4. Embed     │        │
│  │   (bulk)     │ │ (structure)  │ │ (multi-level)│ │ (local/API)  │        │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘        │
│         ↓                                                    ↓               │
│  ┌──────────────┐                                    ┌──────────────┐        │
│  │ 5. Store     │←───────────────────────────────────│ 6. Index     │        │
│  │ (DB + Cache) │                                    │ (search)     │        │
│  └──────────────┘                                    └──────────────┘        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Query Types Comparison

| Query Type | Use Case | Example |
|------------|----------|---------|
| **Semantic Search** | Natural language, conceptual | "Find laws about employee dismissal protection" |
| **Structured Query** | Precise filtering, metadata | "All BGB sections amended after 2020-01-01" |
| **Citation Lookup** | Direct reference | "BGB § 823 Abs. 1" |
| **Full-Text Search** | Keyword matching | "Schadensersatz AND Vorsatz" |
| **Similarity Search** | Related documents | "Laws similar to DSGVO Art. 17" |
| **Hybrid Query** | Combined | "Contract termination laws (semantic) in BGB (filter) after 2015 (date)" |

---

## Git-Tracked Data Architecture

### Directory Structure

```
legal-mcp/
├── data/                              # Git-tracked legal corpus
│   ├── raw/                           # Original XML downloads (verbatim)
│   │   └── de-federal/
│   │       ├── bgb.xml                # Bürgerliches Gesetzbuch
│   │       ├── stgb.xml               # Strafgesetzbuch
│   │       ├── gg.xml                 # Grundgesetz
│   │       ├── hgb.xml                # Handelsgesetzbuch
│   │       └── ...                    # ~6000+ laws
│   │
│   ├── processed/                     # Parsed & chunked documents
│   │   └── de-federal/
│   │       ├── documents.parquet      # All chunks with metadata
│   │       ├── index.json             # Law index (fast lookup)
│   │       └── schema.json            # Document schema version
│   │
│   └── embeddings/                    # Pre-computed vector embeddings
│       └── de-federal/
│           ├── embeddings.parquet     # Vectors + document IDs
│           └── model_info.json        # Which model, dimensions, etc.
│
├── app/                               # Application code
└── ...
```

### What Gets Committed

| Directory | Content | Size Estimate | Update Frequency |
|-----------|---------|---------------|------------------|
| `data/raw/` | Original XML files | ~500MB | When laws change |
| `data/processed/` | Parquet + JSON | ~200MB | After re-processing |
| `data/embeddings/` | Vector embeddings | ~1GB | After re-embedding |

**Total estimated size: ~1.7GB for German federal law corpus**

### Git Workflow for Updates

```bash
# 1. Download latest laws
uv run legal-mcp ingest download --jurisdiction de-federal

# 2. Check what changed
git diff data/raw/

# 3. Process new/changed laws only
uv run legal-mcp ingest process --incremental

# 4. Re-embed changed documents
uv run legal-mcp ingest embed --incremental

# 5. Commit with meaningful message
git add data/
git commit -m "Update: BGBl. 2026 I Nr. 42 - Änderung BGB §§ 312-312k"

# 6. Tag for release
git tag -a v0.1.1 -m "Corpus update 2026-01-23"
```

### Configuration

```python
# app/config.py
from pydantic_settings import BaseSettings
from pathlib import Path

class DataConfig(BaseSettings):
    """Data source configuration."""
    
    # Pre-seeded data (shipped with package)
    use_bundled_data: bool = True
    bundled_data_path: Path = Path("data/")
    
    # Custom data directory (overrides bundled)
    custom_data_path: Path | None = None
    
    # Live updates (fetch new laws at runtime)
    enable_live_updates: bool = False
    update_check_interval_days: int = 7
    
    # Embedding configuration
    embedding_model: str = "paraphrase-multilingual-mpnet-base-v2"
    # Alternatives: "openai:text-embedding-3-large", "cohere:embed-multilingual-v3.0"
    
    @property
    def data_path(self) -> Path:
        """Effective data path (custom overrides bundled)."""
        if self.custom_data_path:
            return self.custom_data_path
        return self.bundled_data_path
```

### Deployment Scenarios

| Scenario | Configuration | Data Source |
|----------|---------------|-------------|
| **Default** | `use_bundled_data=True` | Pre-seeded in package |
| **Custom corpus** | `custom_data_path="/my/data"` | User-provided |
| **Live updates** | `enable_live_updates=True` | Fetch + cache |
| **Air-gapped** | `use_bundled_data=True`, no network | Pre-seeded only |

---

## Data Model

### Document Hierarchy (German Law Example)

```
Jurisdiction: DE-Federal
└── Law: BGB (Bürgerliches Gesetzbuch)
    ├── Metadata:
    │   ├── law_id: "bgb"
    │   ├── full_name: "Bürgerliches Gesetzbuch"
    │   ├── abbreviation: "BGB"
    │   ├── jurisdiction: "DE-Federal"
    │   ├── law_type: "Gesetz" (statute)
    │   ├── first_published: "1896-08-18"
    │   ├── current_version: "2024-01-01"
    │   ├── status: "active"
    │   └── source_url: "https://..."
    │
    ├── Book 1: Allgemeiner Teil (General Part)
    │   ├── chunk_level: "book"
    │   ├── embedding: [0.123, -0.456, ...]  # Book-level embedding
    │   │
    │   ├── Title 1: Personen (Persons)
    │   │   ├── chunk_level: "title"
    │   │   ├── embedding: [...]
    │   │   │
    │   │   ├── § 1 Beginn der Rechtsfähigkeit
    │   │   │   ├── chunk_level: "section"
    │   │   │   ├── section_number: "1"
    │   │   │   ├── heading: "Beginn der Rechtsfähigkeit"
    │   │   │   ├── text: "Die Rechtsfähigkeit des Menschen..."
    │   │   │   ├── embedding: [...]  # Section-level embedding
    │   │   │   └── versions: [
    │   │   │         {version: "1900-01-01", text: "...", status: "superseded"},
    │   │   │         {version: "2024-01-01", text: "...", status: "active"}
    │   │   │       ]
    │   │   │
    │   │   └── § 2 Eintritt der Volljährigkeit
    │   │       └── ...
    │   │
    │   └── Title 2: Sachen und Tiere
    │       └── ...
    │
    └── Book 2: Recht der Schuldverhältnisse
        └── ...
```

### Database Schema

```sql
-- Core document table
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES documents(id),
    
    -- Identification
    law_id TEXT NOT NULL,           -- e.g., "bgb"
    section_ref TEXT,               -- e.g., "§ 823"
    citation TEXT,                  -- e.g., "BGB § 823 Abs. 1"
    
    -- Hierarchy
    chunk_level TEXT NOT NULL,      -- law, book, title, section, paragraph
    path TEXT NOT NULL,             -- e.g., "bgb/book2/title27/section823"
    
    -- Content
    heading TEXT,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,        -- For change detection
    
    -- Metadata
    jurisdiction TEXT NOT NULL,     -- DE-Federal, DE-BY, EU, US-Federal, etc.
    law_type TEXT,                  -- Gesetz, Verordnung, Richtlinie, etc.
    language TEXT DEFAULT 'de',
    
    -- Versioning
    version_id TEXT NOT NULL,
    effective_date DATE NOT NULL,
    end_date DATE,                  -- NULL if current
    status TEXT DEFAULT 'active',   -- active, superseded, repealed
    superseded_by TEXT,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Indexes
    UNIQUE(law_id, section_ref, version_id)
);

-- Embeddings table (separate for flexibility)
CREATE TABLE embeddings (
    document_id TEXT REFERENCES documents(id),
    chunk_level TEXT NOT NULL,
    model TEXT NOT NULL,            -- e.g., "paraphrase-multilingual-mpnet-base-v2"
    embedding BLOB NOT NULL,        -- Vector as binary
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (document_id, chunk_level, model)
);

-- Version history
CREATE TABLE versions (
    id TEXT PRIMARY KEY,
    law_id TEXT NOT NULL,
    version_date DATE NOT NULL,
    change_type TEXT,               -- amendment, new, repeal
    change_source TEXT,             -- e.g., "BGBl. I S. 1234"
    change_description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Citations/references between laws
CREATE TABLE citations (
    source_id TEXT REFERENCES documents(id),
    target_id TEXT REFERENCES documents(id),
    citation_type TEXT,             -- references, amends, repeals
    citation_text TEXT,
    
    PRIMARY KEY (source_id, target_id)
);

-- Full-text search index (SQLite FTS5)
CREATE VIRTUAL TABLE documents_fts USING fts5(
    id, heading, text,
    content='documents',
    content_rowid='rowid'
);
```

---

## Multi-Level Chunking Strategy

### Why Multiple Levels?

Different query types need different granularity:

| Query Type | Best Level | Example |
|------------|------------|---------|
| "What is contract law?" | Law/Book | Returns BGB Book 2 overview |
| "What are the rules for lease termination?" | Title/Chapter | Returns BGB §§ 542-580a |
| "What is § 823 BGB?" | Section | Returns specific section |
| "Define Vorsatz in damages context" | Paragraph | Returns specific paragraph |

### Chunking Levels

```
Level 0: Corpus      - All laws in a jurisdiction (for cross-corpus queries)
Level 1: Law         - Entire law document (e.g., BGB, StGB, GG)
Level 2: Book/Part   - Major divisions (e.g., BGB Book 1-5)
Level 3: Title/Chapter - Thematic groupings (e.g., "Kauf", "Miete")
Level 4: Section (§)  - Individual sections (core unit)
Level 5: Paragraph    - Sub-divisions of sections (Absätze)
```

### Embedding Strategy

```python
# Each level gets its own embedding
embeddings = {
    "law": embed(law.summary + " ".join(section.headings)),
    "book": embed(book.title + book.summary),
    "title": embed(title.name + " ".join(section.headings)),
    "section": embed(section.heading + section.text),
    "paragraph": embed(paragraph.text)
}

# Search cascades from broad to specific
def search(query, start_level="section"):
    # 1. Search at requested level
    results = vector_search(query, level=start_level)
    
    # 2. Optionally expand to parent/child levels
    # 3. Apply metadata filters
    # 4. Return with hierarchy context
```

---

## Query Capabilities

### 1. Semantic Search

```python
@mcp.tool
async def search_laws(
    query: str,
    jurisdiction: list[str] = ["DE-Federal"],
    chunk_level: str = "section",
    date: str | None = None,  # Law as of this date
    limit: int = 10
) -> list[SearchResult]:
    """Natural language search across legal corpus.
    
    Examples:
        - "employee protection against unfair dismissal"
        - "liability for defective products"
        - "data protection rights of individuals"
    """
```

### 2. Structured Queries

```python
@mcp.tool
async def query_laws(
    filters: dict,
    sort_by: str = "relevance",
    limit: int = 50
) -> list[Document]:
    """Complex structured queries with filtering.
    
    Filter syntax:
        law_id: str | list[str]     - Filter by law(s)
        section_range: tuple        - e.g., ("1", "100")
        jurisdiction: str | list    - e.g., ["DE-Federal", "EU"]
        law_type: str               - Gesetz, Verordnung, etc.
        effective_after: date       - Amendments after date
        effective_before: date      - Law as of date
        status: str                 - active, superseded, repealed
        contains: str               - Full-text search (BM25)
        heading_contains: str       - Search in headings only
    
    Examples:
        # All BGB sections about "Schadensersatz"
        {"law_id": "bgb", "contains": "Schadensersatz"}
        
        # Laws amended in 2023
        {"effective_after": "2023-01-01", "effective_before": "2023-12-31"}
        
        # All active EU regulations
        {"jurisdiction": "EU", "law_type": "Verordnung", "status": "active"}
    """
```

### 3. Citation Lookup

```python
@mcp.tool
async def get_law(
    citation: str,
    date: str | None = None,
    include_context: bool = True
) -> Document:
    """Direct lookup by legal citation.
    
    Supports formats:
        - "BGB § 823"
        - "§ 823 BGB"
        - "BGB § 823 Abs. 1"
        - "Art. 1 GG"
        - "DSGVO Art. 17"
        - "TFEU Article 101"
    """
```

### 4. Hybrid Query

```python
@mcp.tool
async def hybrid_search(
    semantic_query: str | None = None,
    filters: dict | None = None,
    full_text: str | None = None,
    weights: dict = {"semantic": 0.5, "bm25": 0.3, "filter": 0.2}
) -> list[SearchResult]:
    """Combine semantic search with structured filtering.
    
    Example:
        hybrid_search(
            semantic_query="consumer protection in online purchases",
            filters={"jurisdiction": "EU", "law_type": "Richtlinie"},
            full_text="Widerruf OR Rücktritt"
        )
    """
```

### 5. Related Documents

```python
@mcp.tool
async def find_related(
    document_id: str,
    relation_type: str = "similar"  # similar, cites, cited_by, amends
) -> list[Document]:
    """Find documents related to a given document."""
```

### 6. Version History

```python
@mcp.tool
async def law_history(
    law_id: str,
    section_ref: str | None = None
) -> VersionHistory:
    """Get complete amendment history of a law or section."""

@mcp.tool
async def compare_versions(
    law_id: str,
    section_ref: str,
    version_a: str,
    version_b: str
) -> VersionDiff:
    """Compare two versions of a legal section."""
```

---

## Data Sources

### German Federal Law (Phase 1 - MVP)

**Primary Source: gesetze-im-internet.de** ✅ VERIFIED

- Official government portal (Federal Ministry of Justice)
- **TOC XML**: `https://www.gesetze-im-internet.de/gii-toc.xml` (~6,871 laws)
- **Per-law downloads**: `https://www.gesetze-im-internet.de/{abbrev}/xml.zip`
- **DTD Schema**: `https://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd`
- Updated regularly after Bundesgesetzblatt publication
- **Free for reuse**: "zur freien Nutzung und Weiterverwendung"
- **robots.txt**: Fully permissive

**Download approach**:
1. Fetch `gii-toc.xml` (table of contents)
2. Parse ~6,871 law entries with individual zip URLs
3. Download each zip, extract single XML file
4. Incremental updates: compare TOC XML periodically

**Schema verified** (see Task-01 research):
- Root: `<dokumente>` containing multiple `<norm>` elements
- Each `<norm>`: `<metadaten>` + `<textdaten>`
- Key fields: `jurabk`, `enbez`, `langue`, `standangabe`, `gliederungseinheit`

**Backup: rechtsprechung-im-internet.de**
- Court decisions
- Similar XML format expected

### EU Law (Phase 2)

**Primary Source: EUR-Lex**
- REST API available: `https://eur-lex.europa.eu/eurlex-ws-client-doc/`
- SPARQL endpoint for complex queries
- Cellar API for raw documents
- All official EU languages

**Features:**
- Regulations, Directives, Decisions
- ECJ/CFI case law
- Official Journal
- Consolidated versions

### German State Law (Phase 3)

16 state law portals (varying quality):
- landesrecht.bayern.de (BY)
- landesrecht.nrw.de (NW)
- etc.

### US Law (Phase 4)

- **Congress.gov API** - Federal legislation
- **CourtListener API** - Case law (Free Law Project)
- **Legal Information Institute** - Cornell Law

### Other Jurisdictions (Future)

| Country | Source | Format |
|---------|--------|--------|
| UK | legislation.gov.uk | XML API |
| Switzerland | fedlex.admin.ch | XML |
| Austria | ris.bka.gv.at | XML |
| France | legifrance.gouv.fr | XML |

---

## Embedding Strategy

### Model Selection

**Option 1: Local Model (Recommended for MVP)**
```python
from sentence_transformers import SentenceTransformer

# Multilingual model with German support
model = SentenceTransformer('paraphrase-multilingual-mpnet-base-v2')
# Dimensions: 768
# Languages: 50+ including German
# Speed: ~100ms per embedding on CPU
```

**Option 2: German-Specific Model**
```python
model = SentenceTransformer('deutsche-telekom/gbert-large-paraphrase-cosine')
# Better German understanding
# Larger model, slower
```

**Option 3: API Models (Better Quality, Costs $$)**
```python
# OpenAI
embedding = openai.embeddings.create(
    model="text-embedding-3-large",
    input=text
)

# Cohere (multilingual)
embedding = cohere.embed(
    texts=[text],
    model="embed-multilingual-v3.0"
)
```

**Recommendation:** Start with local `paraphrase-multilingual-mpnet-base-v2` for MVP, allow configuration for API models.

### Embedding Pipeline

```python
class EmbeddingPipeline:
    def __init__(self, model_name: str = "paraphrase-multilingual-mpnet-base-v2"):
        self.model = SentenceTransformer(model_name)
    
    def embed_document(self, doc: Document) -> dict[str, list[float]]:
        """Generate embeddings at all applicable levels."""
        embeddings = {}
        
        if doc.chunk_level == "law":
            # Law level: title + summary + section headings
            text = f"{doc.full_name}\n{doc.summary}\n" + \
                   "\n".join(s.heading for s in doc.sections[:50])
            embeddings["law"] = self.model.encode(text)
        
        if doc.chunk_level in ["section", "paragraph"]:
            # Section level: heading + full text
            text = f"{doc.heading}\n{doc.text}"
            embeddings["section"] = self.model.encode(text)
        
        return embeddings
    
    def batch_embed(self, docs: list[Document], batch_size: int = 32):
        """Efficient batch embedding for bulk ingestion."""
        texts = [self._prepare_text(doc) for doc in docs]
        embeddings = self.model.encode(texts, batch_size=batch_size, show_progress_bar=True)
        return embeddings
```

---

## Ingestion Pipeline

### Step 1: Download

```python
class DataDownloader:
    async def download_german_federal(self, output_dir: Path):
        """Download all German federal laws as XML."""
        # TODO: Research exact endpoint
        # Expected: ZIP file with XML files per law
        pass
    
    async def download_eurlex(self, output_dir: Path, doc_types: list[str]):
        """Download EU documents via EUR-Lex API."""
        pass
```

### Step 2: Parse XML

```python
class XMLParser:
    def parse_german_law(self, xml_path: Path) -> Law:
        """Parse gesetze-im-internet.de XML format."""
        tree = etree.parse(xml_path)
        
        law = Law(
            law_id=tree.find("//metadaten/jurabk").text.lower(),
            full_name=tree.find("//metadaten/langue").text,
            # ... more fields
        )
        
        for norm in tree.findall("//norm"):
            section = self._parse_section(norm)
            law.sections.append(section)
        
        return law
```

### Step 3: Chunk

```python
class Chunker:
    def chunk_law(self, law: Law) -> list[Document]:
        """Create documents at each hierarchy level."""
        documents = []
        
        # Level 1: Law document
        documents.append(Document(
            id=f"{law.law_id}",
            chunk_level="law",
            text=self._law_summary(law),
            # ...
        ))
        
        # Level 2-5: Sections and subdivisions
        for section in law.sections:
            documents.extend(self._chunk_section(section))
        
        return documents
```

### Step 4: Embed

```python
class Embedder:
    async def embed_documents(self, documents: list[Document]):
        """Generate and store embeddings for all documents."""
        batch_size = 32
        
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i+batch_size]
            embeddings = self.model.encode([d.text for d in batch])
            
            for doc, embedding in zip(batch, embeddings):
                await self.store.save_embedding(doc.id, embedding)
```

### Step 5: Store

```python
class DocumentStore:
    def __init__(self, db_path: str, vector_db: str = "chromadb"):
        self.db = sqlite3.connect(db_path)
        self.vectors = chromadb.PersistentClient(path=vector_db)
    
    async def store_document(self, doc: Document, embedding: list[float]):
        """Store document in both SQL and vector DB."""
        # SQL for structured queries
        self.db.execute("""
            INSERT INTO documents (id, law_id, section_ref, text, ...)
            VALUES (?, ?, ?, ?, ...)
        """, (doc.id, doc.law_id, doc.section_ref, doc.text, ...))
        
        # Vector DB for semantic search
        self.vectors.add(
            ids=[doc.id],
            embeddings=[embedding],
            metadatas=[{"law_id": doc.law_id, "chunk_level": doc.chunk_level}]
        )
```

### Step 6: Index

```python
class Indexer:
    def build_fts_index(self):
        """Build full-text search index."""
        self.db.execute("""
            INSERT INTO documents_fts(id, heading, text)
            SELECT id, heading, text FROM documents
        """)
    
    def build_citation_graph(self):
        """Extract and index cross-references between laws."""
        # Parse citations like "§ 823 BGB", "Art. 17 DSGVO"
        # Store in citations table
        pass
```

---

## Incremental Updates

### Update Detection

```python
class UpdateDetector:
    async def check_for_updates(self) -> list[LawUpdate]:
        """Check official sources for new/changed laws."""
        updates = []
        
        # Option 1: RSS feed (if available)
        feed = await self.fetch_rss("https://gesetze-im-internet.de/rss")
        
        # Option 2: Compare checksums
        current_index = await self.fetch_law_index()
        for law_id, checksum in current_index.items():
            stored_checksum = await self.get_stored_checksum(law_id)
            if checksum != stored_checksum:
                updates.append(LawUpdate(law_id, "modified"))
        
        return updates
```

### Update Processing

```python
class UpdateProcessor:
    async def process_update(self, update: LawUpdate):
        """Process a single law update."""
        # 1. Download updated XML
        xml = await self.downloader.download_law(update.law_id)
        
        # 2. Parse and compare
        new_law = self.parser.parse(xml)
        old_law = await self.store.get_law(update.law_id)
        
        # 3. Detect changes at section level
        changes = self.diff_laws(old_law, new_law)
        
        # 4. Update only changed sections
        for change in changes:
            if change.type == "modified":
                # Mark old version as superseded
                await self.store.supersede_document(change.old_doc_id)
                # Add new version
                await self.store.store_document(change.new_doc)
                # Re-embed only changed sections
                embedding = self.embedder.embed(change.new_doc)
                await self.store.update_embedding(change.new_doc.id, embedding)
        
        # 5. Update version history
        await self.store.add_version_entry(update.law_id, changes)
```

---

## Technology Stack

### Core Dependencies

```toml
[project]
dependencies = [
    # MCP Framework
    "fastmcp>=2.14.0",
    "mcp-refcache>=0.1.0",
    
    # Data Processing
    "lxml>=5.0.0",              # XML parsing
    "httpx>=0.27.0",            # Async HTTP
    
    # Embeddings
    "sentence-transformers>=2.2.0",
    "torch>=2.0.0",             # Or use CPU-only
    
    # Vector Database
    "chromadb>=0.4.0",          # Lightweight, embeddable
    
    # Database
    "aiosqlite>=0.19.0",        # Async SQLite
    
    # Search
    "rank-bm25>=0.2.0",         # BM25 full-text ranking
]

[dependency-groups]
dev = [
    "pytest>=9.0.0",
    "pytest-asyncio>=1.3.0",
]
```

### Optional Dependencies

```toml
[project.optional-dependencies]
postgres = [
    "asyncpg>=0.29.0",
    "pgvector>=0.2.0",
]
api-embeddings = [
    "openai>=1.0.0",
    "cohere>=4.0.0",
]
```

---

## Tasks

### Phase 1: MVP - German Federal Law

| Task ID | Description | Status | Effort | Depends On |
|---------|-------------|--------|--------|------------|
| Task-01 | Research gesetze-im-internet.de XML format & download | 🟢 | 2h | - |
| Task-02 | Add dependencies + design embedding architecture | 🟡 | 4h | Task-01 |
| Task-03 | Set up `data/` directory structure and .gitattributes | 🟢 | 30m | Task-02 |
| Task-04 | Implement XML downloader (`scripts/download_corpus.py`) | 🟢 | 2h | Task-03 |
| Task-04b | Download full corpus (6,871 laws) | 🟢 | 1h | Task-04 |
| Task-04c | Analyze corpus structure (`scripts/analyze_corpus.py`) | 🟢 | 1h | Task-04b |
| Task-05 | Implement XML parser / LangChain loader | ⚪ | 4h | Task-04c |
| Task-06 | Implement multi-level chunker (`app/ingestion/chunk.py`) | ⚪ | 3h | Task-05 |
| Task-07 | Implement embedding pipeline (`app/ingestion/embed.py`) | ⚪ | 3h | Task-06 |
| Task-08 | Implement document store (`app/storage/documents.py`) | ⚪ | 4h | Task-06 |
| Task-09 | Implement vector store (`app/storage/vectors.py`) | ⚪ | 2h | Task-07 |
| Task-10 | Implement semantic search tool | ⚪ | 2h | Task-09 |
| Task-11 | Implement structured query tool | ⚪ | 3h | Task-08 |
| Task-12 | Implement citation lookup tool | ⚪ | 2h | Task-08 |
| Task-13 | Implement hybrid search tool | ⚪ | 3h | Task-10, Task-11 |
| Task-14 | Version tracking and history tools | ⚪ | 3h | Task-08 |
| Task-15 | RefCache integration for full texts | ⚪ | 2h | Task-08 |
| Task-16 | CLI for data ingestion (`legal-mcp ingest`) | ⚪ | 3h | Task-07 |
| Task-17 | Download initial corpus & commit to git | ⚪ | 2h | Task-16 |
| Task-18 | Configuration system (`app/config.py`) | ⚪ | 2h | Task-03 |
| Task-19 | Tests with real German laws | ⚪ | 4h | Task-17 |
| Task-20 | Documentation and usage examples | ⚪ | 3h | Task-19 |

**Estimated Total: ~48 hours**

### Task Details

#### Task-01: Research gesetze-im-internet.de ✅ COMPLETE

**Findings documented in**: `Task-01-Research-GII/scratchpad.md`

**Key discoveries**:
- No single bulk download - individual zips per law via TOC XML
- TOC: `https://www.gesetze-im-internet.de/gii-toc.xml` (~6,871 laws)
- Per-law: `https://www.gesetze-im-internet.de/{abbrev}/xml.zip`
- DTD schema: `https://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd`
- Update mechanism: Compare TOC XML periodically
- Legal: Explicitly free for reuse ("zur freien Nutzung und Weiterverwendung")
- robots.txt: Fully permissive

#### Task-03: Set up data/ directory
- Create directory structure (`raw/`, `processed/`, `embeddings/`)
- Add `.gitattributes` for LFS if needed (large files)
- Add `.gitignore` for temp files
- Document in README

#### Task-16: CLI for data ingestion
```bash
legal-mcp ingest download [--jurisdiction de-federal] [--laws bgb,stgb]
legal-mcp ingest process [--incremental]
legal-mcp ingest embed [--incremental] [--model name]
legal-mcp ingest status  # Show corpus stats
```

#### Task-17: Initial corpus download
- Download all German federal laws (~6000)
- Process and chunk
- Generate embeddings
- Commit to git (may need Git LFS for embeddings)
- Document in CHANGELOG

### Phase 2: Version Tracking & Updates

| Task ID | Description | Status | Effort |
|---------|-------------|--------|--------|
| Task-16 | Update detection mechanism | ⚪ | 4h |
| Task-17 | Incremental update processor | ⚪ | 4h |
| Task-18 | Version comparison/diff tool | ⚪ | 3h |

### Phase 3: Expand Sources

| Task ID | Description | Status | Effort |
|---------|-------------|--------|--------|
| Task-19 | EUR-Lex API integration | ⚪ | 8h |
| Task-20 | German court decisions | ⚪ | 6h |
| Task-21 | Citation graph extraction | ⚪ | 4h |

### Phase 4: Additional Jurisdictions

| Task ID | Description | Status | Effort |
|---------|-------------|--------|--------|
| Task-22 | US Congress.gov integration | ⚪ | 8h |
| Task-23 | CourtListener case law | ⚪ | 6h |
| Task-24 | German state laws | ⚪ | 12h |

---

## Open Research Questions

### Critical (Block MVP)

- [x] **gesetze-im-internet.de XML format**: DTD v1.01, per-law zips via TOC XML
- [x] **Update mechanism**: Compare TOC XML periodically (no RSS/API)
- [x] **Storage requirements**: ~6,871 laws, ~500MB-1GB raw XML, embeddings TBD

### Important (Inform Design)

- [ ] **Optimal chunk size**: What's the best granularity for retrieval?
- [ ] **Embedding model comparison**: Which model works best for German legal text?
- [ ] **Search quality metrics**: How to evaluate retrieval quality?

### Nice to Have

- [ ] **Multi-language**: How to handle EU documents in 24 languages?
- [ ] **Legal concept ontology**: Can we map to standard legal taxonomies?

---

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| XML format undocumented | High | Medium | Start with sample files, reverse engineer |
| Large corpus size | Medium | Medium | Start with subset (BGB, StGB, GG), expand later |
| Embedding quality | Medium | Medium | Evaluate multiple models, allow configuration |
| Update complexity | Medium | High | Start with full reindex, optimize later |
| Storage costs | Low | Low | Use efficient formats, compression |

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-01-07 | Start with German law | Best documentation, existing references |
| 2025-01-23 | Abandon web scraping | Too fragile, prefer official XML sources |
| 2025-01-23 | Per-law downloads | No bulk endpoint exists, TOC+individual zips |
| 2026-01-23 | Hybrid retrieval | Need both semantic AND structured queries |
| 2026-01-23 | Multi-level chunking | Different queries need different granularity |
| 2026-01-23 | Local embeddings first | Avoid API costs, maintain privacy |
| 2026-01-23 | SQLite + ChromaDB | Simple, no external services, portable |
| 2026-01-23 | Git-track legal corpus | Full traceability, reproducibility, pre-seeded releases |
| 2026-01-23 | Configurable data sources | Support bundled, custom, and live update modes |
| 2026-01-23 | Pre-seed during development | Download once, ship with package, works offline |

---

## References

### Official Data Sources
- [gesetze-im-internet.de](https://www.gesetze-im-internet.de) - German federal law
- [rechtsprechung-im-internet.de](https://www.rechtsprechung-im-internet.de) - Court decisions
- [EUR-Lex](https://eur-lex.europa.eu) - EU law

### Technical Resources
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Sentence Transformers](https://www.sbert.net/)
- [lxml Documentation](https://lxml.de/)

### Related Projects
- [deutschland](https://github.com/bundesAPI/deutschland) - German government APIs
- [DejureMcp](https://github.com/halllo/DejureMcp) - Original C# reference (in `.agent/tmp/`)
- [openlegaldata.io](https://openlegaldata.io) - Open legal data initiative

---

## Next Steps

1. ✅ **Complete**: Task-01 - Research gesetze-im-internet.de (see `Task-01-Research-GII/scratchpad.md`)
2. ✅ **Complete**: Corpus downloaded - 6,871 laws, 290 MB in `data/raw/de-federal/`
3. ✅ **Complete**: Corpus analyzed - 129,725 norms, 306,224 paragraphs
4. ✅ **Complete**: Dependencies installed (langchain, langgraph, chromadb, sentence-transformers, lxml)
5. 🟡 **In Progress**: Task-02 - Multi-level embedding architecture design (see `Task-02-Embedding-Architecture/scratchpad.md`)
6. **Next**: Finalize embedding decisions (3-level + single collection recommended)
7. **Then**: Build `GermanLawXMLLoader` (custom LangChain document loader)
8. **Then**: Build multi-level chunking pipeline
9. **Then**: Implement embedding + ChromaDB storage

## Session Progress (2025-01-23)

### Completed
- Downloaded entire German federal law corpus (6,871 files, 290 MB)
- Analyzed corpus structure (129,725 norms, 306,224 paragraphs, 154 MB text)
- Installed LangChain/LangGraph dependencies
- Researched LangChain splitters (no XML splitter, but HTML concepts apply)
- Documented multi-level embedding architecture design

### Key Findings
- **No bulk download** - must use TOC XML + individual zips
- **Corpus contains**: Gesetze (33%), Verordnungen (44%), Bekanntmachungen (8%), Abkommen (6%)
- **Median norm size**: 497 chars (perfect for single embedding)
- **Large norms (P99)**: 10,295 chars (need sub-chunking)
- **Recommended**: 3-level embeddings (law, norm, paragraph) + single collection with metadata filtering

### Files Created
- `scripts/download_corpus.py` - Async corpus downloader
- `scripts/analyze_corpus.py` - Corpus analysis script
- `data/raw/de-federal/*.xml` - 6,871 law XML files
- `Task-01-Research-GII/scratchpad.md` - Download research
- `Task-02-Embedding-Architecture/scratchpad.md` - Architecture design

---

## Session Handoff Template

When handing off to next session, provide:

```
[Legal-MCP: Task-XX Implementation]

Context: 
- Goal 02 scratchpad has full architecture
- Current focus: Task-XX (description)
- Previous tasks completed: Task-01 through Task-(XX-1)

What Was Done:
- Bullet points of completed work

Current Task:
- Specific files to create/modify
- Expected behavior
- Test requirements

Blockers:
- Any issues discovered
```
