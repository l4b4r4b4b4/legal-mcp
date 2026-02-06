#!/usr/bin/env python3
"""Analyze the German federal law corpus structure.

This script analyzes the downloaded XML corpus to understand:
- Document counts and sizes
- Structure (norms, paragraphs, sections)
- Text content statistics
- Chunking requirements for embedding

Usage:
    uv run python scripts/analyze_corpus.py [OPTIONS]

Options:
    --corpus-dir PATH    Corpus directory (default: data/raw/de-federal)
    --sample N           Analyze only N random files (for quick testing)
    --output PATH        Save detailed report to JSON file
    --verbose            Show per-file details
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from collections.abc import Iterator

# Constants
DEFAULT_CORPUS_DIR = Path("data/raw/de-federal")


@dataclass
class NormInfo:
    """Information about a single norm (paragraph/section)."""

    doknr: str
    enbez: str  # e.g., "Art 1", "§ 433"
    title: str
    text_length: int
    paragraph_count: int  # Number of <P> elements
    has_footnotes: bool
    has_tables: bool


@dataclass
class LawInfo:
    """Information about a single law/regulation."""

    filename: str
    abbreviation: str  # jurabk
    full_title: str  # langue
    enactment_date: str
    last_amended: str
    file_size_bytes: int
    norm_count: int
    total_text_length: int
    total_paragraphs: int
    structure_levels: list[str]  # gliederungsbez values
    norms: list[NormInfo] = field(default_factory=list)


@dataclass
class CorpusStats:
    """Aggregate statistics for the entire corpus."""

    total_files: int = 0
    total_size_bytes: int = 0
    total_norms: int = 0
    total_paragraphs: int = 0
    total_text_chars: int = 0

    # Distributions
    norms_per_law: list[int] = field(default_factory=list)
    paragraphs_per_norm: list[int] = field(default_factory=list)
    text_length_per_norm: list[int] = field(default_factory=list)

    # Categorization
    law_types: Counter = field(default_factory=Counter)
    structure_types: Counter = field(default_factory=Counter)

    # Samples
    largest_laws: list[tuple[str, int]] = field(default_factory=list)
    smallest_laws: list[tuple[str, int]] = field(default_factory=list)

    def add_law(self, law: LawInfo) -> None:
        """Add a law's statistics to the aggregate."""
        self.total_files += 1
        self.total_size_bytes += law.file_size_bytes
        self.total_norms += law.norm_count
        self.total_paragraphs += law.total_paragraphs
        self.total_text_chars += law.total_text_length

        self.norms_per_law.append(law.norm_count)

        for norm in law.norms:
            self.paragraphs_per_norm.append(norm.paragraph_count)
            self.text_length_per_norm.append(norm.text_length)

        # Categorize by type
        title_lower = law.full_title.lower()
        if "verordnung" in title_lower:
            self.law_types["Verordnung"] += 1
        elif "gesetz" in title_lower:
            self.law_types["Gesetz"] += 1
        elif "bekanntmachung" in title_lower:
            self.law_types["Bekanntmachung"] += 1
        elif "abkommen" in title_lower or "vertrag" in title_lower:
            self.law_types["Abkommen/Vertrag"] += 1
        elif "anordnung" in title_lower:
            self.law_types["Anordnung"] += 1
        elif "satzung" in title_lower:
            self.law_types["Satzung"] += 1
        else:
            self.law_types["Sonstige"] += 1

        # Track structure types
        for level in law.structure_levels:
            self.structure_types[level] += 1

    def finalize(self) -> None:
        """Compute final statistics after all laws are added."""
        # Sort largest/smallest
        self.largest_laws = sorted(self.largest_laws, key=lambda x: -x[1])[:10]
        self.smallest_laws = sorted(self.smallest_laws, key=lambda x: x[1])[:10]

    def percentile(self, values: list[int], p: float) -> int:
        """Calculate percentile of a list of values."""
        if not values:
            return 0
        sorted_values = sorted(values)
        index = int(len(sorted_values) * p / 100)
        return sorted_values[min(index, len(sorted_values) - 1)]

    def print_report(self) -> None:
        """Print a formatted analysis report."""
        print("\n" + "=" * 70)
        print("GERMAN FEDERAL LAW CORPUS ANALYSIS")
        print("=" * 70)

        # Overview
        print("\n## Overview\n")
        print(f"Total files:        {self.total_files:,}")
        print(f"Total size:         {self.total_size_bytes / (1024 * 1024):.2f} MB")
        print(f"Total norms:        {self.total_norms:,}")
        print(f"Total paragraphs:   {self.total_paragraphs:,}")
        print(f"Total text chars:   {self.total_text_chars:,}")

        # Averages
        print("\n## Averages\n")
        if self.total_files > 0:
            print(f"Norms per law:      {self.total_norms / self.total_files:.1f}")
            print(f"Paragraphs per law: {self.total_paragraphs / self.total_files:.1f}")
            print(
                f"Text per law:       {self.total_text_chars / self.total_files / 1000:.1f} KB"
            )
        if self.total_norms > 0:
            print(
                f"Paragraphs per norm: {self.total_paragraphs / self.total_norms:.1f}"
            )
            print(
                f"Text per norm:       {self.total_text_chars / self.total_norms:.0f} chars"
            )

        # Distributions
        print("\n## Distributions\n")
        if self.norms_per_law:
            print("Norms per law:")
            print(f"  Min:    {min(self.norms_per_law):,}")
            print(f"  Median: {self.percentile(self.norms_per_law, 50):,}")
            print(f"  P90:    {self.percentile(self.norms_per_law, 90):,}")
            print(f"  P99:    {self.percentile(self.norms_per_law, 99):,}")
            print(f"  Max:    {max(self.norms_per_law):,}")

        if self.text_length_per_norm:
            print("\nText length per norm (chars):")
            print(f"  Min:    {min(self.text_length_per_norm):,}")
            print(f"  Median: {self.percentile(self.text_length_per_norm, 50):,}")
            print(f"  P90:    {self.percentile(self.text_length_per_norm, 90):,}")
            print(f"  P99:    {self.percentile(self.text_length_per_norm, 99):,}")
            print(f"  Max:    {max(self.text_length_per_norm):,}")

        # Law types
        print("\n## Law Types\n")
        for law_type, count in self.law_types.most_common():
            pct = count / self.total_files * 100 if self.total_files > 0 else 0
            print(f"  {law_type:20s} {count:5,} ({pct:5.1f}%)")

        # Structure types
        print("\n## Structure Levels (Gliederung)\n")
        for struct_type, count in self.structure_types.most_common(15):
            print(f"  {struct_type:20s} {count:,}")

        # Embedding estimates
        print("\n## Embedding Estimates\n")
        # Assume ~500 chars per chunk for embedding
        chunk_size = 500
        estimated_chunks = self.total_text_chars // chunk_size
        # Each embedding is ~1536 floats * 4 bytes for OpenAI, or 768 * 4 for multilingual
        embedding_dim = 768
        embedding_bytes = estimated_chunks * embedding_dim * 4
        print(f"Assuming ~{chunk_size} chars per chunk:")
        print(f"  Estimated chunks:     {estimated_chunks:,}")
        print(f"  Embedding dimensions: {embedding_dim}")
        print(
            f"  Estimated storage:    {embedding_bytes / (1024 * 1024 * 1024):.2f} GB"
        )

        # Alternative: one embedding per norm
        print(f"\nIf one embedding per norm ({self.total_norms:,} norms):")
        norm_embedding_bytes = self.total_norms * embedding_dim * 4
        print(f"  Estimated storage:    {norm_embedding_bytes / (1024 * 1024):.1f} MB")

        print("\n" + "=" * 70)


