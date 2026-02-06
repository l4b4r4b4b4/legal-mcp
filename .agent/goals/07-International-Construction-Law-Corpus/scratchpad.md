# Goal 07: International Construction Law Corpus (Per Continent)

> **Status**: ⚪ Not Started
> **Priority**: P1 (High) — Core data for consulate compliance tool
> **Created**: 2026-02-07
> **Updated**: 2026-02-07
> **Depends On**: Goal 06 (Multi-Jurisdiction Framework)

## Overview

Build preprocessed legal corpora for one representative country per continent, focusing specifically on construction and real estate law. Each corpus includes:

1. **Federal/National law** — Building codes, energy efficiency, safety regulations
2. **State/Provincial law** — One selected subdivision per country
3. **Related regulations** — Standards, norms, administrative decisions

**Business context:** German consulates in ~220 locations need to verify that consulate buildings comply with German standards. This requires comparing German law against local construction regulations in each host country.

## Success Criteria

### Per-Country Deliverables
- [ ] Federal construction/building code laws ingested
- [ ] Federal energy efficiency regulations ingested
- [ ] Federal workplace/fire safety regulations ingested
- [ ] One state/province's construction laws ingested
- [ ] Documents normalized to Goal 06 unified schema
- [ ] Embeddings generated and stored in ChromaDB
- [ ] Domain tags applied (`construction.*`, `real-estate.*`)
- [ ] Source attribution and licensing documented

### Overall
- [ ] 6 countries completed (one per inhabited continent)
- [ ] All corpora searchable via unified MCP tools
- [ ] Documentation of data sources and update procedures
- [ ] Licensing compliance verified for all sources

## Target Countries (One Per Continent)

| Continent | Country | Federal Level | State/Province | Rationale |
|-----------|---------|---------------|----------------|-----------|
| **Europe** | 🇩🇪 Germany | ✅ Existing | Berlin ✅ | Home base, reference standard |
| **North America** | 🇺🇸 USA | CFR, OSHA | California | Major consulate, good data |
| **South America** | 🇧🇷 Brazil | Federal laws | São Paulo | Largest economy, structured data |
| **Asia** | 🇯🇵 Japan | Building Standards | Tokyo | High standards, structured data |
| **Africa** | 🇿🇦 South Africa | National Building Regs | Western Cape | Common law + civil law mix |
| **Oceania** | 🇦🇺 Australia | National Construction Code | New South Wales | Federal system, English |

## Context & Background

### Why These Countries?

**Selection criteria:**
1. **Consulate presence** — Major German diplomatic missions
2. **Data availability** — Official sources with structured data (not PDF-only)
3. **Legal system diversity** — Mix of civil law, common law, mixed systems
4. **Language accessibility** — English or German preferred, translations available
5. **Economic importance** — Trade and investment relationships

### Construction Law Scope

**In scope (priority order):**
| Domain | Description | German Reference |
|--------|-------------|------------------|
| Building codes | General construction requirements | MBO, LBO |
| Energy efficiency | Thermal performance, renewables | GEG (Gebäudeenergiegesetz) |
| Fire safety | Prevention, egress, materials | MLAR, MIndBauRL |
| Structural safety | Load-bearing, seismic, wind | Eurocodes |
| Workplace safety | Occupational health in buildings | ArbStättV |
| Accessibility | Barrier-free design | DIN 18040 |
| Zoning/Land use | Permitted uses, setbacks | BauNVO, BauGB |

**Out of scope (for now):**
- Environmental impact assessments (separate domain)
- Historic preservation (too specialized)
- Residential tenancy law (not construction)
- Tax implications of property (not construction)

## Constraints & Requirements

### Hard Requirements
- Must use official government sources where available
- Must document licensing for all data
- Must not redistribute copyrighted content without permission
- Must preserve original document identifiers
- Must tag documents with standardized domains

### Soft Requirements
- Prefer XML/API over PDF sources
- Include English translations where officially available
- Prioritize currently-in-force laws over historical
- Document data freshness and update frequency

### Out of Scope
- Real-time updates from all sources
- Unofficial translations
- Legal commentary or interpretation
- Sub-municipal regulations

## Approach

