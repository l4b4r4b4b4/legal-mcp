# Goal 04: German State Law Sources & Indexing (Landesrecht) — Knowledge Base Expansion

## Tasks (this goal)
- Task 01 (Berlin Open Data / CKAN quick test): `.agent/goals/04-German-State-Law-Sources-And-Indexing/Task-01-Berlin-Open-Data-Quick-Test/scratchpad.md`
- Task 02 (Berlin on-the-fly retrieval + optional embed on demand): `.agent/goals/04-German-State-Law-Sources-And-Indexing/Task-02-Berlin-On-The-Fly-Retrieval-And-Embedding/scratchpad.md` (planned)

## Phase A/B Notes (Berlin portal discoveries; 2026-01-25)

This section captures what we have validated so far for the Berlin pilot. It is intentionally evidence-driven and focuses on access mechanics, identifiers, and architectural implications.

### Policy constraint: robots.txt disallows automated crawling for generic user agents (important)

**Finding (robots.txt):**
- `User-agent: *` → `Disallow: /`
- Sitemap is present (`sitemapindex.xml`) but does not override the global disallow rule.

**Implication (engineering decision):**
- We should **not** implement or ship a “bulk downloader” for Berlin that crawls `gesetze.berlin.de` (whether via Playwright or HTTP/2) under a generic user agent.
- Any “full corpus locally” plan requires either:
  - explicit permission/allowlisting from the site owner, and/or
  - an official bulk/export interface, and/or
  - a separately licensed dataset.
- This strongly pushes us toward **on-the-fly retrieval** (bounded, user-initiated) and/or ingestion from user-provided artifacts.

### Direction update: Berlin as on-the-fly retrieval + (optional) embed-on-demand, not bulk-at-startup

Given the SPA + backend session/CSRF behavior, plus robots policy constraints, the pragmatic “minimal functional” implementation path for Goal 04 is:

1) **On-the-fly retrieval tool** (MCP):
   - Fetch a single Berlin document by `document_id` (or canonical URL)
   - Use Playwright as the current reliable retriever for SPA-rendered content
   - Return extracted text + safe metadata (no cookies/tokens), store large payloads by reference (RefCache)

2) **Optional “ingest this one” tool** (MCP):
   - For one retrieved document, chunk + embed + upsert into a dedicated Berlin collection
   - Enables incremental local corpus growth driven by actual usage, without bulk crawling

3) **Search tool** (MCP):
   - Provide Berlin-specific semantic search over the embedded subset
   - Result filtering should include `jurisdiction=de-state:berlin` and `source_name=gesetze-berlin`

4) **Reuse existing Option A artifacts and normalizer as offline inputs**
   - Keep the Playwright batch extractor and normalizer scripts as R&D tools for schema shaping and debugging
   - Do not treat them as the production ingestion path unless permissions change

**Progress update (Option A) — dev shell / Playwright Chromium now works without manual env var**

Note: Option A remains valuable as a “truth oracle” and schema exploration tool (document structure, metadata extraction, normalization). It is not (currently) a compliant bulk ingestion strategy given robots policy constraints.

**Problem observed (earlier):**
- Playwright Chromium (downloaded binary) failed in the Nix/FHS environment with:
  - `error while loading shared libraries: libgbm.so.1: cannot open shared object file: No such file or directory`

**Root cause (confirmed):**
- The FHS rootfs did not contain `/usr/lib64/libgbm.so.1`.
- `libgbm.so.1` *does* exist in the Nix store under `*-mesa-libgbm-*` derivations (store paths varied).

**Fix implemented:**
- Updated `flake.nix` dev shell startup logic to automatically prepend the correct library path by runtime discovery:
  - Find a store directory matching `/nix/store/*-mesa-libgbm-*/lib`
  - Prepend it to `LD_LIBRARY_PATH` (ahead of other `LD_LIBRARY_PATH` content)
  - Warn if it cannot be found (so failures are self-explanatory)

**Verification (post-fix):**
- `uv run python -c "... playwright ... chromium.launch(...)"` succeeds:
  - Output: `playwright chromium launch: ok`

### Berlin Open Data (CKAN) quick-test conclusion (datenregister.berlin.de)

