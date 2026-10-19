#!/usr/bin/env python3
"""Stage 1: load Scopus (SJR) rankings and DBLP journal evidence into journal.db.

Creates the schema on first run. Reruns refresh the evidence in place and keep
every decision whose SJR journal and DBLP key still exist.
"""
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

SCHEMA = """
PRAGMA journal_mode = DELETE;

-- Scopus/SJR ranking, imported from publication-scopus-computer-science-journal-2025.csv (Q1–Q4 only).
CREATE TABLE IF NOT EXISTS sjr_journal (
    id        INTEGER PRIMARY KEY,            -- Scopus Sourceid, stable across editions
    title     TEXT NOT NULL,
    issn_1    TEXT,
    issn_2    TEXT,
    publisher TEXT,
    quartile  TEXT NOT NULL CHECK (quartile IN ('Q1', 'Q2', 'Q3', 'Q4'))
);

-- One row per DBLP journal key (journals/<name>) that has articles.
CREATE TABLE IF NOT EXISTS dblp_journal (
    dblp_key   TEXT PRIMARY KEY CHECK (dblp_key LIKE 'journals/%'),
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER
) WITHOUT ROWID;

-- How often each <journal> name occurs among a key's articles.
CREATE TABLE IF NOT EXISTS dblp_journal_name (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    name     TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, name)
) WITHOUT ROWID;

-- Checksum-valid ISSNs found in a key's article links (DOIs, Elsevier PIIs), the strongest evidence.
CREATE TABLE IF NOT EXISTS dblp_issn (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    issn     TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, issn)
) WITHOUT ROWID;

-- DOI prefixes of a key's articles (10.1145 = ACM, 10.1109 = IEEE, 10.1016 = Elsevier, 10.1007 = Springer, ...).
CREATE TABLE IF NOT EXISTS dblp_doi_prefix (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    prefix   TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, prefix)
) WITHOUT ROWID;

-- Pairs proposed by b_match.py (shared ISSN, or abbreviation-aware name agreement). Not approved.
CREATE TABLE IF NOT EXISTS journal_candidate (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    sjr_id   INTEGER NOT NULL REFERENCES sjr_journal ON DELETE CASCADE,
    method   TEXT NOT NULL CHECK (method IN ('issn', 'name')),
    score    REAL NOT NULL,
    detail   TEXT NOT NULL,
    PRIMARY KEY (dblp_key, sjr_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_candidate_sjr ON journal_candidate (sjr_id);

-- Written only by the AI reviewer (run/c-journal-matching-guide.md).
CREATE TABLE IF NOT EXISTS journal_decision (
    id         INTEGER PRIMARY KEY,
    sjr_id     INTEGER NOT NULL REFERENCES sjr_journal ON DELETE CASCADE,
    dblp_key   TEXT REFERENCES dblp_journal ON DELETE CASCADE,
    verdict    TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'absent')),
    reason     TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'absent') = (dblp_key IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS journal_decision_pair ON journal_decision (dblp_key, sjr_id) WHERE dblp_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS journal_decision_one_journal_per_key ON journal_decision (dblp_key) WHERE verdict = 'accept';
CREATE UNIQUE INDEX IF NOT EXISTS journal_decision_one_absent ON journal_decision (sjr_id) WHERE verdict = 'absent';
CREATE INDEX IF NOT EXISTS journal_decision_sjr ON journal_decision (sjr_id);

CREATE TRIGGER IF NOT EXISTS journal_decision_accept_vs_absent_ins BEFORE INSERT ON journal_decision
WHEN EXISTS (
    SELECT 1 FROM journal_decision d WHERE d.sjr_id = NEW.sjr_id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'SJR journal cannot be both accepted and absent'); END;

CREATE TRIGGER IF NOT EXISTS journal_decision_accept_vs_absent_upd BEFORE UPDATE ON journal_decision
WHEN EXISTS (
    SELECT 1 FROM journal_decision d WHERE d.sjr_id = NEW.sjr_id AND d.id <> NEW.id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'SJR journal cannot be both accepted and absent'); END;

-- One readable evidence row per DBLP key: top names, ISSNs and DOI prefixes.
DROP VIEW IF EXISTS journal_evidence;
CREATE VIEW journal_evidence AS
SELECT j.dblp_key, j.papers, j.first_year, j.last_year,
       (SELECT group_concat(n.name || ' (' || n.papers || ')', ' | ')
          FROM (SELECT name, papers FROM dblp_journal_name WHERE dblp_key = j.dblp_key
                ORDER BY papers DESC, name LIMIT 3) n) AS names,
       (SELECT group_concat(i.issn || ' (' || i.papers || ')', ' | ')
          FROM (SELECT issn, papers FROM dblp_issn WHERE dblp_key = j.dblp_key
                ORDER BY papers DESC, issn LIMIT 4) i) AS issns,
       (SELECT group_concat(p.prefix || ' (' || p.papers || ')', ' | ')
          FROM (SELECT prefix, papers FROM dblp_doi_prefix WHERE dblp_key = j.dblp_key
                ORDER BY papers DESC, prefix LIMIT 3) p) AS doi_prefixes
FROM dblp_journal j;

-- Everything the AI must resolve. Empty = review complete.
DROP VIEW IF EXISTS journal_needs_review;
CREATE VIEW journal_needs_review AS
SELECT 'UNREVIEWED' AS issue, c.dblp_key, c.sjr_id, s.title AS sjr_title, s.issn_1, s.issn_2,
       s.publisher, s.quartile, c.method, c.score, c.detail,
       e.papers, e.first_year, e.last_year, e.names, e.issns, e.doi_prefixes
FROM journal_candidate c
JOIN sjr_journal s ON s.id = c.sjr_id
JOIN journal_evidence e ON e.dblp_key = c.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM journal_decision d WHERE d.dblp_key = c.dblp_key AND d.sjr_id = c.sjr_id)
UNION ALL
SELECT 'UNCOVERED', NULL, s.id, s.title, s.issn_1, s.issn_2, s.publisher, s.quartile,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM sjr_journal s
WHERE NOT EXISTS (SELECT 1 FROM journal_decision d WHERE d.sjr_id = s.id AND d.verdict IN ('accept', 'absent'))
UNION ALL
SELECT 'INVALID', d.dblp_key, d.sjr_id, NULL, NULL, NULL, NULL, NULL,
       NULL, NULL, 'journal_decision ' || d.id || ' refers to a missing DBLP key or SJR journal: delete it',
       NULL, NULL, NULL, NULL, NULL, NULL
FROM journal_decision d
WHERE NOT EXISTS (SELECT 1 FROM sjr_journal s WHERE s.id = d.sjr_id)
   OR (d.dblp_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dblp_journal k WHERE k.dblp_key = d.dblp_key));

-- Final output. Stays empty until journal_needs_review is empty.
DROP VIEW IF EXISTS list_of_journals;
CREATE VIEW list_of_journals AS
SELECT d.dblp_key,
       (SELECT name FROM dblp_journal_name n WHERE n.dblp_key = d.dblp_key
         ORDER BY papers DESC, name LIMIT 1) AS dblp_name,
       s.title, s.issn_1, s.issn_2, s.quartile
FROM journal_decision d
JOIN sjr_journal s ON s.id = d.sjr_id
JOIN dblp_journal k ON k.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM journal_needs_review)
ORDER BY s.quartile, lower(s.title), d.dblp_key;
"""

