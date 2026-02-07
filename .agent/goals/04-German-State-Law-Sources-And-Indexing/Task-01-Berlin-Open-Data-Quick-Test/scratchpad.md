# Task 01: Berlin Open Data (datenregister.berlin.de / CKAN) — Quick Test & Decision Log

Status: 🟡 In Progress  
Owner: You + me  
Goal: Goal 04 — German State Law Sources & Indexing (Landesrecht)  
Created: 2026-01-25  
Last Updated: 2026-01-25  

## 1) Purpose

Run a **quick, evidence-driven** evaluation of the Berlin Open Data stack (Drupal portal + CKAN “Datenregister”) to see whether it provides a **better machine-readable source** for Berlin legal materials than the `gesetze.berlin.de/bsbe` SPA portal.

This is a *research task*, not ingestion implementation.

## 2) Key Facts (Validated)

- `daten.berlin.de` is the public-facing portal.
- The portal is described as a **metadata portal**; datasets are hosted elsewhere.
- A CKAN-based “Datenregister” exists and is reachable at `https://datenregister.berlin.de`.
- CKAN API endpoint appears available:
  - `GET https://datenregister.berlin.de/api/3/action/site_read` → `success: true`
- CKAN `package_search` quick probes (bounded: `rows=10`) show:
  - `q=gesetz` → `count=11`
  - `q=verordnung` → `count=8`
  - `q=gvbl` → `count=7`
  - `q=gesetzblatt` → `count=0`
  - `q=satzung` → `count=0`
  - `q=rechtsprechung` → `count=0`
  - `q=vorschrift` → `count=0`

> Note: The query counts above come from CKAN API probing (not UI browsing). Keep all future notes evidence-based.

## 3) Hypotheses

H1: The CKAN registry contains **some** legal documents (laws, ordinances, gazette PDFs), but likely not a full consolidated corpus.  
H2: Some items might be available as machine-readable formats (HTML/XML/JSON), but PDFs are most likely.  
H3: Even if useful, CKAN will be a **supplementary** source rather than a replacement for the Juris-based portal.

## 4) Success Criteria (for this task)

- [ ] Identify whether CKAN contains datasets plausibly representing:
  - Berlin laws / ordinances (Gesetze/Verordnungen)
  - official gazette publications (GVBl.)
  - case law (Rechtsprechung) (less likely)
- [ ] For top hits, record:
  - dataset URL + dataset ID/name
  - license fields (license_id/license_title/license_url)
  - resource list + formats + resource URLs
  - “machine readability score” (see below)
- [ ] Write a concise recommendation:
  - Use as primary source / supplementary / discard
  - Risks/constraints (licensing, incompleteness, PDF-only)

## 5) Work Plan (Quick Test)

## 5.5 Results (Quick Test B — Juris SPA / Playwright-assisted validation)
> This section is intentionally minimal and evidence-driven. It is included here because it directly answers the “CKAN vs Juris SPA” decision and unlocks a practical retrieval path from sitemap IDs.

### Playwright extraction success (single URL)
Test URL:
- `https://gesetze.berlin.de/bsbe/document/jlr-OpenDataBerVBErahmen`

Observed outcome:
- Headless Chromium successfully rendered and extracted **parseable text and HTML**.
- Best-effort selector chosen by extractor: `#main`
- Extracted content includes the regulation header fields (e.g., “Amtliche Abkürzung”, “Ausfertigungsdatum”, “Gültig ab”, “Fundstelle”) and a Permalink block.
- The rendered DOM contains explicit perma links:
  - `https://gesetze.berlin.de/perma?d=jlr-OpenDataBerVBErahmen`
  - `https://gesetze.berlin.de/perma?j=OpenDataBerV_BE`
  - `https://gesetze.berlin.de/perma?a=OpenDataBerV_BE`

### Concrete backend endpoint paths (captured from browser network)
During rendering, the SPA issued POST requests to these concrete endpoints (paths only):
- `/jportal/wsrest/recherche3/init`
- `/jportal/wsrest/recherche3/search` (multiple times)
- `/jportal/wsrest/recherche3/cmsContent`
- `/jportal/wsrest/recherche3/configUpdate`
- `/jportal/wsrest/recherche3/pricesDefault`
- `/jportal/wsrest/recherche3/document`
- `/jportal/wsrest/recherche3/toc`

Implication:
- This validates that a reproducible retrieval flow likely exists without UI scraping, if we can reproduce the SPA’s init/session prerequisites and request payloads for:
  - `document` (content) and
  - `toc` (table of contents / navigation)
- This also validates that sitemap IDs including `jlr-...` correspond to **Landesnorm** content (not only `NJRE...` decisions).


## 5.4 Results (Quick Test A — CKAN)