**Per-country workflow:**
1. **Research** — Identify official sources, assess data quality, verify licensing
2. **Adapter** — Implement `JurisdictionAdapter` (using Goal 06 framework)
3. **Download** — Bulk fetch or incremental retrieval
4. **Parse** — Convert to normalized schema
5. **Classify** — Apply domain tags
6. **Embed** — Generate vectors, store in ChromaDB
7. **Test** — Verify searchability, quality spot-checks
8. **Document** — Source URLs, licensing, update procedures

## Tasks

| Task ID | Description | Status | Estimate | Depends On |
|---------|-------------|--------|----------|------------|
| **Phase A: Research & Planning** |||||
| Task-01 | Research USA federal construction law sources | ⚪ | 3 hours | Goal 06 |
| Task-02 | Research USA California state sources | ⚪ | 2 hours | Task-01 |
| Task-03 | Research Brazil federal/São Paulo sources | ⚪ | 3 hours | Goal 06 |
| Task-04 | Research Japan federal/Tokyo sources | ⚪ | 3 hours | Goal 06 |
| Task-05 | Research South Africa/Western Cape sources | ⚪ | 3 hours | Goal 06 |
| Task-06 | Research Australia NCC/NSW sources | ⚪ | 3 hours | Goal 06 |
| **Phase B: USA Implementation** |||||
| Task-07 | Implement USA Federal adapter (eCFR, OSHA) | ⚪ | 6 hours | Task-01, Task-02 |
| Task-08 | Implement California adapter | ⚪ | 4 hours | Task-07 |
| Task-09 | Ingest USA corpus + embeddings | ⚪ | 2 hours | Task-08 |
| Task-10 | Test & validate USA corpus | ⚪ | 2 hours | Task-09 |
| **Phase C: Brazil Implementation** |||||
| Task-11 | Implement Brazil Federal adapter | ⚪ | 6 hours | Task-03 |
| Task-12 | Implement São Paulo adapter | ⚪ | 4 hours | Task-11 |
| Task-13 | Ingest Brazil corpus + embeddings | ⚪ | 2 hours | Task-12 |
| Task-14 | Test & validate Brazil corpus | ⚪ | 2 hours | Task-13 |
| **Phase D: Japan Implementation** |||||
| Task-15 | Implement Japan Federal adapter | ⚪ | 6 hours | Task-04 |
| Task-16 | Implement Tokyo adapter | ⚪ | 4 hours | Task-15 |
| Task-17 | Ingest Japan corpus + embeddings | ⚪ | 2 hours | Task-16 |
| Task-18 | Test & validate Japan corpus | ⚪ | 2 hours | Task-17 |
| **Phase E: South Africa Implementation** |||||
| Task-19 | Implement South Africa Federal adapter | ⚪ | 6 hours | Task-05 |
| Task-20 | Implement Western Cape adapter | ⚪ | 4 hours | Task-19 |
| Task-21 | Ingest South Africa corpus + embeddings | ⚪ | 2 hours | Task-20 |
| Task-22 | Test & validate South Africa corpus | ⚪ | 2 hours | Task-21 |
| **Phase F: Australia Implementation** |||||
| Task-23 | Implement Australia NCC adapter | ⚪ | 6 hours | Task-06 |
| Task-24 | Implement NSW adapter | ⚪ | 4 hours | Task-23 |
| Task-25 | Ingest Australia corpus + embeddings | ⚪ | 2 hours | Task-24 |
| Task-26 | Test & validate Australia corpus | ⚪ | 2 hours | Task-25 |
| **Phase G: Integration & Documentation** |||||
| Task-27 | Cross-jurisdiction search testing | ⚪ | 3 hours | All above |
| Task-28 | Write source documentation | ⚪ | 2 hours | Task-27 |
| Task-29 | Create corpus update procedures | ⚪ | 2 hours | Task-27 |

**Total estimate:** ~90-100 hours (can parallelize across countries)

## Data Source Research (Preliminary)

### 🇺🇸 USA

