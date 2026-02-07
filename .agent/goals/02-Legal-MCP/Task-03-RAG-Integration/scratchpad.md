# Task-03: RAG Integration for Legal Q&A

> **Status**: 🟢 Complete (Phase 1, 2, 3)
> **Started**: 2025-01-25
> **Updated**: 2025-01-25
> **Depends On**: Task-02 (Embedding Architecture) ✅ Complete

---

## Objective

Build a Retrieval-Augmented Generation (RAG) pipeline that enables natural language Q&A over the German law corpus. Users should be able to ask legal questions and receive accurate answers with proper citations.

---

## Prerequisites (from Task-02) ✅

- ✅ **193,371 documents** embedded in ChromaDB
- ✅ **58,255 HTML files** from 2,629 laws
- ✅ TEI backend running (ports 8011-8014, 4 replicas)
- ✅ Semantic search working (verified with priority laws)
- ✅ All 76 tests passing
- ✅ `GermanLawEmbeddingStore.search()` method available

---

## Research Findings

### LLM Backend Decision: LiteLLM

After research, **LiteLLM** is the best choice for LLM integration:

**Why LiteLLM:**
- Unified OpenAI-compatible interface for 100+ providers
- Native support for Ollama, vLLM, OpenAI, Anthropic, etc.
- Async support with `acompletion()`
- Streaming support
- Built-in retry/fallback logic
- No vendor lock-in - easily switch providers

**Provider Configuration:**
```python
# Ollama (local, for development)
model="ollama/llama2"
api_base="http://localhost:11434"

# vLLM (local GPU, for production)
model="openai/uai/lm-small"  # served-model-name from docker-compose
api_base="http://localhost:7373/v1"

# OpenAI (external, for testing quality)
model="gpt-4o-mini"
```

### Existing Infrastructure

**Already Available:**
- `app/ingestion/embeddings.py` - `GermanLawEmbeddingStore` with `search()` method
- `app/ingestion/tei_client.py` - TEI client with load balancing (4 replicas)
- `docker-compose.gpu.yml` - vLLM configured on port 7373, rerankings service (replicas=0)
- `app/tools/german_laws.py` - Existing MCP tools pattern with `@cache.cached`

**Reranker (Optional):**
- `rerankings` service in docker-compose uses `BAAI/bge-reranker-large`
- Currently disabled (replicas=0), can enable later
- TEI reranker uses `/rerank` endpoint with `{"query": "...", "texts": [...]}`

### Tool Return Type Pattern

From `.rules`:
> **`@cache.cached` tools MUST return `dict[str, Any]`**
> Decorator wraps raw return in cache response

---

## Implementation Plan

### Phase 1: Core RAG Pipeline (Current Focus)

**Files to Create:**
| File | Purpose |
|------|---------|
| `app/rag/__init__.py` | Package exports |
| `app/rag/pipeline.py` | Core RAG orchestration |
| `app/rag/llm_client.py` | LiteLLM wrapper with provider abstraction |
| `app/rag/prompts.py` | German legal system/user prompt templates |
| `app/rag/context.py` | Context builder with citation formatting |

**Config Additions (`app/config.py`):**
```python
# LLM configuration
llm_provider: Literal["ollama", "vllm", "openai"] = "ollama"
llm_model: str = "llama3.2"  # Model name for provider
llm_api_base: str | None = None  # Override API base URL
llm_temperature: float = 0.1  # Low for factual responses
llm_max_tokens: int = 1024  # Response length limit
```

**Pipeline Flow:**
```
User Question
     │
     ▼
┌─────────────────┐
│  Semantic Search │ ◄── ChromaDB via GermanLawEmbeddingStore.search()
│  (TEI Embeddings)│
└────────┬────────┘
         │ Top 10-20 candidates
         ▼
┌─────────────────┐
│  Context Builder│ ◄── Format with [1], [2], [3] citations
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│      LLM        │ ◄── LiteLLM (Ollama/vLLM/OpenAI)
│  (Generation)   │
└────────┬────────┘
         │
         ▼
   Structured Response
   {answer, sources, model}
```

### Phase 2: MCP Tool Integration

**File:** `app/tools/ask_legal.py`

