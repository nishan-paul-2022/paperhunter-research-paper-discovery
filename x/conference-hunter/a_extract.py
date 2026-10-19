#!/usr/bin/env python3
"""Stage 1: load CORE rankings and DBLP conference evidence into conference.db.

Creates the schema on first run. Reruns refresh the evidence in place and keep
every decision whose CORE venue and DBLP key still exist.
"""
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

SCHEMA = """

-- CORE ranking, imported from publication-core-2026.csv (A*–C only).
CREATE TABLE IF NOT EXISTS core_venue (
    id      INTEGER PRIMARY KEY,
    title   TEXT NOT NULL UNIQUE,
    acronym TEXT NOT NULL,
    rank    TEXT NOT NULL CHECK (rank IN ('A*', 'A', 'B', 'C'))
);

-- One row per DBLP conference key (conf/<name>) that has inproceedings.
CREATE TABLE IF NOT EXISTS dblp_venue (
    dblp_key   TEXT PRIMARY KEY CHECK (dblp_key LIKE 'conf/%'),
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER
) WITHOUT ROWID;

-- How often each booktitle occurs among a key's inproceedings.
CREATE TABLE IF NOT EXISTS dblp_booktitle (
    dblp_key  TEXT NOT NULL REFERENCES dblp_venue ON DELETE CASCADE,
    booktitle TEXT NOT NULL,
    papers    INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, booktitle)
) WITHOUT ROWID;

-- Full titles of a key's proceedings volumes.
CREATE TABLE IF NOT EXISTS dblp_proceedings (
    dblp_key TEXT NOT NULL REFERENCES dblp_venue ON DELETE CASCADE,
    year     INTEGER NOT NULL,
    title    TEXT NOT NULL,
    PRIMARY KEY (dblp_key, year, title)
) WITHOUT ROWID;

-- Pairs proposed by b_match.py (acronym AND proceedings-title agreement). Not approved.
CREATE TABLE IF NOT EXISTS conf_candidate (
    dblp_key          TEXT NOT NULL REFERENCES dblp_venue ON DELETE CASCADE,
    core_id           INTEGER NOT NULL REFERENCES core_venue ON DELETE CASCADE,
    score             REAL NOT NULL,
    proceedings_title TEXT NOT NULL,
    PRIMARY KEY (dblp_key, core_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_candidate_core ON conf_candidate (core_id);

-- Written only by the AI reviewer (run/b-conference-matching-guide.md).
CREATE TABLE IF NOT EXISTS conf_decision (
    id         INTEGER PRIMARY KEY,
    core_id    INTEGER NOT NULL REFERENCES core_venue ON DELETE CASCADE,
    dblp_key   TEXT REFERENCES dblp_venue ON DELETE CASCADE,
    verdict    TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'absent')),
    reason     TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'absent') = (dblp_key IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS conf_decision_pair ON conf_decision (dblp_key, core_id) WHERE dblp_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS conf_decision_one_venue_per_key ON conf_decision (dblp_key) WHERE verdict = 'accept';
CREATE UNIQUE INDEX IF NOT EXISTS conf_decision_one_absent ON conf_decision (core_id) WHERE verdict = 'absent';
CREATE INDEX IF NOT EXISTS conf_decision_core ON conf_decision (core_id);

CREATE TRIGGER IF NOT EXISTS conf_decision_accept_vs_absent_ins BEFORE INSERT ON conf_decision
WHEN EXISTS (
    SELECT 1 FROM conf_decision d WHERE d.core_id = NEW.core_id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'CORE venue cannot be both accepted and absent'); END;

CREATE TRIGGER IF NOT EXISTS conf_decision_accept_vs_absent_upd BEFORE UPDATE ON conf_decision
WHEN EXISTS (
    SELECT 1 FROM conf_decision d WHERE d.core_id = NEW.core_id AND d.id <> NEW.id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'CORE venue cannot be both accepted and absent'); END;

-- One readable evidence row per DBLP key: top 5 booktitles, latest 3 proceedings titles.
DROP VIEW IF EXISTS venue_evidence;
CREATE VIEW venue_evidence AS
SELECT v.dblp_key, upper(substr(v.dblp_key, 6)) AS acronym, v.papers, v.first_year, v.last_year,
       (SELECT group_concat(b.booktitle || ' (' || b.papers || ')', ' | ')
          FROM (SELECT booktitle, papers FROM dblp_booktitle WHERE dblp_key = v.dblp_key
                ORDER BY papers DESC, booktitle LIMIT 5) b) AS booktitles,
       (SELECT group_concat(p.title, ' | ')
          FROM (SELECT title FROM dblp_proceedings WHERE dblp_key = v.dblp_key
                ORDER BY year DESC, title DESC LIMIT 3) p) AS proceedings_titles
FROM dblp_venue v;

-- Everything the AI must resolve. Empty = review complete.
DROP VIEW IF EXISTS conf_needs_review;
CREATE VIEW conf_needs_review AS
SELECT 'UNREVIEWED' AS issue, c.dblp_key, c.core_id, v.title AS core_title, v.acronym, v.rank,
       c.score, c.proceedings_title AS detail,
       e.papers, e.first_year, e.last_year, e.booktitles, e.proceedings_titles
FROM conf_candidate c
JOIN core_venue v ON v.id = c.core_id
JOIN venue_evidence e ON e.dblp_key = c.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM conf_decision d WHERE d.dblp_key = c.dblp_key AND d.core_id = c.core_id)
UNION ALL
SELECT 'UNCOVERED', NULL, v.id, v.title, v.acronym, v.rank,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM core_venue v
WHERE NOT EXISTS (SELECT 1 FROM conf_decision d WHERE d.core_id = v.id AND d.verdict IN ('accept', 'absent'))
UNION ALL
SELECT 'INVALID', d.dblp_key, d.core_id, NULL, NULL, NULL,
       NULL, 'conf_decision ' || d.id || ' refers to a missing DBLP key or CORE venue: delete it',
       NULL, NULL, NULL, NULL, NULL
FROM conf_decision d
WHERE NOT EXISTS (SELECT 1 FROM core_venue v WHERE v.id = d.core_id)
   OR (d.dblp_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dblp_venue k WHERE k.dblp_key = d.dblp_key));

-- Final output. Stays empty until conf_needs_review is empty.
DROP VIEW IF EXISTS list_of_conferences;
CREATE VIEW list_of_conferences AS
SELECT d.dblp_key, upper(substr(d.dblp_key, 6)) AS dblp_acronym, v.title, v.rank AS ranking
FROM conf_decision d
JOIN core_venue v ON v.id = d.core_id
JOIN dblp_venue k ON k.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM conf_needs_review)
ORDER BY CASE v.rank WHEN 'A*' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
         lower(v.title), d.dblp_key;
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
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
