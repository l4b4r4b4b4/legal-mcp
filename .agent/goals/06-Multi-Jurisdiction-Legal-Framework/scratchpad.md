# Goal 06: Multi-Jurisdiction Legal Framework Expansion

> **Status**: ⚪ Not Started
> **Priority**: P1 (High) — Foundation for international legal corpus
> **Created**: 2026-02-07
> **Updated**: 2026-02-07

## Overview

Expand legal-mcp's architecture to support law from all continents, with EU/Germany as the default but extensible to any jurisdiction. This goal establishes the abstract patterns, interfaces, and data models needed to ingest, normalize, and query legal documents from any country's legal system.

**Business context:** German consulates worldwide need AI-assisted tooling to assess whether consulate buildings follow German standards (energy, safety, accessibility) by checking compatibility with local law. This requires a unified framework to compare regulations across jurisdictions.

## Success Criteria

- [ ] Abstract `JurisdictionAdapter` interface defined (Protocol-based)
- [ ] Jurisdiction taxonomy established (Continent → Country → State/Province → Municipality)
- [ ] Unified legal document schema supports all jurisdiction types
- [ ] German federal/state adapters refactored to use new interface
- [ ] At least one non-German jurisdiction adapter implemented (proof of concept)
- [ ] Legal domain classification system (construction, energy, safety, etc.)
- [ ] Query tools support jurisdiction filtering at all levels
- [ ] Documentation for adding new jurisdictions
- [ ] 100% backward compatibility with existing German law tools

## Context & Background

### Why Multi-Jurisdiction?

German consulates operate in ~220 locations worldwide. Each location must comply with:
1. **German standards** — Building codes (BauO), energy efficiency (GEG), safety (ArbSchG)
2. **Local law** — Host country's construction permits, zoning, safety regulations

The AI assistant needs to:
- Retrieve relevant German requirements
- Retrieve equivalent local regulations
- Identify gaps or conflicts
- Support compliance assessment

### Current State

- German Federal Law: ✅ Implemented (gesetze-im-internet.de, 58K+ laws)
- German State Law (Berlin): 🟡 In progress (Goal 04)
- EU Law: ⚪ Planned
- All other jurisdictions: ⚪ Not started

### Architecture Gap

Current implementation is Germany-specific:
- Hardcoded parsers for German XML format
- German-specific metadata fields
- No abstraction for jurisdiction differences

## Constraints & Requirements

### Hard Requirements
- Must not break existing German law functionality
- Must support hierarchical jurisdictions (Federal → State → Local)
- Must handle different legal systems (civil law, common law, mixed)
- Must normalize document identifiers across sources
- Must preserve source-specific metadata

### Soft Requirements
- Prefer official government sources over third-party
- Support multiple languages (original + translations where available)
- Handle PDF sources gracefully (with quality warnings)
- Enable incremental adoption (one jurisdiction at a time)

### Out of Scope (for this goal)
- Actual data ingestion for non-German jurisdictions (Goal 07)
- Cross-jurisdiction comparison algorithms (Goal 08)
- Natural language translation of laws
- Legal advice or interpretation

## Approach

**Phase 1:** Design unified data model and interfaces
**Phase 2:** Refactor German adapters to new interface
**Phase 3:** Implement one proof-of-concept non-German adapter
**Phase 4:** Update MCP tools for multi-jurisdiction queries
**Phase 5:** Documentation and testing

## Tasks

| Task ID | Description | Status | Estimate | Depends On |
|---------|-------------|--------|----------|------------|
| Task-01 | Design jurisdiction taxonomy & data model | ⚪ | 2 hours | - |
| Task-02 | Define JurisdictionAdapter Protocol | ⚪ | 2 hours | Task-01 |
| Task-03 | Design legal domain classification | ⚪ | 1 hour | Task-01 |
| Task-04 | Refactor German Federal adapter | ⚪ | 3 hours | Task-02 |
| Task-05 | Refactor German State (Berlin) adapter | ⚪ | 2 hours | Task-02, Goal-04 |
| Task-06 | Implement EU Law adapter (proof of concept) | ⚪ | 4 hours | Task-02 |
| Task-07 | Update MCP tools for jurisdiction filtering | ⚪ | 2 hours | Task-04 |
| Task-08 | Add jurisdiction metadata to ChromaDB | ⚪ | 1 hour | Task-04 |
| Task-09 | Write documentation for adding jurisdictions | ⚪ | 1 hour | Task-06 |
| Task-10 | Integration testing across jurisdictions | ⚪ | 2 hours | Task-07 |

**Total estimate:** ~20 hours

## Task Details (High-Level)

### Task-01: Jurisdiction Taxonomy & Data Model

**Deliverables:**
- `src/legal_mcp/models/jurisdiction.py` — Pydantic models
- Enum for continents, legal system types
- Hierarchical jurisdiction identifier scheme

**Proposed taxonomy:**
```
Continent/Country/Subdivision/Municipality
Example: EU/DE/BE/Berlin (Germany, Berlin state)
Example: NA/US/CA/LosAngeles (USA, California, LA)
Example: AS/JP/13/Tokyo (Japan, Tokyo prefecture)
```