**What we tested (bounded):**
- CKAN API is reachable and searchable.
- Bounded keyword searches over Berlin’s CKAN registry for legal-ish terms:
  - `gesetz` → small, noisy result set (e.g., “Senatsvorlagen” archives and non-law datasets that mention “Gesetz”)
  - `verordnung` → similarly small and noisy
  - `gvbl` → results dominated by planning/GIS items; no Gazette corpus surfaced
  - `gesetzblatt`, `satzung`, `rechtsprechung`, `vorschrift` → no results in this quick probe

**What we found (representative examples):**
- “Senatsvorlagen” datasets exist and are typically **PDF resources** (archival / legislative documents), not consolidated law texts.
- Some “Verordnung”-related datasets are **GIS services (WMS/WFS)** that model ordinance coverage areas, explicitly stating that the legally binding publication is the **GVBl.**, i.e., these are not authoritative text sources.
- Some technically machine-readable endpoints (HTML/JSON/XML/CSV/XLS) appear in the registry, but licensing can be `other-closed` or otherwise unclear, making them unusable until verified.

**Decision (preliminary, Phase A):**
- **CKAN is not a viable primary source** for consolidated Berlin law texts (Gesetze/Verordnungen) comparable to the Juris portal.
- CKAN is at best a **narrow supplementary source** (archival PDFs; GIS layers tied to specific ordinances) with careful license vetting.

**Evidence / details:**
- See Task 01: `.agent/goals/04-German-State-Law-Sources-And-Indexing/Task-01-Berlin-Open-Data-Quick-Test/scratchpad.md`

### Berlin portal candidate: `gesetze.berlin.de` (portalId: `bsbe`)

**Observed behavior (important for ingestion design):**
- The portal is an SPA: direct requests to document-like URLs (e.g. `/bsbe/document/<ID>`) return SPA bootstrap HTML and do not contain the actual document text without JavaScript.
- The frontend bundle config exposes an API base path: `/jportal/wsrest/recherche3/`.
- Backend calls appear to require:
  - cookies (the SPA uses `credentials: "include"`)
  - header `JURIS-PORTALID: bsbe`
  - and a CSRF token header `X-CSRF-TOKEN` (when available)
- Direct API calls without the portal header fail; with the portal header, they can fail with `security_notAuthenticated`, indicating additional handshake/auth requirements for some endpoints.

**Discovery mechanism (good signal):**
- The site provides a sitemap index at `https://gesetze.berlin.de/sitemapindex.xml`.
- The sitemap index references at least `sitemap1.xml` and `sitemap2.xml`.
- The sitemap contains many stable, canonical document URLs of the form:
  - `https://gesetze.berlin.de/bsbe/document/<document_id>`
- Example IDs observed: `NJRE000029157`, `NJRE000029191`, … (format suggests “document IDs”, potentially case law and/or other document types; exact categorization still needs confirmation).

**Playwright-assisted validation (single URL; proves “parseable text from /bsbe/document/...”)**
- Using a headless Chromium run, the SPA successfully rendered and exposed a **Landesnorm** document for:
  - `https://gesetze.berlin.de/bsbe/document/jlr-OpenDataBerVBErahmen`
- Best-effort extraction target: `#main`
- Extracted text includes the document header fields (e.g., “Amtliche Abkürzung”, “Ausfertigungsdatum”, “Gültig ab”, “Fundstelle”) and Permalink info.
- The rendered DOM contains stable perma links:
  - `https://gesetze.berlin.de/perma?d=jlr-OpenDataBerVBErahmen`
  - `https://gesetze.berlin.de/perma?j=OpenDataBerV_BE`
  - `https://gesetze.berlin.de/perma?a=OpenDataBerV_BE`

**Concrete backend endpoint paths observed (from browser network; paths only)**
During rendering of the `jlr-...` document, the SPA issued POSTs to:
- `/jportal/wsrest/recherche3/init`
- `/jportal/wsrest/recherche3/search` (multiple times)
- `/jportal/wsrest/recherche3/cmsContent`
- `/jportal/wsrest/recherche3/configUpdate`
- `/jportal/wsrest/recherche3/pricesDefault`
- `/jportal/wsrest/recherche3/document`
- `/jportal/wsrest/recherche3/toc`

**Playwright batch extraction (Option A; bounded; first 10 sitemap IDs)**
- A bounded batch run successfully rendered and extracted **HTML + text** for the first 10 sitemap IDs from the latest discovery snapshot.
- Snapshot used (50 IDs total; in that snapshot all IDs were `NJRE...`):
  - `data/raw/de-state/berlin/discovery/berlin_sitemap_discovery_20260125T182456.392174Z0000.json`
