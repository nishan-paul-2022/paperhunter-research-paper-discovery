#!/usr/bin/env python3
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from lxml import etree

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
XML_PATH = ARCHIVE_DIR / "dblp.xml"
DB_PATH = ARCHIVE_DIR / "paperhunter.db"
YEARS = [2023, 2024, 2025, 2026]

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


def load_list(conn, view, rank_column):
    index = dict(conn.execute(f"SELECT dblp_key, {rank_column} FROM {view}"))
    if not index:
        sys.exit(f"ERROR: {view} is empty — finish its review first (see conference-hunter/ and journal-hunter/ matching guides)")
    return index


def extract_text(element, tag):
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def extract_authors(element):
    authors = element.findall("author")
    if not authors:
        authors = element.findall("editor")
    return "; ".join(a.text.strip() for a in authors if a.text)


def extract_doi(element):
    for ee in element.findall("ee"):
        m = DOI_RE.search(ee.text or "")
        if m:
            return m.group(0).rstrip(".,;)").lower()
    return None


def parse_paper(element, venue_key):
    year_str = extract_text(element, "year")
    if not year_str.isdigit() or int(year_str) not in YEARS:
        return None
    return (
        element.get("key", ""),
        venue_key,
        int(year_str),
        " ".join("".join(element.find("title").itertext()).split()) if element.find("title") is not None else "",
        extract_authors(element),
        extract_text(element, "journal") or extract_text(element, "booktitle"),
        extract_doi(element),
    )


def scan_xml(xml_path, journal_index, conf_index):
    size = os.path.getsize(xml_path)
    papers, ranks = [], Counter()
    scanned = 0
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║             paperhunter  ·  Paper Extractor                          ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"Processing DBLP XML ({size / 1_000_000_000:.1f} GB)...")
    print(f"Target years: {', '.join(map(str, YEARS))}")
    print(f"Journals: {len(journal_index)} | Conferences: {len(conf_index)}")
    print()
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag=("article", "inproceedings"),
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )
    for _, elem in ctx:
        scanned += 1
        parts = elem.get("key", "").split("/")
        if len(parts) >= 3:
            venue_key = "/".join(parts[:2])
            index = journal_index if elem.tag == "article" else conf_index
            if venue_key in index:
                paper = parse_paper(elem, venue_key)
                if paper:
                    papers.append(paper)
                    ranks[index[venue_key]] += 1
        if scanned % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"\r[Progress: {pct:3}%] Scanned: {scanned:,} papers | Matched: {len(papers):,} papers",
                  end="", flush=True)
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    f.close()
    print(f"\r[Progress: 100%] Scanned: {scanned:,} papers | Matched: {len(papers):,} papers")
    print()
    print("Statistics:")
    print("  Journals:     " + "   ".join(f"{q}={ranks[q]:,}" for q in ("Q1", "Q2", "Q3", "Q4")))
    print("  Conferences:  " + "   ".join(f"{r}={ranks[r]:,}" for r in ("A*", "A", "B", "C")))
    print()
    return papers


def write_papers(conn, papers):
    print("Syncing paper_dblp...")
    before = conn.execute("SELECT count(*) FROM paper_dblp").fetchone()[0]
    with conn:
        conn.executemany(
            "INSERT INTO paper_dblp (dblp_key, venue_key, year, title, authors, venue, doi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (dblp_key) DO UPDATE SET "
            "venue_key = excluded.venue_key, year = excluded.year, title = excluded.title, "
            "authors = excluded.authors, venue = excluded.venue, doi = excluded.doi",
            papers,
        )
        conn.execute("CREATE TEMP TABLE extracted (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO extracted VALUES (?)", [(p[0],) for p in papers])
        removed = conn.execute("DELETE FROM paper_dblp WHERE dblp_key NOT IN (SELECT dblp_key FROM extracted)").rowcount
        conn.execute("DROP TABLE extracted")
    after = conn.execute("SELECT count(*) FROM paper_dblp").fetchone()[0]
    print(f"  ✓ {len(papers):,} papers extracted  |  {after - before + removed:,} new  |  {removed:,} removed"
          + ("  (their details and decisions too)" if removed else ""))
    print()
    print("Papers per year:")
    for y, conf, jour in conn.execute(
            "SELECT year, sum(venue_key LIKE 'conf/%'), sum(venue_key LIKE 'journals/%') FROM paper_dblp "
            "GROUP BY year ORDER BY year"):
        print(f"  {y}   conferences {conf:>8,}   journals {jour:>8,}")
    print()


def main():
    if not XML_PATH.exists():
        sys.exit(f"ERROR: dblp.xml not found at {XML_PATH}")
    if not DB_PATH.exists():
        sys.exit(f"ERROR: database not found at {DB_PATH} (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    print("Loading journal and conference lists...")
    journal_index = load_list(conn, "journal_list", "quartile")
    conf_index = load_list(conn, "conf_list", "ranking")
    print(f"  ✓ {len(journal_index)} journals loaded")
    print(f"  ✓ {len(conf_index)} conferences loaded")
    papers = scan_xml(XML_PATH, journal_index, conf_index)
    write_papers(conn, papers)
    conn.close()
    print("━" * 70)
    print("  Done! Papers are in paper_dblp of archive/paperhunter.db  → Run b_details.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
