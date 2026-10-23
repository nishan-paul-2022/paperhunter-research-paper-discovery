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

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
XML_PATH = REPO_ROOT / "archive" / "dblp.xml"
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
BATCH_SIZE = 5_000
PAPER_TAGS = ("article", "inproceedings")
# Every child type of <dblp> in dblp.dtd. All are cleared after parsing, or long runs of
# non-paper records (e.g. www person pages) pile up in the tree until the next paper.
RECORD_TAGS = PAPER_TAGS + ("proceedings", "book", "incollection", "phdthesis", "mastersthesis",
                            "www", "person", "data")
DOI_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\S+)$", re.I)
ARXIV_RE = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([a-z.-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?$", re.I)
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+?)(?:v\d+)?$", re.I)
COLUMNS = ("dblp_key", "venue_key", "record_type", "title", "authors", "year", "venue", "volume", "number",
           "pages", "doi", "arxiv_id", "ee", "url", "crossref", "mdate")
UPSERT = (
    f"INSERT INTO paper_dblp ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
    f"ON CONFLICT (dblp_key) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in COLUMNS[1:])} "
    f"WHERE ({', '.join(f'paper_dblp.{c}' for c in COLUMNS[1:])}) "
    f"IS NOT ({', '.join(f'excluded.{c}' for c in COLUMNS[1:])})"
)

# Old Paper Hunter objects, children before parents so foreign keys never block a drop.
OLD_OBJECTS = (
    "paper_review", "paper_list", "paper_summary",
    "paper_candidate_ai", "paper_openalex", "paper_s2", "paper_dblp",
)
# Exists only in the new schema; its presence means the old objects are already gone.
NEW_SCHEMA_MARKER = "paper_abstract_status"

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_dblp (
    paper_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    dblp_key    TEXT NOT NULL UNIQUE,
    venue_key   TEXT NOT NULL,
    record_type TEXT NOT NULL CHECK (record_type IN ('article', 'inproceedings')),
    title       TEXT,
    authors     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(authors) AND json_type(authors) = 'array'),
    year        INTEGER NOT NULL,
    venue       TEXT,
    volume      TEXT,
    number      TEXT,
    pages       TEXT,
    doi         TEXT,
    arxiv_id    TEXT,
    ee          TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(ee) AND json_type(ee) = 'array'),
    url         TEXT,
    crossref    TEXT,
    mdate       TEXT
);
CREATE INDEX IF NOT EXISTS paper_dblp_venue_key ON paper_dblp (venue_key);
CREATE INDEX IF NOT EXISTS paper_dblp_year ON paper_dblp (year);
CREATE INDEX IF NOT EXISTS paper_dblp_doi ON paper_dblp (doi);
CREATE INDEX IF NOT EXISTS paper_dblp_arxiv_id ON paper_dblp (arxiv_id);

CREATE TABLE IF NOT EXISTS paper_abstract_status (
    paper_id     INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    has_abstract INTEGER NOT NULL DEFAULT 0 CHECK (has_abstract IN (0, 1)),
    hit_count    INTEGER NOT NULL DEFAULT 0 CHECK (hit_count >= 0),
    miss_count   INTEGER NOT NULL DEFAULT 0 CHECK (miss_count >= 0)
);
CREATE INDEX IF NOT EXISTS paper_abstract_status_has_abstract ON paper_abstract_status (has_abstract);

CREATE TABLE IF NOT EXISTS paper_abstract (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    abstract TEXT NOT NULL CHECK (length(trim(abstract)) > 0),
    source   TEXT NOT NULL CHECK (length(trim(source)) > 0)
);