- Batch manifest (successes=10, failures=0):
  - `data/raw/de-state/berlin/playwright-batch/berlin_playwright_batch_20260125T193856_637812Z0000_manifest.json`
- Example extracted decision document (NJRE; Langtext):
  - `data/raw/de-state/berlin/playwright-batch/20260125T193856_637812Z0000_NJRE000029157.json`

**Playwright batch extraction (Option A; bounded; mixed sample = 5 norms + 5 decisions)**
- After expanding discovery to include both sitemaps, we ran a bounded mixed batch to confirm extraction works for both:
  - `jlr-...` (Landesnorm / norms)
  - `NJRE...` (decisions)

Artifacts:
- Mixed-sample snapshot (5 `jlr-` + 5 `NJRE`):
  - `data/raw/de-state/berlin/discovery/berlin_sitemap_discovery_mixed_sample_20260125.json`
- Batch JSONL:
  - `data/raw/de-state/berlin/playwright-batch/berlin_playwright_batch_20260125T213424_579876Z0000.jsonl`
- Batch manifest (successes=10, failures=0, elapsed_seconds=34.553):
  - `data/raw/de-state/berlin/playwright-batch/berlin_playwright_batch_20260125T213424_579876Z0000_manifest.json`

Selected examples (for structure comparison):
- `jlr` norm (selected_selector `#main`, includes TOC):
  - `data/raw/de-state/berlin/playwright-batch/20260125T213424_579876Z0000_jlr-7-L-2LPlFVBEpAnlage.json`
- `NJRE` decision (selected_selector `#main`, may be "Dokument ohne Inhaltsverzeichnis", can be large):
  - `data/raw/de-state/berlin/playwright-batch/20260125T213424_579876Z0000_NJRE000033746.json`

Observed content size differences (same batch):
- `jlr-...` norms in this sample were small (~1.5k–3.2k text chars).
- `NJRE...` decisions ranged up to ~67k text chars and ~104k HTML chars for the largest sample doc.

**Environment workaround (Nix/FHS + Playwright/Chromium)**
- A reliable workaround for Chromium’s GBM dependency in this environment was to ensure the FHS `usr/lib64` directory is ahead of other library paths, e.g.:
  - `LD_LIBRARY_PATH="/nix/store/<...>-legal-mcp-dev-env-fhsenv-rootfs/usr/lib64:$LD_LIBRARY_PATH" ...`
- Without this, Chromium may fail with either:
  - missing `libgbm.so.1`, or
  - `wrong ELF class: ELFCLASS32` (when a 32-bit libgbm is picked up).

**Implication:**
- This validates that sitemap IDs including `jlr-...` correspond to **Landesnorm** content (not only `NJRE...` decisions).
- It also validates that a reproducible programmatic retrieval flow exists via `/jportal/wsrest/recherche3/*` if we can replicate the SPA’s init/session prerequisites and payloads.
- In the short term, Playwright can be used as a “truth oracle” to extract HTML/text for a bounded set of sitemap IDs.

### Updated architecture recommendation (Berlin)

To avoid mixing portal-specific logic into the generic ingestion pipeline, split Berlin ingestion into explicit adapters:

1) **Discovery adapter**
   - Input: sitemap(s) (`sitemapindex.xml` → `sitemap1.xml`, `sitemap2.xml`)
   - Output: list of canonical document URLs + extracted `document_id` + basic metadata (source_url, retrieved_at)
   - Responsibility: stable enumeration, change detection, rate-safe crawling

2) **Retrieval adapter**
   - Input: document_id / document URL
   - Output: raw content payload(s) needed for parsing (likely JSON + embedded HTML fragments or structured doc)
   - Responsibility: session handling (cookies), required headers (`JURIS-PORTALID`), CSRF bootstrap if required, retry/backoff, HTTP/2 multiplexing
   - Note: use the reusable HTTP/2 fetcher module as the building block.

3) **Parsing/normalization adapter**
   - Input: raw retrieval payload(s)
   - Output: normalized internal documents (title, body text, hierarchical structure where recoverable, plus provenance/license fields)
   - Responsibility: HTML/fragment parsing (likely different from federal), structure extraction, stable identifiers

Everything “after normalization” (chunking, embeddings, persistence, retrieval tools) should remain shared with the federal pipeline via metadata filters (`jurisdiction=de-state:berlin`, `source_name=gesetze-berlin`).

