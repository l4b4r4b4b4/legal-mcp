# Goal 08: Cross-Jurisdiction Legislation Comparison Tool

> **Status**: ⚪ Not Started
> **Priority**: P2 (Medium) — Builds on Goals 06 & 07
> **Created**: 2026-02-07
> **Updated**: 2026-02-07
> **Depends On**: Goal 06 (Framework), Goal 07 (Corpora)

## Overview

Build a comprehensive tool for comparing legislation between different countries, enabling German consulates to assess compliance of local buildings against German standards. This is the "intelligence layer" on top of the legal corpora, providing semantic similarity analysis, gap detection, and compliance mapping.

**Business context:** A German consulate in Tokyo needs to verify their building meets both German energy efficiency standards (GEG) and Japanese Building Standards Law (建築基準法). This tool identifies equivalent provisions, highlights gaps, and supports compliance decisions.

## Success Criteria

- [ ] Side-by-side law comparison across any two jurisdictions
- [ ] Semantic similarity scoring between regulations (0-100%)
- [ ] Gap analysis: German requirements vs. local law coverage
- [ ] Compliance matrix generation (German standard → local equivalent)
- [ ] Domain-scoped comparison (e.g., only fire safety regulations)
- [ ] Natural language query: "How does Japan's energy code compare to GEG?"
- [ ] Export comparison reports (Markdown, JSON)
- [ ] MCP tools for all comparison operations
- [ ] Unit tests for comparison algorithms
- [ ] Documentation with example use cases

## Context & Background

### Why a Dedicated Comparison Tool?

**Current capability:** Search and retrieve laws from multiple jurisdictions
**Missing capability:** Intelligent comparison and gap analysis

**User stories:**
1. "Show me how California's Title 24 energy requirements map to German GEG sections"
2. "What fire safety requirements exist in Brazil that don't have German equivalents?"
3. "Rate the overall similarity between German and Japanese building codes"
4. "Generate a compliance checklist: German consulate building in South Africa"

### Comparison Types

| Type | Description | Use Case |
|------|-------------|----------|
| **Semantic similarity** | How similar are two laws in meaning? | "Is §5 GEG similar to NCC Vol 1 Part J?" |
| **Coverage analysis** | Does jurisdiction B cover topic X? | "Does Japan regulate accessible design?" |
| **Gap detection** | What does A require that B doesn't? | "German requirements missing in local law" |
| **Equivalence mapping** | Which B provisions match A? | "German fire safety → Australian equivalents" |
| **Strictness comparison** | Which jurisdiction is more restrictive? | "Whose energy standards are stricter?" |

### Technical Approach

**Core algorithms:**
1. **Vector similarity** — Cosine distance between embedded sections
2. **Topic modeling** — LDA/BERTopic for theme extraction
3. **Named entity recognition** — Extract requirements, thresholds, standards
4. **Graph analysis** — Build regulation citation networks

## Constraints & Requirements

### Hard Requirements
- Must work with any jurisdiction pair from Goal 07
- Must use domain tags from Goal 06 for scoping
- Must not provide legal advice or definitive interpretations
- Must clearly label AI-generated comparisons as such
- Must preserve traceability to source documents

### Soft Requirements
- Support batch comparisons (multiple sections at once)
- Cache comparison results for performance
- Provide confidence scores with explanations
- Support incremental comparison as corpora update

### Out of Scope
- Automated compliance certification
- Legal advice or recommendations
- Translation between languages (use existing translations)
- Real-time regulatory change monitoring

## Approach

**Phase 1:** Design comparison data models and algorithms
**Phase 2:** Implement core comparison functions
**Phase 3:** Build MCP tools for comparison operations
**Phase 4:** Create report generation capabilities
**Phase 5:** Testing and documentation

## Tasks

