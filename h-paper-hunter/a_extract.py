#!/usr/bin/env python3
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
XML_PATH = REPO_ROOT / "e-download" / "dblp.xml"
DB_PATH = REPO_ROOT / "a-database" / "paperhunter.db"
BATCH_SIZE = 5_000
PAPER_TAGS = ("article", "inproceedings")
RECORD_TAGS = PAPER_TAGS + ("proceedings", "book", "incollection", "phdthesis", "mastersthesis",
                            "www", "person", "data")
DOI_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\S+)$", re.I)
COLUMNS = ("dblp_key", "venue_key", "record_type", "title", "authors", "year", "venue", "doi", "ee")
UPSERT = (
    f"INSERT INTO paper_dblp ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
    f"ON CONFLICT (dblp_key) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in COLUMNS[1:])} "
    f"WHERE ({', '.join(f'paper_dblp.{c}' for c in COLUMNS[1:])}) "
    f"IS NOT ({', '.join(f'excluded.{c}' for c in COLUMNS[1:])})"
)
NEW_PAPER_ROWS = [
    f"INSERT OR IGNORE INTO {table} (paper_id) SELECT paper_id FROM paper_dblp WHERE dblp_key = ?"
    for table in ("paper_status", "paper_field_arxiv", "paper_field_abstract")
]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run d-context/b_schema.py first)")
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'paper_status'").fetchone()[0] == 0:
        sys.exit("paper_status not found (run d-context/b_schema.py first)")
    return conn


def venue_keys(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    conf = {k for (k,) in conn.execute("SELECT dblp_key FROM conf_list")}
    journal = {k for (k,) in conn.execute("SELECT dblp_key FROM journal_list")}
    if not conf or not journal:
        sys.exit("conf_list or journal_list is empty (pending venue review?). Finish it before extracting papers.")
    return conf, journal


def issue_label(number: str) -> str:
    label = re.sub(r"[\s_.-]*\d+$", "", " ".join(number.split()))
    return label if re.search(r"[A-Za-z]", label) else ""


def match_venue(el, keys: set[str]) -> str | None:
    parts = el.get("key", "").split("/")
    if len(parts) < 3:
        return None
    prefix = "/".join(parts[:2])
    if el.tag == "article" and parts[0] == "journals":
        label = issue_label(el.findtext("number") or "")
        if label and f"{prefix}#{label}" in keys:
            return f"{prefix}#{label}"
    return prefix if prefix in keys else None


def text(el, tag: str) -> str | None:
    child = el.find(tag)
    if child is None:
        return None
    value = " ".join("".join(child.itertext()).split())
    return value or None


def doi_from_ee(ee: list[str]) -> str | None:
    for link in ee:
        m = DOI_RE.match(link)
        if m:
            return urllib.parse.unquote(m.group(1)).lower()
    return None


def record_row(el, venue_key: str, year: int) -> tuple:
    ee = [v for v in ("".join(e.itertext()).strip() for e in el.findall("ee")) if v]
    authors = [" ".join("".join(a.itertext()).split()) for a in el.findall("author")]
    venue = text(el, "booktitle" if el.tag == "inproceedings" else "journal")
    return (el.get("key"), venue_key, el.tag, text(el, "title"), json.dumps(authors, ensure_ascii=False), year,
            venue, doi_from_ee(ee), json.dumps(ee, ensure_ascii=False))


def write_batch(conn: sqlite3.Connection, rows: list[tuple]) -> tuple[int, int]:
    with conn:
        before = conn.total_changes
        conn.executemany(UPSERT, rows)
        changed = conn.total_changes - before
        before = conn.total_changes
        conn.executemany(NEW_PAPER_ROWS[0], [(r[0],) for r in rows])
        new = conn.total_changes - before
        for sql in NEW_PAPER_ROWS[1:]:
            conn.executemany(sql, [(r[0],) for r in rows])
    return new, changed - new


def extract(conn: sqlite3.Connection, xml_path: Path, from_year: int, to_year: int) -> None:
    from lxml import etree

    conf, journal = venue_keys(conn)
    keys = conf | journal
    print(f"  {len(conf):,} conference keys + {len(journal):,} journal keys  |  "
          f"scanning {xml_path.name} ({xml_path.stat().st_size // 1_000_000:,} MB)\n")
    size = os.path.getsize(xml_path)
    started = time.time()
    seen = kept = inserted = updated = 0
    skipped: Counter = Counter()
    rows: list[tuple] = []
    with open(xml_path, "rb") as f:
        for _, el in etree.iterparse(f, events=("end",), tag=RECORD_TAGS,
                                     load_dtd=True, resolve_entities=True, huge_tree=True):
            seen += 1
            year_text = (el.findtext("year") or "").strip()
            venue_key = match_venue(el, keys) if el.tag in PAPER_TAGS else None
            if el.tag not in PAPER_TAGS:
                skipped["type"] += 1
            elif venue_key is None:
                skipped["venue"] += 1
            elif not year_text.isdigit() or not from_year <= int(year_text) <= to_year:
                skipped["year"] += 1
            elif el.get("publtype") == "withdrawn":
                skipped["withdrawn"] += 1
            else:
                rows.append(record_row(el, venue_key, int(year_text)))
                kept += 1
                if len(rows) >= BATCH_SIZE:
                    new, changed = write_batch(conn, rows)
                    inserted, updated, rows = inserted + new, updated + changed, []
            if seen % 500_000 == 0:
                print(f"  {f.tell() * 100 // size:3}%  |  records scanned: {seen:,}  |  kept: {kept:,}  |  "
                      f"{time.time() - started:,.0f}s", end="\r", flush=True)
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]
    if rows:
        new, changed = write_batch(conn, rows)
        inserted, updated = inserted + new, updated + changed
    print(f"  100%  |  records scanned: {seen:,}  |  kept: {kept:,}  |  {time.time() - started:,.0f}s")
    print(f"\n  ✓  {inserted:,} new (status A)  |  {updated:,} updated  |  {kept - inserted - updated:,} unchanged  |  "
          f"skipped: {skipped['type']:,} not article/inproceedings, {skipped['venue']:,} unlisted venue, "
          f"{skipped['year']:,} out of range, {skipped['withdrawn']:,} withdrawn\n")
    report(conn, conf, from_year, to_year)