### Open questions (still Phase A)
- Are the `/bsbe/document/<ID>` entries in the sitemaps mainly decisions, norms, or a mix?
  - Early signal: IDs observed so far look like `NJRE...` which *suggests* decisions, but we must confirm via retrieval payload.
- Is there a public/export-friendly endpoint for norms (XML/HTML/ZIP), or is the portal API the only retrieval channel?
- Which API endpoints are usable without login, and what is the minimal CSRF/bootstrap handshake?

### Next concrete steps (Phase A continuation)
1) Use Playwright-assisted runs to capture (bounded) the request payload shapes for:
   - `/jportal/wsrest/recherche3/init`
   - `/jportal/wsrest/recherche3/document`
   - `/jportal/wsrest/recherche3/toc`
2) Update `scripts/berlin_portal_retrieval_probe.py` to replay the observed init + document + toc sequence **without** a browser.
3) Expand discovery sampling to include `jlr-...` norms in addition to `NJRE...` decisions (so batch extraction covers both content classes).

Status: 🟡 In Progress  
Priority: P1 (High)  
Owner: You + me  
Working Language: English  
Last Updated: 2026-01-25

## 1) Objective

Extend Legal-MCP beyond German federal law by adding **German state law (Landesrecht)** and (optionally) state-level public sources into the same retrieval stack:

- Download → parse → normalize → chunk → embed → store → query/search
- Maintain the same quality bar and safety properties as the federal pipeline:
  - deterministic ingestion
  - traceable provenance & licensing / terms-of-use metadata
  - reproducible builds (data artifacts in `data/` with a clear “commit vs ignore” rule)
  - tool contracts consistent with the existing MCP tools

This goal is explicitly a **server/library development goal**, not an end-user push-button ingestion tool.

**Scope approval for the current phase (explicit):**
- ✅ Phase A: Berlin source validation (format + terms/licensing + access method)
- ✅ Phase B: Reusable HTTP/2 fetcher module + tests
- ⛔ Do not implement a Berlin bulk downloader snapshot or ingestion until Phase A confirms a viable approach

**Phase A progress (factual):**
- We found a deterministic discovery surface (sitemaps) with stable document URLs.
- We identified an API base path configured in the frontend bundle (`/jportal/wsrest/recherche3/`) and portal header requirements (`JURIS-PORTALID: bsbe`).
- We validated parseable content extraction via Playwright for both a `jlr-...` Landesnorm and a bounded batch of `NJRE...` decisions.

---

## 2) Success Criteria

### 2.1 MVP (Berlin-focused pilot)
- [ ] Identify an **official** Berlin state law source with a stable bulk export or a predictable retrieval flow.
- [ ] Document **terms/licensing constraints** clearly, including whether redistribution is permitted.
- [ ] Make a **downloadable local corpus** (raw) under `data/` with provenance metadata (once source is confirmed).
- [ ] Parse + normalize into the existing internal document model (once source is confirmed).
- [ ] Ingest into vector store (shared collection with strict `jurisdiction` + `source` filters, or separate collection if needed).
- [ ] Retrieval tools can:
  - semantic search Berlin state law
  - retrieve a specific norm by identifier (where resolvable)
  - show stats by jurisdiction and source
- [ ] Documentation: README/TOOLS mention the new jurisdiction, data source, and any licensing constraints.

### 2.2 Optional distribution: prebuilt corpus + precomputed embeddings (Berlin)
Goal: make onboarding easy by shipping **optional** pre-downloaded corpus and **precomputed embeddings** (jinaai model), similar to the federal corpus.

**Hard gate:** only publish/redistribute these artifacts if Phase A finds explicit permission to redistribute.
- [ ] If redistribution allowed: publish release artifacts (corpus + embeddings) with provenance + snapshot versioning
- [ ] If redistribution not allowed/unclear: provide “local build recipe” only (scripts), no published artifacts

### 2.3 Full “German State Law” coverage (multi-state)
- [ ] Architecture supports multiple state sources (Berlin + other Länder) without rewrites.
- [ ] Normalization handles differences in data format across portals (HTML/PDF/XML/JSON where available).
- [ ] Clear “source adapters” pattern (one adapter per portal).
- [ ] A repeatable update mechanism (manual or automated) with change detection.

### 2.4 Quality gates
- [ ] Tests: core ingestion + safety + normalization invariants.
- [ ] Ruff formatting/lint clean.
- [ ] No sensitive information in logs.
- [ ] ≥73% test coverage maintained.

---