| Task ID | Description | Status | Estimate | Depends On |
|---------|-------------|--------|----------|------------|
| **Phase 1: Design** |||||
| Task-01 | Design comparison result data models | ⚪ | 2 hours | Goal 06 |
| Task-02 | Research semantic similarity algorithms | ⚪ | 3 hours | - |
| Task-03 | Design gap analysis algorithm | ⚪ | 2 hours | Task-02 |
| Task-04 | Design equivalence mapping algorithm | ⚪ | 2 hours | Task-02 |
| **Phase 2: Core Implementation** |||||
| Task-05 | Implement semantic similarity comparison | ⚪ | 4 hours | Task-01, Task-02 |
| Task-06 | Implement coverage analysis | ⚪ | 3 hours | Task-05 |
| Task-07 | Implement gap detection | ⚪ | 4 hours | Task-03, Task-05 |
| Task-08 | Implement equivalence mapping | ⚪ | 4 hours | Task-04, Task-05 |
| Task-09 | Implement comparison caching | ⚪ | 2 hours | Task-05 |
| **Phase 3: MCP Tools** |||||
| Task-10 | Tool: compare_laws() | ⚪ | 2 hours | Task-05 |
| Task-11 | Tool: find_equivalent_provisions() | ⚪ | 2 hours | Task-08 |
| Task-12 | Tool: analyze_gaps() | ⚪ | 2 hours | Task-07 |
| Task-13 | Tool: generate_compliance_matrix() | ⚪ | 3 hours | Task-08 |
| Task-14 | Tool: compare_jurisdictions() (high-level) | ⚪ | 2 hours | Task-05 |
| **Phase 4: Reports** |||||
| Task-15 | Implement Markdown report generation | ⚪ | 2 hours | Task-13 |
| Task-16 | Implement JSON export | ⚪ | 1 hour | Task-13 |
| Task-17 | Implement comparison summary narratives | ⚪ | 2 hours | Task-15 |
| **Phase 5: Quality** |||||
| Task-18 | Unit tests for comparison algorithms | ⚪ | 3 hours | Phase 2 |
| Task-19 | Integration tests with real corpora | ⚪ | 3 hours | Task-18 |
| Task-20 | Documentation and examples | ⚪ | 2 hours | Task-19 |

**Total estimate:** ~50 hours

## Task Details (High-Level)

### Task-01: Comparison Data Models

**Deliverables:**
- `src/legal_mcp/models/comparison.py`

**Key models:**
```python
class ComparisonResult(BaseModel):
    source_doc: DocumentReference
    target_doc: DocumentReference
    similarity_score: float  # 0.0-1.0
    comparison_type: ComparisonType
    confidence: float
    explanation: str
    matched_segments: list[SegmentMatch]

class GapAnalysis(BaseModel):
    reference_jurisdiction: str  # Usually Germany
    target_jurisdiction: str
    domain: str
    covered_requirements: list[RequirementMatch]
    gaps: list[UnmatchedRequirement]
    coverage_percentage: float

class ComplianceMatrix(BaseModel):
    reference_jurisdiction: str
    target_jurisdiction: str
    domain: str
    mappings: list[EquivalenceMapping]
    summary: ComplianceSummary
```

### Task-05: Semantic Similarity Comparison

**Algorithm:**
1. Retrieve embeddings for both documents from ChromaDB
2. Compute cosine similarity between section vectors
3. Use threshold to classify: equivalent (>0.85), similar (>0.7), related (>0.5), different (<0.5)
4. For high-similarity pairs, extract key differences using attention analysis

**Considerations:**
- Multi-lingual embeddings (paraphrase-multilingual-mpnet-base-v2)
- Section-level vs. paragraph-level comparison
- Handling different document granularities

### Task-07: Gap Detection

**Algorithm:**
1. Extract "requirements" from reference jurisdiction (German law)
   - Use NER to identify obligations ("must", "shall", "required")
   - Extract thresholds, quantities, standards references
2. For each requirement, search target jurisdiction for semantic matches
3. Requirements with no match (similarity < threshold) are "gaps"
4. Classify gaps: missing topic, lower standard, no enforcement

**Example output:**
```
Gap Analysis: GEG (Germany) vs. Building Act (South Africa)
Domain: construction.energy

GAPS IDENTIFIED:
1. GEG §10: Primary energy requirement ≤ 45 kWh/m²a
   Status: NO EQUIVALENT FOUND
   Nearest match: NBR XA (0.42 similarity) - general energy provisions only

2. GEG §71: Renewable heating requirement (65%)
   Status: LOWER STANDARD
   SA equivalent: None mandatory, incentive-based only
```

### Task-08: Equivalence Mapping

**Algorithm:**
1. For each section in reference law, find top-N similar sections in target
2. Apply domain filter (must be same legal domain)
3. Rank by combined score: semantic similarity + keyword overlap + citation similarity
4. Human-readable explanation of why sections are considered equivalent

### Task-10: compare_laws() Tool

**Interface:**
```python
compare_laws(
    doc_a: str,  # Document ID or citation
    doc_b: str,  # Document ID or citation
    comparison_type: Literal["similarity", "coverage", "gaps", "equivalence"] = "similarity",
    section_level: bool = True,  # Compare at section level
    include_explanation: bool = True
) -> ComparisonResult
```

**Example:**
```python
compare_laws(
    doc_a="DE/GEG/§10",
    doc_b="AU/NCC/Vol1/PartJ",
    comparison_type="similarity"
)
# Returns: similarity 0.73, explanation of key differences
```

### Task-13: generate_compliance_matrix() Tool