```python
@cache.cached(namespace="legal_qa")
async def ask_legal_question(
    question: str,
    max_sources: int = 5,
    include_full_text: bool = False,
) -> dict[str, Any]:
    """Answer legal questions using German law knowledge base."""
```

**Response Structure:**
```python
{
    "question": "Was sind die Kündigungsfristen im Arbeitsrecht?",
    "answer": "Die Kündigungsfristen im Arbeitsrecht sind in § 622 BGB geregelt...",
    "sources": [
        {
            "citation": "[1]",
            "law": "BGB",
            "norm_id": "§ 622",
            "title": "Kündigungsfristen bei Arbeitsverhältnissen",
            "excerpt": "Die Kündigungsfrist beträgt vier Wochen...",
            "similarity": 0.89
        }
    ],
    "model": "ollama/llama3.2",
    "retrieval_count": 10,
    "generation_time_ms": 1234
}
```

### Phase 3: Reranking (Future)

- Enable `rerankings` service in docker-compose
- Add `app/rag/reranker.py` with TEI reranker client
- Two-stage retrieval: search(top_k=20) → rerank → top_k=5

---

## Prompts

### System Prompt (German)

```text
Du bist ein Rechtsassistent für deutsches Recht. Deine Aufgabe:

1. Beantworte Fragen NUR basierend auf den bereitgestellten Gesetzestexten
2. Zitiere IMMER die relevanten Paragraphen mit [1], [2], etc.
3. Wenn die Antwort nicht aus den Quellen hervorgeht, sage: "Diese Frage kann ich anhand der verfügbaren Gesetzestexte nicht beantworten."
4. Gib keine Rechtsberatung - verweise auf professionelle Beratung für konkrete Fälle
5. Antworte präzise und strukturiert

Wichtig: Du darfst KEINE Informationen erfinden oder Paragraphen zitieren, die nicht in den Quellen stehen.
```

### Context Template

```text
=== Relevante Gesetzestexte ===

[1] {law_abbrev} {norm_id} - {title}
{content}

[2] {law_abbrev} {norm_id} - {title}
{content}

...

=== Frage ===
{user_question}
```

---

## Test Queries

| Query | Expected Sources | Purpose |
|-------|------------------|---------|
| "Was sind die Kündigungsfristen im Arbeitsrecht?" | BGB § 622 | Employment law |
| "Welche Grundrechte sind in der Verfassung verankert?" | GG Art. 1-19 | Constitutional law |
| "Wie ist Betrug im Strafrecht definiert?" | StGB § 263 | Criminal law |
| "Was regelt das Mietrecht bei Mieterhöhungen?" | BGB §§ 557-561 | Rental law |
| "Welche Erbfolge gilt ohne Testament?" | BGB §§ 1922-1941 | Inheritance law |
| "Was ist ein Kaufvertrag?" | BGB § 433 | Contract law |

---

## Dependencies to Add

```bash
uv add litellm
```

---

## Success Criteria

1. **Accuracy**: Answers cite correct law sections
2. **Citations**: Every claim has [n] reference to sources
3. **Relevance**: Retrieved documents match query topic
4. **Latency**: Response < 5 seconds with local LLM
5. **Graceful degradation**: Clear message for out-of-scope questions
6. **No hallucination**: Model only uses provided context

---

## Implementation Checklist

### Phase 1: Core Pipeline ✅ Complete
- [x] Add `litellm` dependency
- [x] Create `app/rag/__init__.py`
- [x] Create `app/rag/prompts.py` with German templates
- [x] Create `app/rag/context.py` for citation formatting
- [x] Create `app/rag/llm_client.py` with LiteLLM wrapper
- [x] Create `app/rag/pipeline.py` with RAGPipeline class
- [x] Add LLM config options to `app/config.py`
- [x] Tested with vLLM (Mistral) ✅

### Phase 2: MCP Tool ✅ Complete
- [x] Create `app/tools/ask_legal.py`
- [x] Register tool in `app/server.py`
- [x] Add tool factory to `app/tools/__init__.py`
- [x] Manual testing with vLLM ✅