def report(conn: sqlite3.Connection, conf: set[str], from_year: int, to_year: int) -> None:
    counts: Counter = Counter()
    no_doi = 0
    for venue_key, year, doi in conn.execute(
            "SELECT venue_key, year, doi FROM paper_dblp WHERE year BETWEEN ? AND ?", (from_year, to_year)):
        counts[year, "conference" if venue_key in conf else "journal"] += 1
        no_doi += doi is None
    print(f"  {'year':>6}  {'conference':>12}  {'journal':>12}  {'total':>12}")
    for year in range(from_year, to_year + 1):
        c, j = counts[year, "conference"], counts[year, "journal"]
        print(f"  {year:>6}  {c:>12,}  {j:>12,}  {c + j:>12,}")
    c = sum(n for (_, t), n in counts.items() if t == "conference")
    j = sum(n for (_, t), n in counts.items() if t == "journal")
    print(f"  {'total':>6}  {c:>12,}  {j:>12,}  {c + j:>12,}")
    print(f"\n  Papers without a DOI: {no_doi:,} ({no_doi * 100 / max(c + j, 1):.1f}%)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract papers from DBLP into paper_dblp.")
    parser.add_argument("--from-year", type=int, required=True, help="first publication year to keep")
    parser.add_argument("--to-year", type=int, required=True, help="last publication year to keep")
    args = parser.parse_args()
    if args.from_year > args.to_year:
        parser.error("--from-year must not be after --to-year")
    if not XML_PATH.exists():
        sys.exit(f"dblp.xml not found: {XML_PATH}")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  A · Paper Extractor                          ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    extract(conn, XML_PATH, args.from_year, args.to_year)
    conn.close()
    print("━" * 70)
    print("  Done.  → Run b_arxiv.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
