#!/usr/bin/env python3
import argparse
import sqlite3
import sys
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract papers from DBLP into paper_dblp.")
    parser.add_argument("--schema-only", action="store_true", help="create the schema and stop")
    args = parser.parse_args()
    if not args.schema_only:
        parser.error("extraction is not implemented yet; run with --schema-only")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Paper Extractor                              ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    prepare_schema(conn)
    conn.close()


if __name__ == "__main__":
    main()
