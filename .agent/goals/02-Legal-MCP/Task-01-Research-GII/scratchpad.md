# Task-01: Research gesetze-im-internet.de XML Format

> **Status**: 🟢 Complete
> **Started**: 2025-01-23
> **Completed**: 2025-01-23
> **Effort**: 2h (estimated 3h)

---

## Objective

Research and document the XML format, download mechanism, and corpus details for German federal law from gesetze-im-internet.de.

---

## Summary of Findings

### ✅ Key Discovery: No Single Bulk Download

**There is NO single bulk zip file for all laws.** Instead:

1. **Table of Contents XML**: `https://www.gesetze-im-internet.de/gii-toc.xml`
   - Lists all ~6,871 laws with individual download links
   - Updated daily (based on `builddate` attribute)
   - Simple XML structure: `<items><item><title>...</title><link>...</link></item>...</items>`

2. **Per-Law ZIP Downloads**: Each law has its own zip file
   - Pattern: `https://www.gesetze-im-internet.de/{law_abbrev}/xml.zip`
   - Example: `https://www.gesetze-im-internet.de/gg/xml.zip` (Grundgesetz)
   - Each zip contains a single XML file named `{DOKNR}.xml`

### ✅ Corpus Statistics

| Metric | Value |
|--------|-------|
| Total laws/regulations | ~6,871 |
| Average XML size per law | ~50-250 KB |
| Estimated total corpus | ~500 MB - 1 GB raw XML |
| Sample: Grundgesetz (GG) | 244 KB |

### ✅ XML Schema Documentation

**DTD Location**: `https://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd`

**Schema Version**: 1.01 (created 2012-06-25, stable)

**Root Structure**:
```xml
<dokumente builddate="YYYYMMDDHHMMSS" doknr="BJNR...">
  <norm>...</norm>
  <norm>...</norm>
  ...
</dokumente>
```

**Norm Structure** (each paragraph/section is a `<norm>`):
```xml
<norm builddate="..." doknr="...">
  <metadaten>
    <jurabk>GG</jurabk>                    <!-- Law abbreviation -->
    <amtabk>...</amtabk>                   <!-- Official abbreviation (optional) -->
    <ausfertigung-datum manuell="ja">1949-05-23</ausfertigung-datum>
    <fundstelle typ="amtlich">
      <periodikum>BGBl</periodikum>
      <zitstelle>1949, 1</zitstelle>
    </fundstelle>
    <langue>Full law title</langue>
    <kurzue>Short title</kurzue>
    <gliederungseinheit>
      <gliederungskennzahl>010</gliederungskennzahl>
      <gliederungsbez>I.</gliederungsbez>
      <gliederungstitel>Die Grundrechte</gliederungstitel>
    </gliederungseinheit>
    <enbez>Art 1</enbez>                   <!-- Paragraph/Article number -->
    <titel>Paragraph title</titel>
    <standangabe checked="ja">
      <standtyp>Stand</standtyp>
      <standkommentar>Zuletzt geändert durch...</standkommentar>
    </standangabe>
  </metadaten>
  <textdaten>
    <text format="XML">
      <Content>
        <P>Paragraph text...</P>
        <P>(1) Numbered subsection...</P>
      </Content>
    </text>
    <fussnoten>...</fussnoten>
  </textdaten>
</norm>
```

### ✅ Key Metadata Fields

| Field | Description | Example |
|-------|-------------|---------|
| `jurabk` | Law abbreviation | `GG`, `BGB`, `StGB` |
| `amtabk` | Official abbreviation | (when different from jurabk) |
| `langue` | Full official title | "Grundgesetz für die Bundesrepublik Deutschland" |
| `kurzue` | Short title | (optional) |
| `ausfertigung-datum` | Enactment date | `1949-05-23` |
| `fundstelle` | Publication reference | BGBl 1949, 1 |
| `standangabe` | Amendment status | "Zuletzt geändert durch..." |
| `gliederungseinheit` | Structural unit | Book, Part, Section, etc. |
| `enbez` | Paragraph identifier | `Art 1`, `§ 433`, `Anlage 1` |
| `doknr` | Unique document number | `BJNR000010949` |

### ✅ Content Structure