def extract_text(element: ElementTree.Element | None) -> str:
    """Extract all text content from an XML element recursively."""
    if element is None:
        return ""
    return "".join(element.itertext())


def parse_law(filepath: Path) -> LawInfo | None:
    """Parse a single law XML file and extract structure information.

    Args:
        filepath: Path to the XML file

    Returns:
        LawInfo object or None if parsing fails
    """
    try:
        tree = ElementTree.parse(filepath)
        root = tree.getroot()
    except ElementTree.ParseError as e:
        print(f"  Warning: Failed to parse {filepath.name}: {e}", file=sys.stderr)
        return None

    # Initialize with defaults
    abbreviation = ""
    full_title = ""
    enactment_date = ""
    last_amended = ""
    structure_levels: list[str] = []
    norms: list[NormInfo] = []

    # Process each norm
    for norm in root.findall(".//norm"):
        doknr = norm.get("doknr", "")
        metadaten = norm.find("metadaten")
        textdaten = norm.find("textdaten")

        if metadaten is None:
            continue

        # Extract metadata
        jurabk_elem = metadaten.find("jurabk")
        if jurabk_elem is not None and jurabk_elem.text:
            abbreviation = jurabk_elem.text

        langue_elem = metadaten.find("langue")
        if langue_elem is not None:
            title_text = extract_text(langue_elem)
            if title_text and not full_title:
                full_title = title_text

        date_elem = metadaten.find("ausfertigung-datum")
        if date_elem is not None and date_elem.text:
            enactment_date = date_elem.text

        stand_elem = metadaten.find(".//standkommentar")
        if stand_elem is not None:
            stand_text = extract_text(stand_elem)
            if stand_text:
                last_amended = stand_text

        # Track structure levels
        gliederung = metadaten.find("gliederungseinheit")
        if gliederung is not None:
            bez_elem = gliederung.find("gliederungsbez")
            if bez_elem is not None and bez_elem.text:
                structure_levels.append(bez_elem.text)

        # Extract norm details
        enbez_elem = metadaten.find("enbez")
        enbez = enbez_elem.text if enbez_elem is not None and enbez_elem.text else ""

        titel_elem = metadaten.find("titel")
        titel = extract_text(titel_elem) if titel_elem is not None else ""

        # Text content
        text_content = ""
        paragraph_count = 0
        has_footnotes = False
        has_tables = False

        if textdaten is not None:
            text_elem = textdaten.find(".//text")
            if text_elem is not None:
                text_content = extract_text(text_elem)
                paragraph_count = len(text_elem.findall(".//P"))

            has_footnotes = textdaten.find(".//fussnoten") is not None
            has_tables = textdaten.find(".//table") is not None

        norms.append(
            NormInfo(
                doknr=doknr,
                enbez=enbez,
                title=titel,
                text_length=len(text_content),
                paragraph_count=paragraph_count,
                has_footnotes=has_footnotes,
                has_tables=has_tables,
            )
        )

    # Calculate totals
    total_text = sum(n.text_length for n in norms)
    total_paragraphs = sum(n.paragraph_count for n in norms)

    return LawInfo(
        filename=filepath.name,
        abbreviation=abbreviation,
        full_title=full_title,
        enactment_date=enactment_date,
        last_amended=last_amended,
        file_size_bytes=filepath.stat().st_size,
        norm_count=len(norms),
        total_text_length=total_text,
        total_paragraphs=total_paragraphs,
        structure_levels=list(set(structure_levels)),
        norms=norms,
    )


