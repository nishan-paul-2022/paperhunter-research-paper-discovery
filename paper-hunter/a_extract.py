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
XML_PATH = ARCHIVE_DIR / "paper-dblp.xml"
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


def parse_paper(element, venue_key, kind):
    year_str = extract_text(element, "year")
    if not year_str.isdigit() or int(year_str) not in YEARS:
        return None
    return (
        element.get("key", ""),
        venue_key,
        kind,
        int(year_str),
        " ".join("".join(element.find("title").itertext()).split()) if element.find("title") is not None else "",
        extract_authors(element),
        extract_text(element, "journal") or extract_text(element, "booktitle"),
        extract_text(element, "ee") or None,
        extract_text(element, "url") or None,
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
            index, kind = (journal_index, "journal") if elem.tag == "article" else (conf_index, "conference")
            if venue_key in index:
                paper = parse_paper(elem, venue_key, kind)
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
    print("Syncing dblp_paper...")
    before = conn.execute("SELECT count(*) FROM dblp_paper").fetchone()[0]
    users = [t for t in ("paper_source", "paper_accepted", "paper_rejected")
             if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()]
    used = " OR ".join(f"id IN (SELECT paper_id FROM {t})" for t in users)
    with conn:
        conn.executemany(
            "INSERT INTO dblp_paper (dblp_key, venue_key, kind, year, title, authors, venue, ee, url, doi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (dblp_key) DO UPDATE SET "
            "venue_key = excluded.venue_key, kind = excluded.kind, year = excluded.year, title = excluded.title, "
            "authors = excluded.authors, venue = excluded.venue, ee = excluded.ee, url = excluded.url, "
            "doi = excluded.doi",
            papers,
        )
        conn.execute("CREATE TEMP TABLE extracted (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO extracted VALUES (?)", [(p[0],) for p in papers])
        stale = "dblp_key NOT IN (SELECT dblp_key FROM extracted)"
        kept = 0
        if used:
            kept = conn.execute(f"SELECT count(*) FROM dblp_paper WHERE {stale} AND ({used})").fetchone()[0]
            stale += f" AND NOT ({used})"
        removed = conn.execute(f"DELETE FROM dblp_paper WHERE {stale}").rowcount
        conn.execute("DROP TABLE extracted")
    after = conn.execute("SELECT count(*) FROM dblp_paper").fetchone()[0]
    print(f"  ✓ {len(papers):,} papers extracted  |  {after - before + removed:,} new  |  {removed:,} removed"
          + (f"  |  {kept:,} no longer in DBLP but kept (used by paper-hunter)" if kept else ""))
    print()
    print("Papers per slice (paper_inventory):")
    for s, n in conn.execute("SELECT slice, count(*) FROM paper_inventory GROUP BY slice ORDER BY slice"):
        print(f"  {s:<14} {n:>8,}")
    print()


def main():
    if not XML_PATH.exists():
        sys.exit(f"ERROR: paper-dblp.xml not found at {XML_PATH}")
    if not DB_PATH.exists():
        sys.exit(f"ERROR: database not found at {DB_PATH} (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(DB_PATH)
    print("Loading journal and conference lists...")
    journal_index = load_list(conn, "journal_list", "quartile")
    conf_index = load_list(conn, "conf_list", "ranking")
    print(f"  ✓ {len(journal_index)} journals loaded")
    print(f"  ✓ {len(conf_index)} conferences loaded")
    papers = scan_xml(XML_PATH, journal_index, conf_index)
    write_papers(conn, papers)
    conn.close()
    print("━" * 70)
    print("  Done! Papers are in dblp_paper (view: paper_inventory) of archive/paperhunter.db")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