**Hierarchy within a law (observed in GG.xml)**:
1. First `<norm>` = Law-level metadata (title, dates, status)
2. Special sections: Eingangsformel (preamble), Präambel
3. `<gliederungseinheit>` norms = Structural divisions (Parts, Books, Titles)
4. Regular `<norm>` elements = Individual articles/paragraphs

**Text formatting elements**:
- `<P>` - Paragraph
- `<BR/>` - Line break
- `<B>`, `<I>`, `<U>` - Bold, italic, underline
- `<SP>` - Special formatting (emphasis)
- `<DL>`, `<DT>`, `<DD>` - Definition lists (numbered items)
- `<table>` - Tables (CALS format)
- `<FnR>`, `<Footnote>` - Footnote references

### ✅ Update Mechanism

**No dedicated API or RSS feed discovered.**

**Detection strategy**:
1. Re-download `gii-toc.xml` periodically
2. Compare against stored version (git diff)
3. Download only changed/new law zips
4. Check `builddate` attribute for recency

**Update frequency**: 
- Laws are updated after publication in Bundesgesetzblatt
- Typically within days of official publication
- `builddate` in XML indicates last processing date

### ✅ Legal/Terms of Use

**From hinweise.html**:
> "Die Rechtsnormen in deutscher Sprache stehen in allen angebotenen Formaten zur freien Nutzung und Weiterverwendung zur Verfügung."

Translation: "The legal norms in German are available in all offered formats for free use and reuse."

**robots.txt**: Fully permissive (no restrictions)

**Conclusion**: ✅ Free to download, store, and redistribute

---

## Download Strategy for MVP

### Recommended Approach

```python
# Pseudocode for download pipeline

async def download_corpus():
    # 1. Get table of contents
    toc = await fetch("https://www.gesetze-im-internet.de/gii-toc.xml")
    
    # 2. Parse all law entries (~6871)
    laws = parse_toc(toc)  # Returns list of (title, zip_url)
    
    # 3. Download each zip (parallel, rate-limited)
    for law in laws:
        zip_data = await fetch(law.url)
        xml_content = extract_xml(zip_data)
        save_to("data/raw/de-federal/{abbrev}.xml", xml_content)
    
    # 4. Commit to git
    git_commit("Initial German federal law corpus download")
```

### Estimated Download Time

| Approach | Time Estimate |
|----------|---------------|
| Sequential | ~3-4 hours |
| Parallel (10 concurrent) | ~20-30 minutes |
| Parallel (50 concurrent) | ~5-10 minutes |

**Recommendation**: Use 10-20 concurrent connections with 100ms delay between batches to be respectful.

---

## Sample Files Saved

| File | Description |
|------|-------------|
| `.agent/tmp/gii-toc.xml` | Table of contents (partial, in /tmp) |
| `.agent/tmp/gg.zip` | Grundgesetz ZIP |
| `.agent/tmp/gg_xml/BJNR000010949.xml` | Grundgesetz XML (extracted) |
| `.agent/tmp/gii-norm.dtd` | Official DTD schema |
| `.agent/tmp/gii_hinweise.html` | Hinweise page (terms of use) |

---

## Blockers Resolved

| Question | Answer |
|----------|--------|
| Is there a bulk download? | No, individual zips per law via TOC XML |
| What's the XML schema? | DTD v1.01, well-documented |
| How many laws? | ~6,871 |
| Update mechanism? | Compare TOC XML periodically |
| Legal to use? | Yes, explicitly free for reuse |

---

## Implications for Task-04 (Downloader)

The downloader needs to:

1. **Fetch TOC**: Parse `gii-toc.xml` to get all law URLs
2. **Parallel downloads**: Use async/aiohttp with rate limiting
3. **Extract ZIPs**: Each zip contains one XML file
4. **Handle naming**: Use law abbreviation from URL for filename
5. **Incremental updates**: Store TOC hash, re-download only changed laws
6. **Progress tracking**: ~6871 downloads, show progress bar

---

## Next Steps

1. ✅ Task-01 complete - Research documented
2. → Task-02: Add dependencies (lxml, aiohttp, chromadb, sentence-transformers)
3. → Task-03: Set up `data/` directory structure

---

## References

- **TOC XML**: https://www.gesetze-im-internet.de/gii-toc.xml
- **DTD Schema**: https://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd
- **Terms of Use**: https://www.gesetze-im-internet.de/hinweise.html
- **Sample Law (GG)**: https://www.gesetze-im-internet.de/gg/xml.zip