ISSN_HYPHEN = re.compile(r"(?<![\dA-Za-z])(\d{4})-(\d{3}[\dXx])(?![\dA-Za-z])")
ISSN_PII = re.compile(r"(?:pii/|10\.1016/)S(\d{4})-?(\d{3}[\dXx])", re.I)
DOI_PREFIX = re.compile(r"doi\.org/(10\.\d{4,9})/", re.I)


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
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
    print("  Phase 1 – Scopus/SJR rankings → sjr_journal")
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
            "INSERT INTO sjr_journal (id, title, issn_1, issn_2, publisher, quartile) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET title = excluded.title, issn_1 = excluded.issn_1, "
            "issn_2 = excluded.issn_2, publisher = excluded.publisher, quartile = excluded.quartile",
            rows,
        )
        conn.execute("CREATE TEMP TABLE sjr_ids (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO sjr_ids VALUES (?)", [(r[0],) for r in rows])
        removed = conn.execute("DELETE FROM sjr_journal WHERE id NOT IN (SELECT id FROM sjr_ids)").rowcount
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
    print("  Phase 2 – DBLP evidence → dblp_journal, dblp_journal_name, dblp_issn, dblp_doi_prefix")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB, ~15–20 min)\n")
    journals, name_rows, issn_rows, prefix_rows = scan_xml(xml_path)
    with conn:
        conn.executemany(
            "INSERT INTO dblp_journal (dblp_key, papers, first_year, last_year) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (dblp_key) DO UPDATE SET papers = excluded.papers, "
            "first_year = excluded.first_year, last_year = excluded.last_year",
            journals,
        )
        conn.execute("CREATE TEMP TABLE scanned (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO scanned VALUES (?)", [(j[0],) for j in journals])
        removed = conn.execute(
            "DELETE FROM dblp_journal WHERE dblp_key NOT IN (SELECT dblp_key FROM scanned)"
        ).rowcount
        conn.execute("DROP TABLE scanned")
        for table in ("dblp_journal_name", "dblp_issn", "dblp_doi_prefix"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO dblp_journal_name VALUES (?, ?, ?)", name_rows)
        conn.executemany("INSERT INTO dblp_issn VALUES (?, ?, ?)", issn_rows)
        conn.executemany("INSERT INTO dblp_doi_prefix VALUES (?, ?, ?)", prefix_rows)
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
        n = conn.execute("SELECT count(*) FROM dblp_journal").fetchone()[0]
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