## 3) Constraints & Non-Goals

### Constraints
- Must validate licensing/terms-of-use before implementing bulk download and before publishing any prebuilt artifacts.
- Avoid brittle scraping if a bulk export or official API exists.
- Preserve least privilege: downloads should be constrained and validated.
- Results should be reference-based for large documents (RefCache patterns).

### Non-goals (for this goal / current phase)
- Building “a universal scraper for every state portal” in one pass.
- Shipping an end-user “ingest state law now” MCP tool (keep ingestion as CLI/scripts).
- Doing legal reasoning or “answers”—the server provides retrieval primitives only.

---

## 4) Current State (Baseline)

We already have a working federal pipeline (German federal norms):
- format: official source (federal level), parsed into internal model
- storage: structured metadata + vector embeddings + RefCache results
- tools: semantic search + direct lookup by citation/identifier + stats
- bulk download: `httpx` HTTP/2 multiplexing as the high-throughput strategy

Goal 04 is to replicate the *pattern* for state law with a modular, source-adapter design.

---

## 5) Research Plan (Problem Space First)

### 5.1 Identify sources
Focus order:
1. **Berlin state law portal** (official Landesrecht portal)
   - Look for bulk downloads, APIs, or at minimum a stable, legally permitted retrieval pattern.
2. Other state portals (NRW, Bayern, BW, etc.) as follow-on
3. Public decisions databases (Berlin courts), only if:
   - licensing permits, and
   - machine-readable formats exist, and
   - stable identifiers/citations are available

For each candidate source, collect:
- official status (government-operated vs third party)
- licensing statement / terms of use (explicit citations/URLs)
- access method (bulk ZIP, API, XML, HTML, PDF)
- update policy (versioned snapshots? “last modified”?)
- document identifiers (norm IDs, article/§ structure)
- data completeness (consolidated versions, historical versions)

### 5.2 Decide target formats
Preferred:
- XML/JSON with explicit structure and identifiers
Acceptable:
- HTML with consistent structure (parse with a fast HTML parser)
Last resort:
- PDF (requires robust PDF-to-text conversion and will lose structure)

---

## 6) Architecture Proposal

### 6.1 Data model alignment
Extend our existing internal schema with:
- `jurisdiction`: `de-state:<state_code>` (e.g., `de-state:berlin`)
- `source_name`: stable portal identifier (candidate: `gesetze-berlin`)
- `source_url`: canonical URL per norm/document
- `retrieved_at`: timestamp (string/int, consistent with existing conventions)
- `source_license`: short identifier + URL if available
- `norm_id` conventions:
  - Keep a canonical “citation string” where possible
  - Store a structured identifier if the portal provides one

### 6.2 Storage strategy
Option A (recommended): reuse existing “laws” collection/table with a `jurisdiction` dimension.
- Pros: unified tools and filters; cross-jurisdiction search possible
- Cons: indexing must remain performant

Option B: separate collections per jurisdiction.
- Pros: operational isolation
- Cons: more tool branching, harder hybrid queries

Decision (initial): **Option A**, as long as filtering is strict and stats can snapshot per jurisdiction.

### 6.3 Source adapter pattern (key maintainability decision)
Create one adapter per portal, but for Berlin explicitly split responsibilities to match the portal architecture:

- `BerlinPortalDiscovery`
  - `discover_documents(...) -> iterable[DocumentHandle]`
  - Uses sitemap discovery (stable, indexable, cacheable)

- `BerlinPortalRetriever`
  - `retrieve_raw(handle: DocumentHandle) -> RawArtifacts`
  - Handles cookies, required headers (e.g., `JURIS-PORTALID`), CSRF/bootstrap as needed
  - Uses HTTP/2 multiplexing and retry/backoff

- `BerlinPortalParser`
  - `parse_raw(raw: RawArtifacts) -> list[NormalizedDocument]`
  - Extracts identifiers and hierarchy where possible

Shared interfaces (conceptual):
- `discover(...)`
- `retrieve_raw(...)`
- `parse_raw(...)`
- `extract_identifiers(...)`
- `extract_hierarchy(...)`

This prevents portal-specific session/token logic and HTML structure assumptions from infecting the core pipeline.

### 6.4 Networking strategy (reuse federal learnings)
We will reuse the federal high-throughput pattern:
- `httpx.AsyncClient(http2=True)` (HTTP/2 multiplexing)
- connection pooling limits
- bounded concurrency via semaphore
- retry/backoff for `429`/`5xx` and transient failures