def iter_corpus(corpus_dir: Path, sample: int | None = None) -> Iterator[Path]:
    """Iterate over corpus files, optionally sampling.

    Args:
        corpus_dir: Directory containing XML files
        sample: If set, randomly sample this many files

    Yields:
        Paths to XML files
    """
    files = list(corpus_dir.glob("*.xml"))

    if sample is not None and sample < len(files):
        files = random.sample(files, sample)

    yield from sorted(files)


def analyze_corpus(
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    sample: int | None = None,
    verbose: bool = False,
) -> CorpusStats:
    """Analyze the entire corpus and compute statistics.

    Args:
        corpus_dir: Directory containing XML files
        sample: If set, analyze only this many random files
        verbose: Print per-file details

    Returns:
        CorpusStats with aggregate statistics
    """
    stats = CorpusStats()
    files = list(iter_corpus(corpus_dir, sample))
    total = len(files)

    print(f"Analyzing {total:,} files from {corpus_dir}...")
    if sample:
        print(f"(Sampling {sample} files)")
    print()

    for i, filepath in enumerate(files, 1):
        if verbose:
            print(f"[{i}/{total}] {filepath.name}")

        law = parse_law(filepath)
        if law is None:
            continue

        stats.add_law(law)
        stats.largest_laws.append((law.abbreviation, law.norm_count))
        stats.smallest_laws.append((law.abbreviation, law.norm_count))

        # Progress indicator
        if not verbose and i % 500 == 0:
            print(f"  Processed {i:,}/{total:,} files...")

    stats.finalize()
    return stats


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze German federal law corpus structure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"Corpus directory (default: {DEFAULT_CORPUS_DIR})",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Analyze only N random files (for quick testing)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save detailed report to JSON file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-file details",
    )

    args = parser.parse_args()

    if not args.corpus_dir.exists():
        print(f"Error: Corpus directory not found: {args.corpus_dir}", file=sys.stderr)
        return 1

    stats = analyze_corpus(
        corpus_dir=args.corpus_dir,
        sample=args.sample,
        verbose=args.verbose,
    )

    stats.print_report()

    # Save JSON report if requested
    if args.output:
        report = {
            "total_files": stats.total_files,
            "total_size_bytes": stats.total_size_bytes,
            "total_norms": stats.total_norms,
            "total_paragraphs": stats.total_paragraphs,
            "total_text_chars": stats.total_text_chars,
            "law_types": dict(stats.law_types),
            "structure_types": dict(stats.structure_types),
            "largest_laws": stats.largest_laws,
            "distributions": {
                "norms_per_law": {
                    "min": min(stats.norms_per_law) if stats.norms_per_law else 0,
                    "median": stats.percentile(stats.norms_per_law, 50),
                    "p90": stats.percentile(stats.norms_per_law, 90),
                    "p99": stats.percentile(stats.norms_per_law, 99),
                    "max": max(stats.norms_per_law) if stats.norms_per_law else 0,
                },
                "text_per_norm": {
                    "min": min(stats.text_length_per_norm)
                    if stats.text_length_per_norm
                    else 0,
                    "median": stats.percentile(stats.text_length_per_norm, 50),
                    "p90": stats.percentile(stats.text_length_per_norm, 90),
                    "p99": stats.percentile(stats.text_length_per_norm, 99),
                    "max": max(stats.text_length_per_norm)
                    if stats.text_length_per_norm
                    else 0,
                },
            },
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nDetailed report saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