### Query results summary
- `gesetz` returned a small set (11) but is noisy: results include “Senatsvorlagen” archives and unrelated datasets that merely mention “Gesetz” in description/title.
- `verordnung` similarly returns a small set (8) and is mostly non-corpus material.
- `gvbl` did not surface “Gesetzblatt” corpora; results appear dominated by planning/GIS items and “Verordnungen” represented as geodata (WMS/WFS), not text.

### Plausible “legal-ish” datasets (top evidence)
1) `senatsvorlagen` — “Senatsvorlagen”
   - Dataset: https://datenregister.berlin.de/dataset/senatsvorlagen
   - License: `cc-by` (Creative Commons Attribution), `license_url=https://opendefinition.org/licenses/cc-by/`
   - Resources: 7 items, URLs point to `berlin.de` PDFs (resource `format` often empty, but URLs end in `.pdf`)
   - Relevance: contains some law/ordinance-related documents, but these are “Senatsdokumente”, not a consolidated code corpus.

2) `senatsvorlagen-der-senatsverwaltung-f-r-finanzen` — “Senatsvorlagen der Senatsverwaltung für Finanzen”
   - Dataset: https://datenregister.berlin.de/dataset/senatsvorlagen-der-senatsverwaltung-f-r-finanzen
   - License: `cc-by` (Creative Commons Attribution), `license_url=https://opendefinition.org/licenses/cc-by/`
   - Resources: 143, `format=PDF` (at least in sampled resources)
   - Notes (CKAN): “nicht mehr aktualisiert … nur noch zur Archivierung … Dokumente … auch … Parlamensdokumentation (PARDOK)”
   - Relevance: archival parliamentary/senate documents; not a law text source.

3) `vorkaufsrechtsverordnungen-auf-grund-25-abs-1-satz-1-nr-2-baugb-wfs-224f34d0` — “Vorkaufsrechtsverordnungen … [WFS]”
   - Dataset: https://datenregister.berlin.de/dataset/vorkaufsrechtsverordnungen-auf-grund-25-abs-1-satz-1-nr-2-baugb-wfs-224f34d0
   - License: `dl-de-zero-2.0` (Datenlizenz Deutschland – Zero – Version 2.0), `license_url=https://www.govdata.de/dl-de/zero-2-0`
   - Resources: WFS endpoints (`GetCapabilities` + base endpoint at `https://gdi.berlin.de/services/wfs/vorkaufsrechtsverordnungen`)
   - Notes: explicitly states: “Rechtsverbindlich ist ausschließlich die Veröffentlichung im GVBl.”
   - Relevance: strong for geospatial delimitation of ordinance coverage areas; not a text corpus.

4) `vorkaufsrechtsverordnungen-auf-grund-25-abs-1-satz-1-nr-2-baugb-wms-d9318741` — “Vorkaufsrechtsverordnungen … [WMS]”
   - Dataset: https://datenregister.berlin.de/dataset/vorkaufsrechtsverordnungen-auf-grund-25-abs-1-satz-1-nr-2-baugb-wms-d9318741
   - License: `dl-de-zero-2.0`, `license_url=https://www.govdata.de/dl-de/zero-2-0`
   - Resources: WMS endpoints + `https://gdi.berlin.de/view/vorkaufsrechtsverordnungen`
   - Relevance: same as WFS item; GIS surface, not consolidated legal text.

5) `simple_search_wwwberlindesenarbeitberlin_bec9845e8172fdf99f09b421b49dec3a_riflicheabweichungen` — “In Berlin und Brandenburg sind folgende tarifliche Abweichungen erlaubt:”
   - Dataset: https://datenregister.berlin.de/dataset/simple_search_wwwberlindesenarbeitberlin_bec9845e8172fdf99f09b421b49dec3a_riflicheabweichungen
   - License: `other-closed` (“Siehe Website des Datensatzes”) → treat as non-permissive/unknown until proven otherwise
   - Resources: HTML + JSON + XML + XLS + CSV at `berlin.de` endpoints (e.g., `.../index.php/index/all.json?q=`)
   - Relevance: information related to Mindestlohn/tarifliche Abweichungen, but not a general Berlin-law corpus, and license is a blocker.

### Machine-readability scoring (rubric)
- `senatsvorlagen`: 1 (PDF-only; plus not consolidated law text)
- `senatsvorlagen-der-senatsverwaltung-f-r-finanzen`: 1 (PDF-only; archival; not law text)
- `vorkaufsrechtsverordnungen ... WFS/WMS`: 3 for *structured geodata* but 0–1 as *law text source* (no normative text)
- `simple_search_...tariflicheabweichungen`: 3 technically (JSON/XML), but **blocked by license** and not a law corpus

