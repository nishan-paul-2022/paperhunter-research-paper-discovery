#!/usr/bin/env python3
import argparse
import csv
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
SJR_SRC = ARCHIVE_DIR / "publication-scopus-computer-science-journal-2025.csv"
XML_PATH = ARCHIVE_DIR / "paper-dblp.xml"
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
VALID_QUARTILES = ("Q1", "Q2", "Q3", "Q4")

ISSN_HYPHEN = re.compile(r"(?<![\dA-Za-z])(\d{4})-(\d{3}[\dXx])(?![\dA-Za-z])")
ISSN_PII = re.compile(r"(?:pii/|10\.1016/)S(\d{4})-?(\d{3}[\dXx])", re.I)
DOI_PREFIX = re.compile(r"doi\.org/(10\.\d{4,9})/", re.I)


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def format_issn(raw: str) -> str:
    s = raw.strip().replace("-", "").replace(" ", "").upper()
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 else ""


def issn_valid(issn: str) -> bool:
    digits = issn.replace("-", "")
    if len(digits) != 8 or not digits[:7].isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def link_issns(url: str) -> set[str]:
    found = {f"{a}-{b.upper()}" for a, b in ISSN_HYPHEN.findall(url)}
    found |= {f"{a}-{b.upper()}" for a, b in ISSN_PII.findall(url)}
    return {i for i in found if issn_valid(i)}


def load_sjr(conn: sqlite3.Connection, sjr_path: Path) -> None:
    print("━" * 70)
    print("  Phase 1 – Scopus/SJR rankings → journal_sjr")
    print("━" * 70)
    rows, dropped = [], 0
    with open(sjr_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            q = r.get("SJR Best Quartile", "").strip()
            if q not in VALID_QUARTILES:
                dropped += 1
                continue
            issns = [format_issn(p) for p in r.get("Issn", "").split(",") if p.strip()]
            issns = [i for i in issns if i] + ["", ""]
            rows.append((int(r["Sourceid"]), r["Title"].strip(), issns[0], issns[1], r["Publisher"].strip(), q))
    with conn:
        conn.executemany(
            "INSERT INTO journal_sjr (id, title, issn_1, issn_2, publisher, quartile) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET title = excluded.title, issn_1 = excluded.issn_1, "
            "issn_2 = excluded.issn_2, publisher = excluded.publisher, quartile = excluded.quartile",
            rows,
        )
        conn.execute("CREATE TEMP TABLE sjr_ids (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO sjr_ids VALUES (?)", [(r[0],) for r in rows])
        removed = conn.execute("DELETE FROM journal_sjr WHERE id NOT IN (SELECT id FROM sjr_ids)").rowcount
        conn.execute("DROP TABLE sjr_ids")
    print(f"  ✓  {len(rows)} SJR journals  |  {dropped} rows without Q1–Q4 skipped  |  {removed} removed\n")


def scan_xml(xml_path: Path):
    from lxml import etree

    size = os.path.getsize(xml_path)
    seen = 0
    papers: Counter = Counter()
    names: dict[str, Counter] = defaultdict(Counter)
    issns: dict[str, Counter] = defaultdict(Counter)
    prefixes: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, list[int]] = {}
    f = open(xml_path, "rb")
    ctx = etree.iterparse(f, events=("end",), tag="article", load_dtd=True, resolve_entities=True, huge_tree=True)
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        parts = key.split("/")
        if key.startswith("journals/") and len(parts) >= 3:
            prefix = "/".join(parts[:2])
            papers[prefix] += 1
            name = (el.findtext("journal") or "").strip()
            if name:
                names[prefix][name] += 1
            year_text = el.findtext("year") or ""
            if year_text.isdigit():
                year = int(year_text)
                lo, hi = years.get(prefix, (year, year))
                years[prefix] = [min(lo, year), max(hi, year)]
            found, dois = set(), set()
            for ee in el.findall("ee"):
                url = ee.text or ""
                found |= link_issns(url)
                dois.update(DOI_PREFIX.findall(url))
            for i in found:
                issns[prefix][i] += 1
            for p in dois:
                prefixes[prefix][p] += 1
        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  |  articles scanned: {seen:,}  |  journal keys: {len(papers):,}", end="\r", flush=True)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    f.close()
    print(f"  100%  |  articles scanned: {seen:,}  |  journal keys: {len(papers):,}")
    journals = [(k, n, *years.get(k, (None, None))) for k, n in papers.items()]
    name_rows = [(k, v, n) for k in papers for v, n in names[k].items()]
    issn_rows = [(k, v, n) for k in papers for v, n in issns[k].items()]
    prefix_rows = [(k, v, n) for k in papers for v, n in prefixes[k].items()]
    return journals, name_rows, issn_rows, prefix_rows


def load_dblp(conn: sqlite3.Connection, xml_path: Path) -> None:
    print("━" * 70)
    print("  Phase 2 – DBLP evidence → journal_dblp_venue, journal_dblp_name, journal_dblp_issn, journal_dblp_doi_prefix")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB, ~15–20 min)\n")
    journals, name_rows, issn_rows, prefix_rows = scan_xml(xml_path)
    with conn:
        conn.executemany(
            "INSERT INTO journal_dblp_venue (dblp_key, papers, first_year, last_year) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (dblp_key) DO UPDATE SET papers = excluded.papers, "
            "first_year = excluded.first_year, last_year = excluded.last_year",
            journals,
        )
        conn.execute("CREATE TEMP TABLE scanned (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO scanned VALUES (?)", [(j[0],) for j in journals])
        removed = conn.execute(
            "DELETE FROM journal_dblp_venue WHERE dblp_key NOT IN (SELECT dblp_key FROM scanned)"
        ).rowcount
        conn.execute("DROP TABLE scanned")
        for table in ("journal_dblp_name", "journal_dblp_issn", "journal_dblp_doi_prefix"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO journal_dblp_name VALUES (?, ?, ?)", name_rows)
        conn.executemany("INSERT INTO journal_dblp_issn VALUES (?, ?, ?)", issn_rows)
        conn.executemany("INSERT INTO journal_dblp_doi_prefix VALUES (?, ?, ?)", prefix_rows)
    with_issn = len({r[0] for r in issn_rows})
    print(
        f"\n  ✓  {len(journals):,} keys  |  {len(name_rows):,} names  |  {with_issn:,} keys with ISSNs"
        f"  |  {len(prefix_rows):,} DOI prefixes  |  {removed} keys removed\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", default=str(XML_PATH), help=f"Path to paper-dblp.xml  (default: {XML_PATH.name})")
    ap.add_argument("--sjr", default=str(SJR_SRC), help=f"Path to Scopus CSV  (default: {SJR_SRC.name})")
    ap.add_argument("--skip-xml", action="store_true", help="Keep the DBLP evidence already in the database")
    args = ap.parse_args()
    xml_path, sjr_path = Path(args.xml), Path(args.sjr)
    if not sjr_path.exists():
        sys.exit(f"Scopus CSV not found: {sjr_path}")
    if not args.skip_xml and not xml_path.exists():
        sys.exit(f"paper-dblp.xml not found: {xml_path}\nRun the downloader first.")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Journal Evidence Loader                      ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    load_sjr(conn, sjr_path)
    if args.skip_xml:
        n = conn.execute("SELECT count(*) FROM journal_dblp_venue").fetchone()[0]
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