**Legal system types:**
- Civil Law (Germany, France, Japan, Brazil)
- Common Law (USA, UK, Australia)
- Mixed Systems (South Africa, Scotland)

### Task-02: JurisdictionAdapter Protocol

**Interface design:**
```python
class JurisdictionAdapter(Protocol):
    jurisdiction_id: str  # e.g., "EU/DE"
    
    def list_available_sources(self) -> list[LegalSource]: ...
    def fetch_document(self, doc_id: str) -> RawLegalDocument: ...
    def parse_document(self, raw: RawLegalDocument) -> NormalizedLegalDocument: ...
    def get_citation_format(self) -> CitationFormat: ...
    def supports_bulk_download(self) -> bool: ...
```

### Task-03: Legal Domain Classification

**Construction/Real Estate domains:**
| Domain | German Example | Description |
|--------|----------------|-------------|
| `construction.building-code` | BauO, MBO | General building regulations |
| `construction.energy` | GEG, EnEV | Energy efficiency requirements |
| `construction.fire-safety` | MLAR, LBO | Fire protection |
| `construction.accessibility` | DIN 18040 | Barrier-free design |
| `construction.workplace` | ArbStättV | Workplace safety |
| `real-estate.zoning` | BauNVO | Land use planning |
| `real-estate.contracts` | BGB §§433-853 | Property transactions |

### Task-04: Refactor German Federal Adapter

**Changes:**
- Move `loaders/gii/` to new adapter pattern
- Implement `JurisdictionAdapter` protocol
- Add domain classification to documents
- Preserve all existing functionality

### Task-05: Refactor German State Adapter

**Changes:**
- Extend Berlin loader (Goal 04) to new pattern
- Support state-specific overrides of federal law
- Link state laws to federal counterparts

### Task-06: EU Law Adapter (Proof of Concept)

**Target:** EUR-Lex API for EU Directives/Regulations

**Focus areas:**
- Construction Products Regulation (CPR)
- Energy Performance of Buildings Directive (EPBD)
- Workplace Safety Framework Directive

**Why EU first:**
- Well-documented API (EUR-Lex REST)
- Structured data (XML, CELEX identifiers)
- Directly applicable to German context
- Tests multi-language support

### Task-07: Update MCP Tools

**New/modified tools:**
```python
# Jurisdiction-aware search
search_laws(
    query="fire safety requirements",
    jurisdictions=["EU/DE", "EU/DE/BE"],  # Federal + Berlin
    domains=["construction.fire-safety"],
    ...
)

# List available jurisdictions
list_jurisdictions(
    continent="EU",
    supports_domain="construction.building-code"
)

# Get jurisdiction metadata
get_jurisdiction_info(jurisdiction_id="EU/DE")
```

### Task-08: ChromaDB Jurisdiction Metadata

**New metadata fields:**
```python
{
    "jurisdiction_id": "EU/DE",
    "jurisdiction_level": "federal",  # federal, state, local
    "legal_system": "civil_law",
    "domains": ["construction.building-code", "construction.fire-safety"],
    "language": "de",
    "source_authority": "gesetze-im-internet.de"
}
```

### Task-09: Documentation

**Contents:**
- Architecture overview
- How to add a new jurisdiction
- Adapter implementation guide
- Domain classification reference
- API changes from v0.x

### Task-10: Integration Testing

**Test scenarios:**
- Search across multiple jurisdictions
- Filter by domain
- Jurisdiction hierarchy queries
- Backward compatibility with existing tools

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Breaking existing German functionality | High | Medium | Extensive regression tests |
| Over-engineering the abstraction | Medium | Medium | Start minimal, extend as needed |
| Jurisdiction ID collisions | Medium | Low | Use ISO country codes + subdivisions |
| EUR-Lex API complexity | Low | Medium | Start with subset of document types |

## Dependencies

- **Upstream**: 
  - Goal 04 (German State Law) — Berlin adapter to refactor
- **Downstream**: 
  - Goal 07 (International Corpus) — Uses adapters from this goal
  - Goal 08 (Comparison Tool) — Uses unified data model

## Notes & Decisions

### Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-07 | Protocol-based adapters over ABC | More flexible, better for type checking |
| 2026-02-07 | Hierarchical jurisdiction IDs | Enables parent/child queries, intuitive |
| 2026-02-07 | EU as first non-German adapter | Best data quality, relevant to use case |

### Open Questions

- [ ] How to handle laws that span multiple domains?
- [ ] Should translations be stored as separate documents or metadata?
- [ ] How to represent federal/state law relationships (override, supplement)?
- [ ] Standard for legal effective dates across jurisdictions?

## References

- [EUR-Lex API Documentation](https://eur-lex.europa.eu/content/help/data-reuse/webservice.html)
- [ISO 3166-2 Country Subdivisions](https://en.wikipedia.org/wiki/ISO_3166-2)
- [Legal Systems of the World](https://en.wikipedia.org/wiki/List_of_national_legal_systems)
- [CELEX Number Format](https://eur-lex.europa.eu/content/tools/TableOfSectors/types_of_documents_in_eurlex.html)