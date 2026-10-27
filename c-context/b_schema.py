#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "b-config" / "paperhunter.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conf_core (
    id           INTEGER PRIMARY KEY,
    core_title   TEXT NOT NULL UNIQUE,
    core_acronym TEXT NOT NULL,
    rank         TEXT NOT NULL CHECK (rank IN ('A*', 'A', 'B', 'C'))
);

CREATE TABLE IF NOT EXISTS conf_dblp_venue (
    dblp_key     TEXT PRIMARY KEY CHECK (dblp_key LIKE 'conf/%' OR dblp_key LIKE 'journals/%'),
    dblp_acronym TEXT NOT NULL,
    papers       INTEGER NOT NULL,
    first_year   INTEGER,
    last_year    INTEGER
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS conf_dblp_book_title (
    dblp_key   TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    book_title TEXT NOT NULL,
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER,
    PRIMARY KEY (dblp_key, book_title)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS conf_dblp_proceeding_title (
    dblp_key         TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    proceeding_key   TEXT PRIMARY KEY,
    year             INTEGER NOT NULL,
    proceeding_title TEXT NOT NULL,
    publisher        TEXT,
    papers           INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_dblp_proceeding_title_key ON conf_dblp_proceeding_title (dblp_key, year);

CREATE TABLE IF NOT EXISTS conf_dblp_lineage (
    predecessor_key TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    successor_key   TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    PRIMARY KEY (predecessor_key, successor_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_dblp_lineage_successor ON conf_dblp_lineage (successor_key);

CREATE TABLE IF NOT EXISTS conf_candidate_rule (
    dblp_key          TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    core_id           INTEGER NOT NULL REFERENCES conf_core ON DELETE CASCADE,
    method            TEXT NOT NULL CHECK (method <> '' AND trim(replace(replace(replace(replace(
                          method, 'acronym', ''), 'related', ''), 'lineage', ''), 'title', ''), ',') = ''),
    detail            TEXT NOT NULL,
    PRIMARY KEY (dblp_key, core_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_candidate_rule_core ON conf_candidate_rule (core_id);

CREATE TABLE IF NOT EXISTS conf_candidate_ai (
    id         INTEGER PRIMARY KEY,
    core_id    INTEGER NOT NULL REFERENCES conf_core ON DELETE CASCADE,
    dblp_key   TEXT REFERENCES conf_dblp_venue ON DELETE CASCADE,
    verdict    TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'absent')),
    reason     TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'absent') = (dblp_key IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS conf_candidate_ai_pair ON conf_candidate_ai (dblp_key, core_id) WHERE dblp_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS conf_candidate_ai_one_venue_per_key ON conf_candidate_ai (dblp_key) WHERE verdict = 'accept';
CREATE UNIQUE INDEX IF NOT EXISTS conf_candidate_ai_one_absent ON conf_candidate_ai (core_id) WHERE verdict = 'absent';
CREATE INDEX IF NOT EXISTS conf_candidate_ai_core ON conf_candidate_ai (core_id);

CREATE TRIGGER IF NOT EXISTS conf_candidate_ai_accept_vs_absent_ins BEFORE INSERT ON conf_candidate_ai
WHEN EXISTS (
    SELECT 1 FROM conf_candidate_ai d WHERE d.core_id = NEW.core_id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'CORE venue cannot be both accepted and absent'); END;

CREATE TRIGGER IF NOT EXISTS conf_candidate_ai_accept_vs_absent_upd BEFORE UPDATE ON conf_candidate_ai
WHEN EXISTS (
    SELECT 1 FROM conf_candidate_ai d WHERE d.core_id = NEW.core_id AND d.id <> NEW.id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'CORE venue cannot be both accepted and absent'); END;

DROP VIEW IF EXISTS conf_summary;
CREATE VIEW conf_summary AS
SELECT v.dblp_key, v.dblp_acronym, v.papers, v.first_year, v.last_year,
       CASE WHEN v.dblp_key LIKE 'conf/%'
            THEN (SELECT count(*) FROM conf_dblp_proceeding_title WHERE dblp_key = v.dblp_key) END AS volumes,
       (SELECT group_concat(p.publisher || ' (' || p.n || ')', ' | ')
          FROM (SELECT publisher, count(*) AS n FROM conf_dblp_proceeding_title
                WHERE dblp_key = v.dblp_key AND coalesce(publisher, '') <> ''
                GROUP BY publisher ORDER BY n DESC, publisher LIMIT 3) p) AS publishers,
       (SELECT group_concat(l.link, ' | ')
          FROM (SELECT 'successor of ' || predecessor_key AS link FROM conf_dblp_lineage WHERE successor_key = v.dblp_key
                UNION ALL
                SELECT 'predecessor of ' || successor_key FROM conf_dblp_lineage WHERE predecessor_key = v.dblp_key
                ORDER BY 1) l) AS lineage,
       (SELECT group_concat(b.book_title || ' (' || b.papers || ', ' || coalesce(b.first_year, '?') || '-'
                            || coalesce(b.last_year, '?') || ')', ' | ')
          FROM (SELECT book_title, papers, first_year, last_year FROM conf_dblp_book_title WHERE dblp_key = v.dblp_key
                ORDER BY papers DESC, book_title LIMIT 5) b) AS book_titles,
       (SELECT group_concat(p.proceeding_title || ' (' || p.papers || ' papers)', ' | ')
          FROM (SELECT proceeding_title, papers FROM conf_dblp_proceeding_title WHERE dblp_key = v.dblp_key
                ORDER BY year DESC, proceeding_title DESC LIMIT 3) p) AS proceeding_titles
FROM conf_dblp_venue v;

DROP VIEW IF EXISTS conf_review;
CREATE VIEW conf_review AS
SELECT 'UNREVIEWED' AS issue, c.dblp_key, e.dblp_acronym, c.core_id, v.core_title, v.core_acronym, v.rank,
       c.method, c.detail,
       e.papers, e.first_year, e.last_year, e.volumes, e.publishers, e.lineage, e.book_titles, e.proceeding_titles
FROM conf_candidate_rule c
JOIN conf_core v ON v.id = c.core_id
JOIN conf_summary e ON e.dblp_key = c.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM conf_candidate_ai d WHERE d.dblp_key = c.dblp_key AND d.core_id = c.core_id)
UNION ALL
SELECT 'UNCOVERED', NULL, NULL, v.id, v.core_title, v.core_acronym, v.rank,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM conf_core v
WHERE NOT EXISTS (SELECT 1 FROM conf_candidate_ai d WHERE d.core_id = v.id AND d.verdict IN ('accept', 'absent'))
UNION ALL
SELECT 'INVALID', d.dblp_key, NULL, d.core_id, NULL, NULL, NULL,
       NULL, 'conf_candidate_ai ' || d.id || ' refers to a missing DBLP key or CORE venue: delete it',
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM conf_candidate_ai d
WHERE NOT EXISTS (SELECT 1 FROM conf_core v WHERE v.id = d.core_id)
   OR (d.dblp_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM conf_dblp_venue k WHERE k.dblp_key = d.dblp_key));

DROP VIEW IF EXISTS conf_list;
CREATE VIEW conf_list AS
SELECT d.dblp_key, upper(k.dblp_acronym) AS dblp_acronym, v.core_title, v.rank AS ranking
FROM conf_candidate_ai d
JOIN conf_core v ON v.id = d.core_id
JOIN conf_dblp_venue k ON k.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM conf_review)
ORDER BY CASE v.rank WHEN 'A*' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
         lower(v.core_title), d.dblp_key;

CREATE TABLE IF NOT EXISTS journal_scopus (
    id               INTEGER PRIMARY KEY,
    scopus_title     TEXT NOT NULL,
    issn_1           TEXT,
    issn_2           TEXT,
    scopus_publisher TEXT,
    quartile         TEXT NOT NULL CHECK (quartile IN ('Q1', 'Q2', 'Q3', 'Q4')),
    coverage         TEXT
);

CREATE TABLE IF NOT EXISTS journal_dblp_venue (
    dblp_key   TEXT PRIMARY KEY CHECK (dblp_key LIKE 'journals/%'),
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS journal_dblp_name (
    dblp_key   TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    name       TEXT NOT NULL,
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER,
    PRIMARY KEY (dblp_key, name)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS journal_crossref (
    doi                TEXT PRIMARY KEY,
    dblp_key           TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    name               TEXT NOT NULL,
    year               INTEGER NOT NULL,
    status             INTEGER CHECK (status IN (200, 404)),
    issn_1             TEXT,
    issn_2             TEXT,
    crossref_title     TEXT,
    crossref_publisher TEXT
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_crossref_key ON journal_crossref (dblp_key);

CREATE TABLE IF NOT EXISTS journal_dblp_issn (
    dblp_key TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    issn     TEXT NOT NULL,
    PRIMARY KEY (dblp_key, issn)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_dblp_issn_issn ON journal_dblp_issn (issn);

CREATE TABLE IF NOT EXISTS journal_dblp_lineage (
    predecessor_key TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    successor_key   TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    PRIMARY KEY (predecessor_key, successor_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_dblp_lineage_successor ON journal_dblp_lineage (successor_key);

CREATE TABLE IF NOT EXISTS journal_dblp_doi (
    dblp_key TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    prefix   TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, prefix)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS journal_candidate_rule (
    dblp_key TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    scopus_id   INTEGER NOT NULL REFERENCES journal_scopus ON DELETE CASCADE,
    method   TEXT NOT NULL CHECK (method IN ('dblp', 'crossref', 'lineage', 'name')),
    detail   TEXT NOT NULL,
    PRIMARY KEY (dblp_key, scopus_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_candidate_rule_scopus ON journal_candidate_rule (scopus_id);

CREATE TABLE IF NOT EXISTS journal_candidate_ai (
    id         INTEGER PRIMARY KEY,
    scopus_id     INTEGER NOT NULL REFERENCES journal_scopus ON DELETE CASCADE,
    dblp_key   TEXT REFERENCES journal_dblp_venue ON DELETE CASCADE,
    verdict    TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'absent')),
    reason     TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'absent') = (dblp_key IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS journal_candidate_ai_pair ON journal_candidate_ai (dblp_key, scopus_id) WHERE dblp_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS journal_candidate_ai_one_journal_per_key ON journal_candidate_ai (dblp_key) WHERE verdict = 'accept';
CREATE UNIQUE INDEX IF NOT EXISTS journal_candidate_ai_one_absent ON journal_candidate_ai (scopus_id) WHERE verdict = 'absent';
CREATE INDEX IF NOT EXISTS journal_candidate_ai_scopus ON journal_candidate_ai (scopus_id);

CREATE TRIGGER IF NOT EXISTS journal_candidate_ai_accept_vs_absent_ins BEFORE INSERT ON journal_candidate_ai
WHEN EXISTS (
    SELECT 1 FROM journal_candidate_ai d WHERE d.scopus_id = NEW.scopus_id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'Scopus journal cannot be both accepted and absent'); END;

CREATE TRIGGER IF NOT EXISTS journal_candidate_ai_accept_vs_absent_upd BEFORE UPDATE ON journal_candidate_ai
WHEN EXISTS (
    SELECT 1 FROM journal_candidate_ai d WHERE d.scopus_id = NEW.scopus_id AND d.id <> NEW.id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'Scopus journal cannot be both accepted and absent'); END;

DROP VIEW IF EXISTS journal_summary;
CREATE VIEW journal_summary AS
SELECT j.dblp_key, j.papers, j.first_year, j.last_year,
       (SELECT group_concat(issn, ' ') FROM (SELECT issn FROM journal_dblp_issn WHERE dblp_key = j.dblp_key
                                              ORDER BY issn)) AS dblp_issns,
       (SELECT group_concat(n.name || ' (' || n.papers || ', ' || coalesce(n.first_year, '?') || '-'
                            || coalesce(n.last_year, '?') || ')', ' | ')
          FROM (SELECT name, papers, first_year, last_year FROM journal_dblp_name WHERE dblp_key = j.dblp_key
                ORDER BY last_year DESC, papers DESC, name LIMIT 5) n) AS dblp_names,
       (SELECT group_concat(x.crossref_title || ' [' || x.issns || '] (' || x.dois || ' DOIs, ' || x.lo || '-' || x.hi || ')', ' | ')
          FROM (SELECT coalesce(crossref_title, '?') AS crossref_title, trim(coalesce(issn_1, '') || ' ' || coalesce(issn_2, '')) AS issns,
                       count(*) AS dois, min(year) AS lo, max(year) AS hi
                FROM journal_crossref WHERE dblp_key = j.dblp_key AND status = 200
                GROUP BY 1, 2 ORDER BY hi DESC, dois DESC LIMIT 4) x) AS crossref_titles,
       (SELECT group_concat(c.crossref_publisher || ' (' || c.dois || ' DOIs, ' || c.lo || '-' || c.hi || ')', ' | ')
          FROM (SELECT crossref_publisher, count(*) AS dois, min(year) AS lo, max(year) AS hi
                FROM journal_crossref WHERE dblp_key = j.dblp_key AND status = 200 AND coalesce(crossref_publisher, '') <> ''
                GROUP BY crossref_publisher ORDER BY hi DESC, dois DESC LIMIT 3) c) AS crossref_publishers,
       (SELECT group_concat(p.prefix || ' (' || p.papers || ')', ' | ')
          FROM (SELECT prefix, papers FROM journal_dblp_doi WHERE dblp_key = j.dblp_key
                ORDER BY papers DESC, prefix LIMIT 3) p) AS doi_prefixes,
       (SELECT group_concat(l.link, ' | ')
          FROM (SELECT 'successor of ' || predecessor_key AS link FROM journal_dblp_lineage WHERE successor_key = j.dblp_key
                UNION ALL
                SELECT 'predecessor of ' || successor_key FROM journal_dblp_lineage WHERE predecessor_key = j.dblp_key
                ORDER BY 1) l) AS lineage
FROM journal_dblp_venue j;

DROP VIEW IF EXISTS journal_review;
CREATE VIEW journal_review AS
SELECT 'UNREVIEWED' AS issue, c.dblp_key, c.scopus_id, s.scopus_title, s.issn_1, s.issn_2,
       s.scopus_publisher, s.quartile, s.coverage, c.method, c.detail,
       e.papers, e.first_year, e.last_year, e.dblp_issns, e.dblp_names, e.crossref_titles, e.crossref_publishers,
       e.doi_prefixes, e.lineage
FROM journal_candidate_rule c
JOIN journal_scopus s ON s.id = c.scopus_id
JOIN journal_summary e ON e.dblp_key = c.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM journal_candidate_ai d WHERE d.dblp_key = c.dblp_key AND d.scopus_id = c.scopus_id)
UNION ALL
SELECT 'UNCOVERED', NULL, s.id, s.scopus_title, s.issn_1, s.issn_2, s.scopus_publisher, s.quartile, s.coverage,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM journal_scopus s
WHERE NOT EXISTS (SELECT 1 FROM journal_candidate_ai d WHERE d.scopus_id = s.id AND d.verdict IN ('accept', 'absent'))
UNION ALL
SELECT 'INVALID', d.dblp_key, d.scopus_id, NULL, NULL, NULL, NULL, NULL, NULL,
       NULL, 'journal_candidate_ai ' || d.id || ' refers to a missing DBLP key or Scopus journal: delete it',
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM journal_candidate_ai d
WHERE NOT EXISTS (SELECT 1 FROM journal_scopus s WHERE s.id = d.scopus_id)
   OR (d.dblp_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM journal_dblp_venue k WHERE k.dblp_key = d.dblp_key));

DROP VIEW IF EXISTS journal_list;
CREATE VIEW journal_list AS
SELECT d.dblp_key,
       (SELECT name FROM journal_dblp_name n WHERE n.dblp_key = d.dblp_key
         ORDER BY last_year DESC, papers DESC, name LIMIT 1) AS dblp_name,
       s.scopus_title, s.issn_1, s.issn_2, s.quartile
FROM journal_candidate_ai d
JOIN journal_scopus s ON s.id = d.scopus_id
JOIN journal_dblp_venue k ON k.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM journal_review)
ORDER BY s.quartile, lower(s.scopus_title), d.dblp_key;
"""

PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_semantic_threshold (
    basis     TEXT PRIMARY KEY CHECK (basis IN ('title', 'title_abstract')),
    threshold REAL NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS paper_dblp (
    paper_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    dblp_key    TEXT NOT NULL UNIQUE,
    venue_key   TEXT NOT NULL,
    record_type TEXT NOT NULL CHECK (record_type IN ('article', 'inproceedings')),
    title       TEXT,
    authors     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(authors) AND json_type(authors) = 'array'),
    year        INTEGER NOT NULL,
    venue       TEXT,
    doi         TEXT,
    ee          TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(ee) AND json_type(ee) = 'array')
);
CREATE INDEX IF NOT EXISTS paper_dblp_venue_key ON paper_dblp (venue_key);
CREATE INDEX IF NOT EXISTS paper_dblp_doi ON paper_dblp (doi);

CREATE TABLE IF NOT EXISTS paper_status (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    status   TEXT NOT NULL DEFAULT 'A' CHECK (status IN ('A', 'B', 'B-1', 'B-2', 'B-3', 'B-4', 'C', 'D', 'E', 'F', 'G'))
);
CREATE INDEX IF NOT EXISTS paper_status_status ON paper_status (status);

CREATE TABLE IF NOT EXISTS paper_field_arxiv (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    arxiv_id TEXT CHECK (arxiv_id IS NULL OR length(trim(arxiv_id)) > 0)
);
CREATE INDEX IF NOT EXISTS paper_field_arxiv_arxiv_id ON paper_field_arxiv (arxiv_id);

CREATE TABLE IF NOT EXISTS paper_field_abstract (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    abstract TEXT CHECK (abstract IS NULL OR length(trim(abstract)) > 0)
);

CREATE TABLE IF NOT EXISTS paper_semantic_vector (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    basis    TEXT NOT NULL CHECK (basis IN ('title', 'title_abstract')),
    vector   BLOB NOT NULL CHECK (length(vector) > 0 AND length(vector) % 4 = 0)
);

CREATE TABLE IF NOT EXISTS paper_semantic_shortlist (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id)
);

CREATE TABLE IF NOT EXISTS paper_ai_shortlist (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    category TEXT NOT NULL CHECK (category IN ('baseline', 'benchmark', 'survey', 'evaluation'))
);

DROP VIEW IF EXISTS paper_summary;
CREATE VIEW paper_summary AS
SELECT p.paper_id, p.dblp_key, p.venue_key, p.record_type, p.title, p.authors, p.year, p.venue, p.doi, p.ee,
       x.arxiv_id,
       a.abstract,
       st.status,
       CASE WHEN c.dblp_key IS NOT NULL THEN c.core_title ELSE j.scopus_title END AS venue_name,
       CASE WHEN c.dblp_key IS NOT NULL THEN c.ranking ELSE j.quartile END AS venue_rank,
       ai.category
FROM paper_dblp p
LEFT JOIN paper_field_arxiv x ON x.paper_id = p.paper_id
LEFT JOIN paper_field_abstract a ON a.paper_id = p.paper_id
LEFT JOIN paper_status st ON st.paper_id = p.paper_id
LEFT JOIN conf_list c ON c.dblp_key = p.venue_key
LEFT JOIN journal_list j ON j.dblp_key = p.venue_key
LEFT JOIN paper_ai_shortlist ai ON ai.paper_id = p.paper_id;

DROP VIEW IF EXISTS paper_review;
CREATE VIEW paper_review AS
SELECT paper_id FROM paper_field_abstract WHERE abstract IS NULL;

DROP VIEW IF EXISTS paper_list;
CREATE VIEW paper_list AS
SELECT p.paper_id, p.dblp_key, p.venue_key, p.title, p.authors, p.year, p.venue, p.doi,
       x.arxiv_id,
       a.abstract,
       ai.category,
       CASE WHEN c.dblp_key IS NOT NULL THEN c.core_title ELSE j.scopus_title END AS venue_name,
       CASE WHEN c.dblp_key IS NOT NULL THEN c.ranking ELSE j.quartile END AS venue_rank
FROM paper_ai_shortlist ai
JOIN paper_dblp p ON p.paper_id = ai.paper_id
LEFT JOIN paper_field_arxiv x ON x.paper_id = p.paper_id
LEFT JOIN paper_field_abstract a ON a.paper_id = p.paper_id
LEFT JOIN conf_list c ON c.dblp_key = p.venue_key
LEFT JOIN journal_list j ON j.dblp_key = p.venue_key;
"""


def migrate_status(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'paper_status'").fetchone()
    if row is None or "'B-4'" in row[0]:
        return
    conn.executescript("""
PRAGMA foreign_keys = OFF;
BEGIN;
DROP VIEW IF EXISTS paper_summary;
CREATE TABLE paper_status_new (
    paper_id INTEGER PRIMARY KEY REFERENCES paper_dblp (paper_id),
    status   TEXT NOT NULL DEFAULT 'A' CHECK (status IN ('A', 'B', 'B-1', 'B-2', 'B-3', 'B-4', 'C', 'D', 'E', 'F', 'G'))
);
INSERT INTO paper_status_new (paper_id, status)
SELECT paper_id, CASE status WHEN 'B-3' THEN 'B-4' ELSE status END FROM paper_status;
DROP TABLE paper_status;
ALTER TABLE paper_status_new RENAME TO paper_status;
COMMIT;
PRAGMA foreign_keys = ON;
""")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    conn = sqlite3.connect(path)
    migrate_status(conn)
    conn.executescript(SCHEMA + PAPER_SCHEMA)
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        sys.exit(f"foreign key violations: {broken[:10]}")
    counts = dict(conn.execute(
        "SELECT type, count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' GROUP BY type"))
    conn.close()
    print(f"{path}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