### Preliminary recommendation (CKAN)
- Decision 01 (primary source for Berlin consolidated law texts): **Discard as primary**
  - Rationale: searches did not reveal a corpus of consolidated Berlin laws/ordinances in structured form; results are mostly PDFs (senate documents) or GIS datasets.
- Decision 02 (supplementary source): **Supplementary (narrow)**
  - Use-case A: archival “Senatsvorlagen” PDFs where relevant to a case or legislative history (high parsing cost).
  - Use-case B: GIS ordinance boundary datasets (WFS/WMS) for spatial/legal intersection features, explicitly not as authoritative text.
  - Blocker: datasets with strong machine readability sometimes have unclear/closed licensing (`other-closed`) and must be excluded unless license is confirmed permissive.

### 5.1 Query strategy (CKAN API)
Use `package_search` with small `rows` and legal-ish keywords:

Suggested queries:
- `gesetz`
- `verordnung`
- `gvbl`
- `gesetzblatt`
- `rechtsverordnung`
- `satzung`
- `rechtsprechung`
- `vorschrift`
- optional: `"Berlin" AND (gesetz OR verordnung)` (if CKAN search supports boolean syntax)

For each query, capture:
- total count
- top N results (N=10) and why they look relevant

### 5.2 Resource format scoring (simple rubric)
Assign a quick score per dataset based on its resources:
- 3 = structured machine-readable (JSON/XML) with stable identifiers
- 2 = HTML pages with consistent structure (parsable)
- 1 = PDF-only (still usable, but high parsing cost / low structure)
- 0 = links only / unclear / non-legal material

### 5.3 Minimal validation of “legal-ness”
For a few highest-ranking datasets, verify content type:
- Is it actually a Berlin law/ordinance text (or only a legislative document, senate memo, etc.)?
- Is it consolidated “current law” or merely publication/documentation?

## 6) Constraints / Safety

- Do not download large files (especially PDFs) in this task; only inspect metadata and a small bounded preview if necessary.
- Do not store secrets.
- Do not assume redistribution rights; record license fields and links and treat as unknown until verified.

## 7) Outputs / Artifacts

- This scratchpad updated with findings and citations.
- Optional: a small JSON report under:
  - `data/raw/de-state/berlin/open-data/ckan_probe_*.json`
  (Only if we decide it’s useful and safe to store locally.)

## 8) Decision Log (fill as evidence arrives)

### Decision 01: Is CKAN a viable primary source for Berlin law texts?
- Status: Decided (preliminary)
- Evidence:
  - `package_search` counts: `gesetz=11`, `verordnung=8`, `gvbl=7`, `gesetzblatt=0`, `satzung=0`, `rechtsprechung=0`, `vorschrift=0`
  - Top “gesetz” hits are largely “Senatsvorlagen” (PDF documents) and unrelated datasets mentioning “Gesetz”
  - “GVBl” does not yield a Gazette corpus; results include planning/GIS datasets
- Decision:
  - **No — discard CKAN as primary source for consolidated Berlin law texts**
- Rationale:
  - CKAN appears to be a metadata registry with scattered documents; it does not expose a comprehensive, structured Berlin-law corpus comparable to `gesetze.berlin.de/bsbe`.

### Decision 02: Use CKAN as supplementary source?
- Status: Decided (preliminary)
- Evidence:
  - `senatsvorlagen` (`cc-by`) provides a small set of PDFs that include some “Gesetz/Verordnung”-related documents.
  - `senatsvorlagen-der-senatsverwaltung-f-r-finanzen` (`cc-by`) provides a large archival PDF set (explicitly “nicht mehr aktualisiert”).
  - `vorkaufsrechtsverordnungen ...` (`dl-de-zero-2.0`) provides WFS/WMS geodata tied to ordinance coverage areas with an explicit disclaimer: “Rechtsverbindlich ist ausschließlich die Veröffentlichung im GVBl.”
  - One highly machine-readable dataset (HTML/JSON/XML/CSV/XLS) is `other-closed` licensed, making it unusable until license is clarified.
- Decision:
  - **Yes — but only for narrow, well-scoped supplementary use-cases (archival PDFs; GIS ordinance boundary layers)**

## 9) Next Session Checklist

- [ ] Run `package_search` for the query list with `rows=5..10`
- [ ] Record at least 3 promising datasets with:
  - license info + resource formats + URLs
- [ ] Conclude whether CKAN is “better” (machine-readable) than SPA portal for any meaningful subset
- [ ] Update Goal 04 scratchpad with a short summary + recommendation

## 10) Open Questions

- Does CKAN contain a dataset that clearly corresponds to **Berlin consolidated laws/ordinances** (not just PDFs / senate documents)?
- Are licenses permissive enough for local caching and embedding?
- Are there stable identifiers mapping to citations (e.g., § / Art.) or only filenames?