**Federal:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [eCFR](https://www.ecfr.gov/) | Code of Federal Regulations | XML API | Free, public domain |
| [OSHA Regulations](https://www.osha.gov/laws-regs) | Workplace safety | HTML/PDF | Free, public domain |
| [Congress.gov](https://www.congress.gov/) | USC (statutes) | XML API | Free, public domain |

**California:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [California Legislative Info](https://leginfo.legislature.ca.gov/) | CA codes | XML/HTML | Free |
| [CA Building Standards](https://www.dgs.ca.gov/BSC) | Title 24 | PDF | Free (redistribution unclear) |

### 🇧🇷 Brazil

**Federal:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Planalto](http://www.planalto.gov.br/) | Federal laws | HTML | Free |
| [LexML Brasil](https://www.lexml.gov.br/) | Aggregated legal data | XML API | Free, open |
| [ABNT](https://www.abntcatalogo.com.br/) | Technical standards | PDF | Paid (copyright) |

**São Paulo:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Assembleia Legislativa SP](https://www.al.sp.gov.br/) | State laws | HTML | Free |

### 🇯🇵 Japan

**Federal:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [e-Gov](https://elaws.e-gov.go.jp/) | Japanese laws | XML API | Free |
| [Japanese Law Translation](http://www.japaneselawtranslation.go.jp/) | English translations | HTML/PDF | Free |
| [Building Standards Law](https://elaws.e-gov.go.jp/) | 建築基準法 | XML | Free |

**Tokyo:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Tokyo Metropolitan Gov](https://www.reiki.metro.tokyo.lg.jp/) | Tokyo ordinances | HTML | Free |

### 🇿🇦 South Africa

**Federal:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Gov.za Legislation](https://www.gov.za/documents/acts) | National acts | PDF | Free |
| [SANS (Standards)](https://www.sabs.co.za/) | Building standards | PDF | Paid |
| [National Building Regulations](https://www.gov.za/) | NBR & Building Standards Act | PDF | Free |

**Western Cape:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [WC Provincial Legislature](https://www.wcpp.gov.za/) | Provincial acts | PDF | Free |

### 🇦🇺 Australia

**Federal/National:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Federal Register of Legislation](https://www.legislation.gov.au/) | Commonwealth acts | XML API | Free, CC BY |
| [National Construction Code](https://ncc.abcb.gov.au/) | Building code | Online/PDF | Free access (registration) |

**New South Wales:**
| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [NSW Legislation](https://www.legislation.nsw.gov.au/) | State acts | XML API | Free, CC BY |

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| PDF-only sources (low quality) | High | High (SA, Brazil) | OCR + manual review, quality warnings |
| Paid standards (ABNT, SANS) | Medium | Certain | Document gap, link to purchase, summarize |
| Language barriers (Japanese, Portuguese) | Medium | Medium | Use official translations where available |
| Rate limiting on APIs | Low | Medium | Polite fetching, caching, bulk downloads |
| Licensing restrictions | High | Medium | Thorough research before implementation |
| Data staleness | Medium | Medium | Document freshness, plan update cycles |

## Dependencies

- **Upstream**: 
  - Goal 06 (Multi-Jurisdiction Framework) — Provides adapter interface, unified schema
  - Goal 04 (German State Law) — Berlin as reference implementation
- **Downstream**: 
  - Goal 08 (Comparison Tool) — Uses corpora for cross-jurisdiction analysis

## Notes & Decisions

### Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-07 | Start with USA | Best data quality, English, familiar legal system |
| 2026-02-07 | California over Texas/NY | Strictest building codes, most comprehensive |
| 2026-02-07 | Skip China initially | Data access challenges, translation complexity |
| 2026-02-07 | Include paid sources in docs | Users may purchase; don't ignore existence |

### Open Questions

- [ ] How to handle standards (ISO, DIN, ABNT) that are copyrighted?
- [ ] Should we store PDF text extraction alongside structured data?
- [ ] Frequency of corpus updates per jurisdiction?
- [ ] How to handle laws not yet in force vs. currently effective?
- [ ] Should we include municipal building codes for major cities?

## References

- [eCFR API Documentation](https://www.ecfr.gov/developer-resources)
- [Australian Legislation API](https://www.legislation.gov.au/Content/Linking)
- [LexML Brasil](https://projeto.lexml.gov.br/)
- [e-Gov Japan API](https://elaws.e-gov.go.jp/apitop/)
- [EUR-Lex CELEX](https://eur-lex.europa.eu/content/help/eurlex-content/celex-number.html)