### Phase 3: Testing & Reranker ✅ Complete
- [x] Create `tests/test_rag_pipeline.py` (30 tests)
- [x] Create `tests/test_ask_legal.py` (15 tests)
- [x] Integration tests with mock LLM
- [x] End-to-end tests with vLLM ✅
- [x] Create `app/rag/reranker.py` for two-stage retrieval
- [x] Integrate reranker into pipeline (optional, graceful fallback)
- [x] Fix docker-compose.gpu.yml vLLM compatibility issues

### Phase 4: Optimization (Future)
- [ ] Fix TEI reranker model compatibility (BGE models failing)
- [ ] Add response caching
- [ ] Tune prompts for German legal context
- [ ] Benchmark latency and quality
- [ ] Improve search relevance (§ 263 StGB not ranking for "Betrug")

---

## Notes

- Start with Ollama for fast iteration (no GPU required for small models)
- vLLM is configured but uses GPU memory alongside TEI
- Keep prompts in German for better legal context
- Citation format `[n]` matches sources list indexing
- Use low temperature (0.1) for factual consistency

---

## Completed Work (2025-01-25)

### Files Created
| File | Lines | Purpose |
|------|-------|---------|
| `app/rag/__init__.py` | 90 | Package exports |
| `app/rag/prompts.py` | 173 | German legal prompt templates |
| `app/rag/context.py` | 176 | Context builder with citations |
| `app/rag/llm_client.py` | 311 | LiteLLM wrapper (Ollama/vLLM/OpenAI) |
| `app/rag/pipeline.py` | 420 | RAGPipeline with reranker support |
| `app/rag/reranker.py` | 249 | TEI reranker client |
| `app/tools/ask_legal.py` | 163 | MCP tool for legal Q&A |
| `tests/test_rag_pipeline.py` | 565 | 30 unit tests for RAG |
| `tests/test_ask_legal.py` | 293 | 15 unit tests for tool |

### Files Modified
| File | Changes |
|------|---------|
| `pyproject.toml` | Added `litellm` dependency |
| `app/config.py` | Added LLM + reranker config |
| `app/tools/__init__.py` | Export `create_ask_legal_question` |
| `app/server.py` | Register `ask_legal_question` tool |
| `docker-compose.gpu.yml` | Fixed vLLM deprecated args, added reranker service, reduced GPU memory |

### Test Results
- **121 tests passing** (up from 76)
- 30 new RAG pipeline tests
- 15 new ask_legal tool tests
- All linting passing (ruff check + format)

### Configuration Options Added
```bash
# LLM (defaults match docker-compose.gpu.yml vLLM)
LLM_PROVIDER=vllm                # Default: vllm (not ollama)
LLM_MODEL=uai/lm-small           # Matches vLLM served-model-name
LLM_API_BASE=http://localhost:7373/v1
LLM_TEMPERATURE=0.1              # Low for factual
LLM_MAX_TOKENS=1024              # Response limit

# Reranker
RERANKER_URL=http://localhost:8020  # TEI reranker
```

### Manual Testing Results ✅

**vLLM tested successfully with:**
- Mistral 7B AWQ quantized model
- GPU memory: 0.55 utilization (fits alongside TEI embeddings)
- Context length: 4096 tokens
- Response time: ~15-25 seconds per query

**Test queries passed:**
- "Was ist ein Kaufvertrag?" → Answered with BGB references
- "Welche Grundrechte gibt es?" (with GG filter) → Listed Art. 1 etc.

**Known Issues:**
- Reranker (BGE models) not compatible with TEI version - graceful fallback works
- Some semantic search queries don't find optimal matches (e.g., "Betrug" doesn't find § 263 StGB directly)

### Running the Stack

```bash
# Start all services (embeddings + vLLM)
docker compose -f docker-compose.gpu.yml up -d embeddings vllm

# Verify
docker ps  # Should show embeddings (2 replicas) + vllm healthy
curl http://localhost:7373/v1/models

# Test RAG pipeline
USE_TEI=true python3 -c "
import asyncio
from app.rag.pipeline import RAGPipeline

async def test():
    pipeline = RAGPipeline(max_sources=3, use_reranker=False)
    result = await pipeline.ask('Was ist ein Kaufvertrag?')
    print(result.answer[:300])
asyncio.run(test())
"

# Run MCP server
USE_TEI=true uv run legal-mcp stdio
```