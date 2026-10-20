#!/usr/bin/env python3
import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
CORE_SRC = ARCHIVE_DIR / "publication-core-2026.csv"
XML_PATH = ARCHIVE_DIR / "paper-dblp.xml"
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
VALID_RANKS = ("A*", "A", "B", "C")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run archive/schema.py first)")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_core(conn: sqlite3.Connection, core_path: Path) -> None:
    print("━" * 70)
    print("  Phase 1 – CORE rankings → core_venue")
    print("━" * 70)
    rows, dropped = [], 0
    with open(core_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rank = r.get("Ranking", "").strip()
            if rank not in VALID_RANKS:
                dropped += 1
                continue
            rows.append((r["Title"].strip(), r["Acronym"].strip(), rank))
    duplicates = [t for t, n in Counter(t for t, _, _ in rows).items() if n > 1]
    if duplicates:
        sys.exit(f"Duplicate CORE titles: {duplicates}")
    with conn:
        conn.executemany(
            "INSERT INTO core_venue (title, acronym, rank) VALUES (?, ?, ?) "
            "ON CONFLICT (title) DO UPDATE SET acronym = excluded.acronym, rank = excluded.rank",
            rows,
        )
        conn.execute("CREATE TEMP TABLE core_titles (title TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO core_titles VALUES (?)", [(t,) for t, _, _ in rows])
        removed = conn.execute(
            "DELETE FROM core_venue WHERE title NOT IN (SELECT title FROM core_titles)"
        ).rowcount
        conn.execute("DROP TABLE core_titles")
    print(f"  ✓  {len(rows)} CORE venues  |  {dropped} rows outside A*–C skipped  |  {removed} removed\n")


def scan_xml(xml_path: Path):
    from lxml import etree

    size = os.path.getsize(xml_path)
    seen = 0
    papers: Counter = Counter()
    booktitles: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, list[int]] = {}
    proceedings: dict[str, set[tuple[int, str]]] = defaultdict(set)
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag=("inproceedings", "proceedings"),
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        parts = key.split("/")
        if key.startswith("conf/") and len(parts) >= 3:
            prefix = "/".join(parts[:2])
            year_text = el.findtext("year") or ""
            year = int(year_text) if year_text.isdigit() else 0
            if el.tag == "inproceedings":
                papers[prefix] += 1
                booktitle = (el.findtext("booktitle") or "").strip()
                if booktitle:
                    booktitles[prefix][booktitle] += 1
                if year:
                    lo, hi = years.get(prefix, (year, year))
                    years[prefix] = [min(lo, year), max(hi, year)]
            else:
                title_el = el.find("title")
                if title_el is not None:
                    title = " ".join("".join(title_el.itertext()).split())
                    if title:
                        proceedings[prefix].add((year, title))
        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  |  records scanned: {seen:,}  |  conference keys: {len(papers):,}", end="\r", flush=True)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    f.close()
    print(f"  100%  |  records scanned: {seen:,}  |  conference keys: {len(papers):,}")
    venues = [(k, n, *years.get(k, (None, None))) for k, n in papers.items()]
    bts = [(k, b, n) for k in papers for b, n in booktitles[k].items()]
    pts = [(k, y, t) for k in papers for y, t in proceedings.get(k, ())]
    return venues, bts, pts


def load_dblp(conn: sqlite3.Connection, xml_path: Path) -> None:
    print("━" * 70)
    print("  Phase 2 – DBLP evidence → dblp_venue, dblp_booktitle, dblp_proceedings")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB, ~15–20 min)\n")
    venues, bts, pts = scan_xml(xml_path)
    with conn:
        conn.executemany(
            "INSERT INTO dblp_venue (dblp_key, papers, first_year, last_year) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (dblp_key) DO UPDATE SET papers = excluded.papers, "
            "first_year = excluded.first_year, last_year = excluded.last_year",
            venues,
        )
        conn.execute("CREATE TEMP TABLE scanned (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO scanned VALUES (?)", [(v[0],) for v in venues])
        removed = conn.execute(
            "DELETE FROM dblp_venue WHERE dblp_key NOT IN (SELECT dblp_key FROM scanned)"
        ).rowcount
        conn.execute("DROP TABLE scanned")
        conn.execute("DELETE FROM dblp_booktitle")
        conn.execute("DELETE FROM dblp_proceedings")
        conn.executemany("INSERT INTO dblp_booktitle VALUES (?, ?, ?)", bts)
        conn.executemany("INSERT INTO dblp_proceedings VALUES (?, ?, ?)", pts)
    print(f"\n  ✓  {len(venues):,} keys  |  {len(bts):,} booktitles  |  {len(pts):,} proceedings  |  {removed} keys removed\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", default=str(XML_PATH), help=f"Path to paper-dblp.xml  (default: {XML_PATH.name})")
    ap.add_argument("--core", default=str(CORE_SRC), help=f"Path to CORE CSV  (default: {CORE_SRC.name})")
    ap.add_argument("--skip-xml", action="store_true", help="Keep the DBLP evidence already in the database")
    args = ap.parse_args()
    xml_path, core_path = Path(args.xml), Path(args.core)
    if not core_path.exists():
        sys.exit(f"CORE CSV not found: {core_path}")
    if not args.skip_xml and not xml_path.exists():
        sys.exit(f"paper-dblp.xml not found: {xml_path}\nRun the downloader first.")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Conference Evidence Loader                   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    load_core(conn, core_path)
    if args.skip_xml:
        n = conn.execute("SELECT count(*) FROM dblp_venue").fetchone()[0]
        if not n:
            sys.exit("--skip-xml given but the database has no DBLP evidence yet")
        print(f"  Skipping XML scan: keeping {n:,} DBLP keys already loaded\n")
    else:
        load_dblp(conn, xml_path)
    conn.close()
    print("━" * 70)
    print("  Done.  → Run b_match.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