**Interface:**
```python
generate_compliance_matrix(
    reference_jurisdiction: str,  # e.g., "EU/DE"
    target_jurisdiction: str,     # e.g., "AS/JP"
    domain: str,                  # e.g., "construction.energy"
    format: Literal["matrix", "checklist", "report"] = "matrix"
) -> ComplianceMatrix
```

**Example output (checklist format):**
```markdown
# Compliance Checklist: German Energy Standards in Japan

## Reference: GEG (Gebäudeenergiegesetz)
## Target: Building Standards Law (建築基準法) + Energy Conservation Act

| German Requirement | Status | Japanese Equivalent | Notes |
|-------------------|--------|---------------------|-------|
| GEG §10: Primary energy limit | ✅ Covered | Energy Conservation Act §73 | JP stricter (40 vs 45 kWh/m²a) |
| GEG §71: 65% renewable heating | ⚠️ Partial | Tokyo Ordinance only | Not federal requirement |
| GEG §48: Energy certificate | ✅ Covered | BELS certification | Voluntary but common |
| GEG §60: Inspection requirements | ❌ Gap | No equivalent | Consider German inspector |

Overall Coverage: 78%
```

### Task-15: Markdown Report Generation

**Structure:**
```markdown
# Cross-Jurisdiction Comparison Report

**Generated:** 2026-02-07
**Reference:** Germany (EU/DE)
**Target:** Japan (AS/JP)
**Domain:** Construction & Building Codes

## Executive Summary
[AI-generated narrative summary]

## Detailed Comparison
### 1. Building Codes
### 2. Energy Efficiency
### 3. Fire Safety
### 4. Accessibility

## Gap Analysis
[List of German requirements not covered in Japanese law]

## Recommendations
[Suggestions for addressing gaps]

## Methodology
[Explanation of comparison algorithms used]

## Disclaimer
This report is generated by AI analysis and does not constitute legal advice.
All findings should be verified by qualified legal professionals.
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| False equivalence detection | High | Medium | Confidence scores, human review flag |
| Over-reliance on AI comparison | High | High | Clear disclaimers, audit trails |
| Cross-language comparison accuracy | Medium | Medium | Use multilingual models, test thoroughly |
| Performance with large corpora | Medium | Medium | Caching, incremental comparison |
| Misleading gap analysis | High | Medium | Conservative thresholds, explanations |

## Dependencies

- **Upstream**: 
  - Goal 06 (Framework) — Unified data model, jurisdiction taxonomy
  - Goal 07 (Corpora) — Legal documents to compare
- **Downstream**: 
  - Consulate compliance workflows
  - Future: Automated compliance monitoring

## Notes & Decisions

### Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-07 | Germany as default reference | Primary use case is German standard compliance |
| 2026-02-07 | Confidence scores mandatory | Prevent over-reliance on AI analysis |
| 2026-02-07 | No legal advice framing | Liability and accuracy concerns |
| 2026-02-07 | Section-level as default | Balance between granularity and noise |

### Open Questions

- [ ] Should we support user-defined equivalence overrides (manual mappings)?
- [ ] How to handle evolving laws (comparison at specific dates)?
- [ ] Should comparison results be persisted or computed on-demand?
- [ ] Integration with external compliance management systems?
- [ ] Support for comparing more than two jurisdictions simultaneously?

## Example Use Cases

### Use Case 1: Consulate Building Assessment (Tokyo)

```python
# Step 1: Generate compliance matrix
matrix = generate_compliance_matrix(
    reference_jurisdiction="EU/DE",
    target_jurisdiction="AS/JP",
    domain="construction.*",
    format="checklist"
)

# Step 2: Identify gaps
gaps = analyze_gaps(
    reference="EU/DE",
    target="AS/JP",
    domain="construction.energy"
)

# Step 3: Find local equivalents for specific German requirement
equivalents = find_equivalent_provisions(
    provision="DE/GEG/§71",  # 65% renewable heating
    target_jurisdiction="AS/JP"
)
```

### Use Case 2: Pre-Construction Due Diligence (São Paulo)

```python
# Compare German workplace safety against Brazilian
comparison = compare_jurisdictions(
    jurisdiction_a="EU/DE",
    jurisdiction_b="SA/BR",
    domains=["construction.workplace", "construction.fire-safety"],
    output_format="report"
)
```

## References

- [Semantic Similarity in Legal Texts](https://arxiv.org/abs/2104.12871)
- [Cross-lingual Document Similarity](https://huggingface.co/sentence-transformers/paraphrase-multilingual-mpnet-base-v2)
- [Legal NER for Regulatory Compliance](https://github.com/lexpredict/lexnlp)
- [ISO 19650 (BIM Standards)](https://www.iso.org/standard/68078.html) — Potential future integration