CREATE TABLE IF NOT EXISTS paper_abstract_hit (
    paper_id  INTEGER NOT NULL REFERENCES paper_dblp (paper_id),
    source    TEXT NOT NULL CHECK (length(trim(source)) > 0),
    lookup_by TEXT NOT NULL CHECK (lookup_by IN ('doi', 'arxiv', 'title', 'web')),
    request   TEXT NOT NULL,
    response  TEXT NOT NULL DEFAULT '',
    called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_abstract_hit_lookup ON paper_abstract_hit (paper_id, source, lookup_by);

CREATE TABLE IF NOT EXISTS paper_abstract_miss (
    paper_id  INTEGER NOT NULL REFERENCES paper_dblp (paper_id),
    source    TEXT NOT NULL CHECK (length(trim(source)) > 0),
    lookup_by TEXT NOT NULL CHECK (lookup_by IN ('doi', 'arxiv', 'title', 'web')),
    request   TEXT NOT NULL,
    response  TEXT NOT NULL DEFAULT '',
    called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_abstract_miss_lookup ON paper_abstract_miss (paper_id, source, lookup_by);

CREATE TABLE IF NOT EXISTS paper_seed (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    category TEXT NOT NULL CHECK (category IN ('baseline', 'benchmark', 'survey', 'evaluation')),
    evidence TEXT NOT NULL CHECK (length(trim(evidence)) > 0)
);

CREATE TABLE IF NOT EXISTS paper_screen (
    paper_id    INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    embed_basis TEXT NOT NULL CHECK (embed_basis IN ('abstract', 'title')),
    embed_hash  TEXT NOT NULL,
    similarity  REAL,
    passed_by   TEXT CHECK (passed_by IN ('semantic', 'keyword', 'both')),
    screened    INTEGER NOT NULL DEFAULT 0 CHECK (screened IN (0, 1)),
    passed      INTEGER CHECK (passed IN (0, 1))
);
CREATE INDEX IF NOT EXISTS paper_screen_screened ON paper_screen (screened);
CREATE INDEX IF NOT EXISTS paper_screen_passed ON paper_screen (passed);

CREATE TABLE IF NOT EXISTS paper_verdict_with_abstract (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    reviewed INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    verdict  TEXT CHECK (verdict IN ('baseline', 'benchmark', 'survey', 'evaluation', 'reject')),
    reason   TEXT
);
CREATE INDEX IF NOT EXISTS paper_verdict_with_abstract_reviewed ON paper_verdict_with_abstract (reviewed);

CREATE TABLE IF NOT EXISTS paper_verdict_without_abstract (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    reviewed INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    verdict  TEXT CHECK (verdict IN ('baseline', 'benchmark', 'survey', 'evaluation', 'reject')),
    reason   TEXT
);
CREATE INDEX IF NOT EXISTS paper_verdict_without_abstract_reviewed ON paper_verdict_without_abstract (reviewed);

CREATE VIEW IF NOT EXISTS paper_lookup_queue AS
SELECT s.paper_id, sc.similarity, s.miss_count
FROM paper_abstract_status s
JOIN paper_screen sc ON sc.paper_id = s.paper_id
WHERE s.has_abstract = 0 AND sc.passed = 1
ORDER BY sc.similarity DESC, s.miss_count, s.paper_id;

CREATE VIEW IF NOT EXISTS paper_list AS
SELECT p.*,
       v.verdict AS category,
       v.reason,
       v.decided_from,
       CASE WHEN c.dblp_key IS NOT NULL THEN c.core_title ELSE j.scopus_title END AS venue_name,
       CASE WHEN c.dblp_key IS NOT NULL THEN c.ranking ELSE j.quartile END AS venue_rank
FROM (SELECT paper_id, reviewed, verdict, reason, 'abstract' AS decided_from FROM paper_verdict_with_abstract
      UNION ALL
      SELECT paper_id, reviewed, verdict, reason, 'metadata' AS decided_from FROM paper_verdict_without_abstract) v
JOIN paper_dblp p ON p.paper_id = v.paper_id
LEFT JOIN conf_list c ON c.dblp_key = p.venue_key
LEFT JOIN journal_list j ON j.dblp_key = p.venue_key
WHERE v.reviewed = 1 AND v.verdict IS NOT NULL AND v.verdict <> 'reject';
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def object_type(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def prepare_schema(conn: sqlite3.Connection) -> None:
    print("━" * 70)
    print("  Phase 1 – Schema")
    print("━" * 70)
    for view in ("conf_list", "journal_list"):
        if conn.execute(f"SELECT count(*) FROM {view}").fetchone()[0] == 0:
            sys.exit(f"{view} is empty (pending venue review?). Finish it before extracting papers.")
    statements = []
    if object_type(conn, NEW_SCHEMA_MARKER) is None:
        for name in OLD_OBJECTS:
            kind = object_type(conn, name)
            if kind in ("table", "view"):
                statements.append(f"DROP {kind.upper()} {name}")
    dropped = len(statements)
    statements += [s.strip() for s in SCHEMA.split(";") if s.strip()]
    try:
        conn.execute("BEGIN")
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    tables = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'paper%'").fetchone()[0]
    views = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'view' AND name LIKE 'paper%'").fetchone()[0]
    print(f"  ✓  {dropped} old objects dropped  |  {tables} paper tables, {views} paper views present\n")


def venue_keys(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    conf = {k for (k,) in conn.execute("SELECT dblp_key FROM conf_list")}
    journal = {k for (k,) in conn.execute("SELECT dblp_key FROM journal_list")}
    return conf, journal


# Same rule as conference-hunter: the issue label of a journal stream key journals/<name>#<label>.
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


def ids_from_ee(ee: list[str]) -> tuple[str | None, str | None]:
    doi = arxiv_id = None
    for link in ee:
        m = DOI_RE.match(link)
        if m:
            value = urllib.parse.unquote(m.group(1)).lower()
            a = ARXIV_DOI_RE.match(value)
            if a:
                arxiv_id = arxiv_id or a.group(1)
            doi = doi or value
            continue
        m = ARXIV_RE.match(link)
        if m:
            arxiv_id = arxiv_id or m.group(1).lower()
    return doi, arxiv_id


def record_row(el, venue_key: str, year: int) -> tuple:
    ee = [v for v in ("".join(e.itertext()).strip() for e in el.findall("ee")) if v]
    doi, arxiv_id = ids_from_ee(ee)
    authors = [" ".join("".join(a.itertext()).split()) for a in el.findall("author")]
    venue = text(el, "booktitle" if el.tag == "inproceedings" else "journal")
    return (el.get("key"), venue_key, el.tag, text(el, "title"), json.dumps(authors, ensure_ascii=False), year,
            venue, text(el, "volume"), text(el, "number"), text(el, "pages"), doi, arxiv_id,
            json.dumps(ee, ensure_ascii=False), text(el, "url"), text(el, "crossref"), el.get("mdate"))


def write_batch(conn: sqlite3.Connection, rows: list[tuple]) -> tuple[int, int]:
    with conn:
        before = conn.total_changes
        conn.executemany(UPSERT, rows)
        changed = conn.total_changes - before
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO paper_abstract_status (paper_id) SELECT paper_id FROM paper_dblp WHERE dblp_key = ?",
            [(r[0],) for r in rows],
        )
        new = conn.total_changes - before
    return new, changed - new


def extract(conn: sqlite3.Connection, xml_path: Path, from_year: int, to_year: int) -> None:
    from lxml import etree

    print("━" * 70)
    print(f"  Phase 2 – DBLP papers {from_year}–{to_year} → paper_dblp, paper_abstract_status")
    print("━" * 70)
    conf, journal = venue_keys(conn)
    keys = conf | journal
    print(f"  {len(conf):,} conference keys + {len(journal):,} journal keys  |  "
          f"scanning {xml_path.name} ({xml_path.stat().st_size // 1_000_000:,} MB)\n")
    size = os.path.getsize(xml_path)
    started = time.time()
    seen = 0
    skipped: Counter = Counter()
    kept = inserted = updated = 0
    rows: list[tuple] = []
    with open(xml_path, "rb") as f:
        ctx = etree.iterparse(f, events=("end",), tag=RECORD_TAGS,
                              load_dtd=True, resolve_entities=True, huge_tree=True)
        for _, el in ctx:
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
    print(f"\n  ✓  {inserted:,} inserted  |  {updated:,} updated  |  {kept - inserted - updated:,} unchanged  |  "
          f"skipped: {skipped['type']:,} not article/inproceedings, "
          f"{skipped['venue']:,} unlisted venue, {skipped['year']:,} out of range, "
          f"{skipped['withdrawn']:,} withdrawn\n")
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
    parser.add_argument("--from-year", type=int, help="first publication year to keep (required)")
    parser.add_argument("--to-year", type=int, help="last publication year to keep (required)")
    parser.add_argument("--schema-only", action="store_true", help="create the schema and stop")
    args = parser.parse_args()
    if not args.schema_only:
        if args.from_year is None or args.to_year is None:
            parser.error("the following arguments are required: --from-year, --to-year")
        if args.from_year > args.to_year:
            parser.error("--from-year must not be after --to-year")
        if not XML_PATH.exists():
            sys.exit(f"dblp.xml not found: {XML_PATH}")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Paper Extractor                              ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    prepare_schema(conn)
    if not args.schema_only:
        extract(conn, XML_PATH, args.from_year, args.to_year)
    conn.close()
    print("━" * 70)
    print("  Done.  → Run b_fetch.py next" if not args.schema_only else "  Done (schema only).")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
