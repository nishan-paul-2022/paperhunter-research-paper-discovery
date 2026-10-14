#!/usr/bin/env python3
"""
Paper Extractor - Extract papers from DBLP XML based on journal/conference lists.

Outputs: paper-list/q1-2023.csv, paper-list/a-star-2024.csv, etc.
"""

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml. Run: pip install lxml")

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
XML_PATH = SCRIPT_DIR / "dblp.xml"
OUTPUT_DIR = REPO_ROOT / "paper-list"

JOURNAL_LIST = REPO_ROOT / "journal-context" / "list-of-journals.csv"
CONF_LIST = REPO_ROOT / "conference-context" / "list-of-conferences.csv"

# Year range
YEARS = [2023, 2024, 2025, 2026]

# CSV output fields
FIELDS = [
    "dblp_key",
    "title",
    "authors",
    "year",
    "venue",
    "ee",
    "url"
]


def load_journal_list():
    """Load journal list and build index: dblp_key -> quartile."""
    journal_index = {}
    with open(JOURNAL_LIST, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            journal_index[row['dblp_key']] = row['quartile']
    return journal_index


def load_conference_list():
    """Load conference list and build index: dblp_key -> ranking."""
    conf_index = {}
    with open(CONF_LIST, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            conf_index[row['dblp_key']] = row['ranking']
    return conf_index


def extract_text(element, tag):
    """Extract text from child tag, return empty string if not found."""
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def extract_authors(element):
    """Extract all authors, join with '; '."""
    authors = element.findall("author")
    if not authors:
        authors = element.findall("editor")
    return "; ".join(a.text.strip() for a in authors if a.text)


def parse_paper(element, paper_type):
    """Parse article/inproceedings element into dict."""
    key = element.get("key", "")
    year_str = extract_text(element, "year")
    
    try:
        year = int(year_str)
    except (ValueError, TypeError):
        return None
    
    if year not in YEARS:
        return None
    
    return {
        "dblp_key": key,
        "title": extract_text(element, "title"),
        "authors": extract_authors(element),
        "year": year,
        "venue": extract_text(element, "journal") or extract_text(element, "booktitle"),
        "ee": extract_text(element, "ee"),
        "url": extract_text(element, "url"),
    }


def scan_xml(xml_path, journal_index, conf_index):
    """
    Scan DBLP XML and extract papers matching journal/conference lists.
    
    Returns: dict with keys like 'q1-2023', 'a-star-2024', values are lists of paper dicts.
    """
    size = os.path.getsize(xml_path)
    
    # Output storage
    papers = defaultdict(list)
    
    # Stats
    stats = {
        'scanned': 0,
        'matched': 0,
        'journals': {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0},
        'conferences': {'A*': 0, 'A': 0, 'B': 0, 'C': 0},
    }
    
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║             paperhunter  ·  Paper Extractor                          ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"Processing DBLP XML ({size / 1_000_000_000:.1f} GB)...")
    print(f"Target years: {', '.join(map(str, YEARS))}")
    print(f"Journals: {len(journal_index)} | Conferences: {len(conf_index)}")
    print()
    
    f = open(xml_path, 'rb')
    
    # Parse both <article> and <inproceedings>
    ctx = etree.iterparse(
        f, events=('end',), tag=('article', 'inproceedings'),
        load_dtd=True, resolve_entities=True, huge_tree=True
    )
    
    for event, elem in ctx:
        stats['scanned'] += 1
        
        key = elem.get('key', '')
        tag_name = elem.tag
        
        # Try to match journal
        if tag_name == 'article':
            # Extract journal key prefix: journals/4or/Paper2023 -> journals/4or
            parts = key.split('/')
            if len(parts) >= 2:
                journal_key = '/'.join(parts[:2])  # journals/4or
                
                if journal_key in journal_index:
                    paper = parse_paper(elem, 'journal')
                    if paper:
                        quartile = journal_index[journal_key]
                        year = paper['year']
                        
                        # Store in q1-2023, q2-2024, etc.
                        file_key = f"{quartile.lower()}-{year}"
                        papers[file_key].append(paper)
                        
                        stats['matched'] += 1
                        stats['journals'][quartile] += 1
        
        # Try to match conference
        elif tag_name == 'inproceedings':
            # Extract conference key prefix: conf/sigcomm/Paper2023 -> conf/sigcomm
            parts = key.split('/')
            if len(parts) >= 2:
                conf_key = '/'.join(parts[:2])  # conf/sigcomm
                
                if conf_key in conf_index:
                    paper = parse_paper(elem, 'conference')
                    if paper:
                        ranking = conf_index[conf_key]
                        year = paper['year']
                        
                        # Store in a-star-2023, a-2024, b-2023, etc.
                        if ranking == 'A*':
                            file_key = f"a-star-{year}"
                        else:
                            file_key = f"{ranking.lower()}-{year}"
                        
                        papers[file_key].append(paper)
                        
                        stats['matched'] += 1
                        stats['conferences'][ranking] += 1
        
        # Progress display every 500k papers
        if stats['scanned'] % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"\r[Progress: {pct:3}%] Scanned: {stats['scanned']:,} papers | "
                  f"Matched: {stats['matched']:,} papers", end='', flush=True)
        
        # Clear element to save memory
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    
    f.close()
    
    # Final progress
    print(f"\r[Progress: 100%] Scanned: {stats['scanned']:,} papers | "
          f"Matched: {stats['matched']:,} papers")
    print()
    
    # Stats breakdown
    print("Statistics:")
    print(f"  Journals:     Q1={stats['journals']['Q1']:,}   Q2={stats['journals']['Q2']:,}   "
          f"Q3={stats['journals']['Q3']:,}   Q4={stats['journals']['Q4']:,}")
    print(f"  Conferences:  A*={stats['conferences']['A*']:,}   A={stats['conferences']['A']:,}   "
          f"B={stats['conferences']['B']:,}   C={stats['conferences']['C']:,}")
    print()
    
    return papers


def write_papers(papers, output_dir):
    """Write papers to separate CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("Writing CSV files...")
    
    file_counts = {}
    
    for file_key, paper_list in sorted(papers.items()):
        csv_path = output_dir / f"{file_key}.csv"
        
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(paper_list)
        
        file_counts[file_key] = len(paper_list)
        print(f"  ✓ {file_key}.csv: {len(paper_list):,} papers")
    
    print()
    print(f"Total: {len(papers)} files written to {output_dir}/")
    print()


def main():
    # Check files exist
    if not XML_PATH.exists():
        sys.exit(f"ERROR: dblp.xml not found at {XML_PATH}")
    
    if not JOURNAL_LIST.exists():
        sys.exit(f"ERROR: Journal list not found at {JOURNAL_LIST}")
    
    if not CONF_LIST.exists():
        sys.exit(f"ERROR: Conference list not found at {CONF_LIST}")
    
    # Load indices
    print("Loading journal and conference lists...")
    journal_index = load_journal_list()
    conf_index = load_conference_list()
    print(f"  ✓ {len(journal_index)} journals loaded")
    print(f"  ✓ {len(conf_index)} conferences loaded")
    
    # Scan and extract
    papers = scan_xml(XML_PATH, journal_index, conf_index)
    
    # Write output
    write_papers(papers, OUTPUT_DIR)
    
    print("━" * 70)
    print("  Done! Papers extracted to paper-list/ directory.")
    print("━" * 70)
    print()


if __name__ == '__main__':
    main()