---

## 7) Task Breakdown (Updated)

### Phase A — Source discovery & licensing validation (Berlin pilot)
- Enumerate official Berlin portals (laws/regulations, possibly decisions)
- Document licensing/terms and permitted use **with citations**
- Identify best retrieval method (bulk/API/HTML/PDF)
- Output: shortlist + go/no-go decision noted in this scratchpad

Acceptance:
- at least one viable Berlin law source identified, including terms/licensing constraints and feasible access path

### Phase B — Reusable HTTP/2 fetcher module (approved)
Deliverables:
- Reusable internal fetcher supporting:
  - HTTP/2 multiplexing + pooling
  - bounded concurrency
  - retry/backoff
  - byte caps / range requests (for safe JS bundle probing)

Planned files:
- `legal_mcp/net/http2_fetcher.py` (new)
- `tests/test_http2_fetcher.py` (new)
- `scripts/berlin_probe_endpoints.py` (new; bounded research helper)

Acceptance:
- module imported + unit tested; probe script can extract candidate endpoints without large downloads

### Phase C — Berlin downloader snapshot + provenance metadata (blocked on Phase A)

**Clarification (Berlin-specific):**
The “downloader snapshot” for Berlin is likely a two-step process:
1) discovery snapshot: sitemap-derived list of document handles / IDs
2) retrieval snapshot: raw payloads fetched via the portal API (not static HTML pages)

Planned steps:
- Implement discovery snapshot download for Berlin source (sitemap index + sitemap files)
- Store discovery artifacts under `data/raw/de-state/berlin/discovery/...`
- Implement retrieval snapshot (raw artifacts) under `data/raw/de-state/berlin/retrieved/...`
- Record a provenance file (source URLs, retrieval time, portalId, headers used at a high level, and license/terms links)

Acceptance:
- reproducible snapshot download; no brittle assumptions; bounded and rate-safe crawling

### Phase D — Berlin parser + normalization (blocked on Phase A)
- Parse raw format into internal normalized model
- Extract: title, norm hierarchy, citations, source_url
- Emit `data/processed/de-state/berlin/...` artifacts

Acceptance:
- sample law parsed with stable IDs and structure

### Phase E — Chunking + embeddings + persistence (blocked on Phase A)
- Reuse existing pipeline stages
- Ensure metadata includes `jurisdiction=de-state:berlin`
- Add stats checks

Acceptance:
- semantic search returns Berlin state law hits; direct lookup works where feasible

### Phase F — Tool integration polish + docs (blocked on Phase A)
- Ensure existing tools accept Berlin jurisdiction filter
- Update README/TOOLS
- Add smoke tests: search + get-by-id for at least one known norm

Acceptance:
- documented and demonstrably usable in the running server

### Phase G (Optional) — Prebuilt corpus + embeddings distribution (hard-gated)
- Only if redistribution is explicitly permitted
- Publish release artifacts (corpus + embeddings) with provenance and snapshot versioning
- Otherwise: local build recipe only (no published artifacts)

Acceptance:
- onboarding path documented; compliance with source terms is explicit

---

## 8) Risks & Mitigations

### Risk: No bulk export / hostile portal structure
Mitigation:
- prefer stable exports/APIs
- if scraping is unavoidable, isolate it and add robust parsing tests + throttling

### Risk: Licensing restrictions (redistribution)
Mitigation:
- treat corpus/embedding publication as hard-gated
- store license metadata per document
- keep raw downloads local-only if redistribution is not permitted (do not commit artifacts)

### Risk: Identifier ambiguity
Mitigation:
- define canonical ID rules early
- keep both “portal ID” and “human citation” if available

### Risk: PDF-only sources degrade quality
Mitigation:
- treat PDF as last resort
- clearly mark heuristic chunking

---

## 9) Open Questions (Need Research)

- What official Berlin Landesrecht data source exists and what is the distribution format?
- Is there an official Berlin court decisions source with bulk download or structured access?
- Can we obtain state law as XML/JSON in a way comparable to federal law?
- How should versioning work for state law (historical snapshots vs consolidated current)?

---

## 10) Next Actions (Immediate)

- Start Phase A research for Berlin sources + licensing/terms.
- Implement Phase B (reusable HTTP/2 fetcher + tests + bounded probe script).
- Only after Phase A confirms a viable source: proceed with Berlin snapshot download + provenance and ingestion.

---