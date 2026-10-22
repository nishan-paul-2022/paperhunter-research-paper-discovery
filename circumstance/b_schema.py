#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "archive" / "paperhunter.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conf_core_venue (
    id      INTEGER PRIMARY KEY,
    title   TEXT NOT NULL UNIQUE,
    acronym TEXT NOT NULL,
    rank    TEXT NOT NULL CHECK (rank IN ('A*', 'A', 'B', 'C'))
);

CREATE TABLE IF NOT EXISTS conf_dblp_venue (
    dblp_key   TEXT PRIMARY KEY CHECK (dblp_key LIKE 'conf/%' OR dblp_key LIKE 'journals/%'),
    acronym    TEXT NOT NULL,
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS conf_dblp_booktitle (
    dblp_key   TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    booktitle  TEXT NOT NULL,
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER,
    PRIMARY KEY (dblp_key, booktitle)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS conf_dblp_title (
    dblp_key  TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    proc_key  TEXT PRIMARY KEY,
    year      INTEGER NOT NULL,
    title     TEXT NOT NULL,
    publisher TEXT,
    papers    INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_dblp_title_key ON conf_dblp_title (dblp_key, year);

CREATE TABLE IF NOT EXISTS conf_dblp_lineage (
    predecessor_key TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    successor_key   TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    PRIMARY KEY (predecessor_key, successor_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_dblp_lineage_successor ON conf_dblp_lineage (successor_key);

CREATE TABLE IF NOT EXISTS conf_candidate (
    dblp_key          TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    core_id           INTEGER NOT NULL REFERENCES conf_core_venue ON DELETE CASCADE,
    method            TEXT NOT NULL CHECK (method <> '' AND trim(replace(replace(replace(replace(
                          method, 'acronym', ''), 'related', ''), 'lineage', ''), 'title', ''), ',') = ''),
    detail            TEXT NOT NULL,
    PRIMARY KEY (dblp_key, core_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS conf_candidate_core ON conf_candidate (core_id);

CREATE TABLE IF NOT EXISTS conf_decision (
    id         INTEGER PRIMARY KEY,
    core_id    INTEGER NOT NULL REFERENCES conf_core_venue ON DELETE CASCADE,
    dblp_key   TEXT REFERENCES conf_dblp_venue ON DELETE CASCADE,
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

DROP VIEW IF EXISTS conf_summary;
CREATE VIEW conf_summary AS
SELECT v.dblp_key, v.acronym, v.papers, v.first_year, v.last_year,
       (SELECT count(*) FROM conf_dblp_title WHERE dblp_key = v.dblp_key) AS volumes,
       (SELECT group_concat(b.booktitle || ' (' || b.papers || ', ' || coalesce(b.first_year, '?') || '-'
                            || coalesce(b.last_year, '?') || ')', ' | ')
          FROM (SELECT booktitle, papers, first_year, last_year FROM conf_dblp_booktitle WHERE dblp_key = v.dblp_key
                ORDER BY papers DESC, booktitle LIMIT 5) b) AS booktitles,
       (SELECT group_concat(p.title || ' (' || p.papers || ' papers)', ' | ')
          FROM (SELECT title, papers FROM conf_dblp_title WHERE dblp_key = v.dblp_key
                ORDER BY year DESC, title DESC LIMIT 3) p) AS proceedings_titles
FROM conf_dblp_venue v;

DROP VIEW IF EXISTS conf_review;
CREATE VIEW conf_review AS
SELECT 'UNREVIEWED' AS issue, c.dblp_key, c.core_id, v.title AS core_title, v.acronym, v.rank,
       c.method, c.detail,
       e.papers, e.first_year, e.last_year, e.booktitles, e.proceedings_titles
FROM conf_candidate c
JOIN conf_core_venue v ON v.id = c.core_id
JOIN conf_summary e ON e.dblp_key = c.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM conf_decision d WHERE d.dblp_key = c.dblp_key AND d.core_id = c.core_id)
UNION ALL
SELECT 'UNCOVERED', NULL, v.id, v.title, v.acronym, v.rank,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM conf_core_venue v
WHERE NOT EXISTS (SELECT 1 FROM conf_decision d WHERE d.core_id = v.id AND d.verdict IN ('accept', 'absent'))
UNION ALL
SELECT 'INVALID', d.dblp_key, d.core_id, NULL, NULL, NULL,
       NULL, 'conf_decision ' || d.id || ' refers to a missing DBLP key or CORE venue: delete it',
       NULL, NULL, NULL, NULL, NULL
FROM conf_decision d
WHERE NOT EXISTS (SELECT 1 FROM conf_core_venue v WHERE v.id = d.core_id)
   OR (d.dblp_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM conf_dblp_venue k WHERE k.dblp_key = d.dblp_key));

DROP VIEW IF EXISTS conf_list;
CREATE VIEW conf_list AS
SELECT d.dblp_key, upper(k.acronym) AS dblp_acronym, v.title, v.rank AS ranking
FROM conf_decision d
JOIN conf_core_venue v ON v.id = d.core_id
JOIN conf_dblp_venue k ON k.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM conf_review)
ORDER BY CASE v.rank WHEN 'A*' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
         lower(v.title), d.dblp_key;

CREATE TABLE IF NOT EXISTS journal_scopus_venue (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    issn_1    TEXT,
    issn_2    TEXT,
    publisher TEXT,
    quartile  TEXT NOT NULL CHECK (quartile IN ('Q1', 'Q2', 'Q3', 'Q4')),
    coverage  TEXT
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

CREATE TABLE IF NOT EXISTS journal_dblp_crossref (
    doi       TEXT PRIMARY KEY,
    dblp_key  TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    name      TEXT NOT NULL,
    year      INTEGER NOT NULL,
    status    INTEGER CHECK (status IN (200, 404)),
    issn_1    TEXT,
    issn_2    TEXT,
    title     TEXT,
    publisher TEXT
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_dblp_crossref_key ON journal_dblp_crossref (dblp_key);

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

CREATE TABLE IF NOT EXISTS journal_candidate (
    dblp_key TEXT NOT NULL REFERENCES journal_dblp_venue ON DELETE CASCADE,
    scopus_id   INTEGER NOT NULL REFERENCES journal_scopus_venue ON DELETE CASCADE,
    method   TEXT NOT NULL CHECK (method IN ('dblp', 'crossref', 'lineage', 'name')),
    detail   TEXT NOT NULL,
    PRIMARY KEY (dblp_key, scopus_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_candidate_scopus ON journal_candidate (scopus_id);

CREATE TABLE IF NOT EXISTS journal_decision (
    id         INTEGER PRIMARY KEY,
    scopus_id     INTEGER NOT NULL REFERENCES journal_scopus_venue ON DELETE CASCADE,
    dblp_key   TEXT REFERENCES journal_dblp_venue ON DELETE CASCADE,
    verdict    TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'absent')),
    reason     TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'absent') = (dblp_key IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS journal_decision_pair ON journal_decision (dblp_key, scopus_id) WHERE dblp_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS journal_decision_one_journal_per_key ON journal_decision (dblp_key) WHERE verdict = 'accept';
CREATE UNIQUE INDEX IF NOT EXISTS journal_decision_one_absent ON journal_decision (scopus_id) WHERE verdict = 'absent';
CREATE INDEX IF NOT EXISTS journal_decision_scopus ON journal_decision (scopus_id);

CREATE TRIGGER IF NOT EXISTS journal_decision_accept_vs_absent_ins BEFORE INSERT ON journal_decision
WHEN EXISTS (
    SELECT 1 FROM journal_decision d WHERE d.scopus_id = NEW.scopus_id
      AND ((NEW.verdict = 'accept' AND d.verdict = 'absent')
        OR (NEW.verdict = 'absent' AND d.verdict = 'accept'))
)
BEGIN SELECT RAISE(ABORT, 'Scopus journal cannot be both accepted and absent'); END;

CREATE TRIGGER IF NOT EXISTS journal_decision_accept_vs_absent_upd BEFORE UPDATE ON journal_decision
WHEN EXISTS (
    SELECT 1 FROM journal_decision d WHERE d.scopus_id = NEW.scopus_id AND d.id <> NEW.id
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
                ORDER BY last_year DESC, papers DESC, name LIMIT 5) n) AS names,
       (SELECT group_concat(x.title || ' [' || x.issns || '] (' || x.dois || ' DOIs, ' || x.lo || '-' || x.hi || ')', ' | ')
          FROM (SELECT coalesce(title, '?') AS title, trim(coalesce(issn_1, '') || ' ' || coalesce(issn_2, '')) AS issns,
                       count(*) AS dois, min(year) AS lo, max(year) AS hi
                FROM journal_dblp_crossref WHERE dblp_key = j.dblp_key AND status = 200
                GROUP BY 1, 2 ORDER BY hi DESC, dois DESC LIMIT 4) x) AS crossref,
       (SELECT group_concat(p.prefix || ' (' || p.papers || ')', ' | ')
          FROM (SELECT prefix, papers FROM journal_dblp_doi WHERE dblp_key = j.dblp_key
                ORDER BY papers DESC, prefix LIMIT 3) p) AS doi_prefixes
FROM journal_dblp_venue j;

DROP VIEW IF EXISTS journal_review;
CREATE VIEW journal_review AS
SELECT 'UNREVIEWED' AS issue, c.dblp_key, c.scopus_id, s.title AS scopus_title, s.issn_1, s.issn_2,
       s.publisher, s.quartile, s.coverage, c.method, c.detail,
       e.papers, e.first_year, e.last_year, e.dblp_issns, e.names, e.crossref, e.doi_prefixes
FROM journal_candidate c
JOIN journal_scopus_venue s ON s.id = c.scopus_id
JOIN journal_summary e ON e.dblp_key = c.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM journal_decision d WHERE d.dblp_key = c.dblp_key AND d.scopus_id = c.scopus_id)
UNION ALL
SELECT 'UNCOVERED', NULL, s.id, s.title, s.issn_1, s.issn_2, s.publisher, s.quartile, s.coverage,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM journal_scopus_venue s
WHERE NOT EXISTS (SELECT 1 FROM journal_decision d WHERE d.scopus_id = s.id AND d.verdict IN ('accept', 'absent'))
UNION ALL
SELECT 'INVALID', d.dblp_key, d.scopus_id, NULL, NULL, NULL, NULL, NULL, NULL,
       NULL, 'journal_decision ' || d.id || ' refers to a missing DBLP key or Scopus journal: delete it',
       NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM journal_decision d
WHERE NOT EXISTS (SELECT 1 FROM journal_scopus_venue s WHERE s.id = d.scopus_id)
   OR (d.dblp_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM journal_dblp_venue k WHERE k.dblp_key = d.dblp_key));

DROP VIEW IF EXISTS journal_list;
CREATE VIEW journal_list AS
SELECT d.dblp_key,
       (SELECT name FROM journal_dblp_name n WHERE n.dblp_key = d.dblp_key
         ORDER BY last_year DESC, papers DESC, name LIMIT 1) AS dblp_name,
       s.title, s.issn_1, s.issn_2, s.quartile
FROM journal_decision d
JOIN journal_scopus_venue s ON s.id = d.scopus_id
JOIN journal_dblp_venue k ON k.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM journal_review)
ORDER BY s.quartile, lower(s.title), d.dblp_key;

CREATE TABLE IF NOT EXISTS paper_dblp (
    id        INTEGER PRIMARY KEY,
    dblp_key  TEXT NOT NULL UNIQUE,
    venue_key TEXT NOT NULL,
    year      INTEGER NOT NULL,
    title     TEXT NOT NULL,
    authors   TEXT NOT NULL,
    venue     TEXT NOT NULL,
    doi       TEXT
);
CREATE INDEX IF NOT EXISTS paper_dblp_venue ON paper_dblp (venue_key);

CREATE TABLE IF NOT EXISTS paper_details_openalex (
    paper_id     INTEGER PRIMARY KEY REFERENCES paper_dblp ON DELETE CASCADE,
    via          TEXT NOT NULL CHECK (via IN ('doi', 'title')),
    found        INTEGER NOT NULL CHECK (found IN (0, 1)),
    openalex_id  TEXT,
    doi          TEXT,
    title        TEXT,
    year         INTEGER,
    abstract     TEXT,
    keywords     TEXT,
    topics       TEXT,
    work_type    TEXT,
    is_paratext  INTEGER CHECK (is_paratext IN (0, 1)),
    is_retracted INTEGER CHECK (is_retracted IN (0, 1)),
    language     TEXT,
    arxiv_id     TEXT,
    fetched_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((found = 1) = (openalex_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS paper_details_s2 (
    paper_id   INTEGER PRIMARY KEY REFERENCES paper_dblp ON DELETE CASCADE,
    via        TEXT NOT NULL CHECK (via IN ('doi', 'title')),
    found      INTEGER NOT NULL CHECK (found IN (0, 1)),
    s2_id      TEXT,
    doi        TEXT,
    title      TEXT,
    year       INTEGER,
    abstract   TEXT,
    tldr       TEXT,
    fields     TEXT,
    arxiv_id   TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((found = 1) = (s2_id IS NOT NULL))
);

DROP VIEW IF EXISTS paper_summary;
CREATE VIEW paper_summary AS
SELECT p.id AS paper_id, p.title,
       CASE WHEN length(s.abstract) >= 120 THEN s.abstract END AS abstract_s2,
       CASE WHEN length(o.abstract) >= 120 THEN o.abstract END AS abstract_openalex,
       s.tldr, o.keywords, o.topics, s.fields, o.work_type, o.is_paratext
FROM paper_dblp p
LEFT JOIN paper_details_openalex o ON o.paper_id = p.id
LEFT JOIN paper_details_s2 s ON s.paper_id = p.id;

CREATE TABLE IF NOT EXISTS paper_decision (
    paper_id     INTEGER PRIMARY KEY REFERENCES paper_dblp ON DELETE CASCADE,
    verdict      TEXT NOT NULL CHECK (verdict IN ('accept', 'reject')),
    role         TEXT CHECK (role IN ('baseline', 'benchmark', 'evaluation', 'survey')),
    p            REAL NOT NULL CHECK (p >= 0 AND p <= 1),
    agent        TEXT CHECK (agent IN ('y', 'p', 'n', 'u')),
    offense      TEXT CHECK (offense IN ('y', 'p', 'n', 'u')),
    black_box    TEXT CHECK (black_box IN ('y', 'p', 'n', 'u')),
    long_horizon TEXT CHECK (long_horizon IN ('y', 'p', 'n', 'u')),
    quote        TEXT,
    quote_src    TEXT CHECK (quote_src IN ('title', 'abstract', 'tldr')),
    reject_class TEXT CHECK (reject_class IN ('off_domain', 'security_other', 'defensive_only',
                                              'vuln_detection_or_code_scan', 'generic_llm_security',
                                              'static_qa_benchmark', 'llm_no_offense', 'frontmatter')),
    reason       TEXT NOT NULL CHECK (length(trim(reason)) >= 10
                                      AND length(trim(reason)) - length(replace(trim(reason), ' ', '')) < 60),
    reviewer     TEXT NOT NULL CHECK (length(trim(reviewer)) > 0),
    decided_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (verdict <> 'accept' OR (role IS NOT NULL AND p >= 0.5 AND agent = 'y' AND offense = 'y'
                                   AND black_box IS NOT NULL AND long_horizon IS NOT NULL
                                   AND quote_src IS NOT NULL AND reject_class IS NULL
                                   AND length(trim(quote)) - length(replace(trim(quote), ' ', '')) BETWEEN 3 AND 44)),
    CHECK (verdict <> 'reject' OR (reject_class IS NOT NULL AND p < 0.5
                                   AND role IS NULL AND quote IS NULL AND quote_src IS NULL)),
    CHECK (role IS NOT 'baseline' OR (black_box <> 'n' AND long_horizon <> 'n'))
);

DROP VIEW IF EXISTS paper_review;
CREATE VIEW paper_review AS
SELECT 'UNFETCHED' AS issue, p.id AS paper_id, p.dblp_key,
       'run paper-hunter/b_details.py' AS detail
FROM paper_dblp p
WHERE NOT EXISTS (SELECT 1 FROM paper_details_s2 s WHERE s.paper_id = p.id AND (s.found = 1 OR s.via = 'title'))
   OR (NOT EXISTS (SELECT 1 FROM paper_details_openalex o WHERE o.paper_id = p.id AND (o.found = 1 OR o.via = 'title'))
       AND NOT EXISTS (SELECT 1 FROM paper_summary d WHERE d.paper_id = p.id
                         AND (d.abstract_s2 IS NOT NULL OR d.abstract_openalex IS NOT NULL)))
UNION ALL
SELECT 'UNDECIDED', p.id, p.dblp_key, NULL
FROM paper_dblp p
WHERE NOT EXISTS (SELECT 1 FROM paper_decision d WHERE d.paper_id = p.id)
  AND EXISTS (SELECT 1 FROM paper_details_s2 s WHERE s.paper_id = p.id AND (s.found = 1 OR s.via = 'title'))
  AND (EXISTS (SELECT 1 FROM paper_details_openalex o WHERE o.paper_id = p.id AND (o.found = 1 OR o.via = 'title'))
       OR EXISTS (SELECT 1 FROM paper_summary d WHERE d.paper_id = p.id
                    AND (d.abstract_s2 IS NOT NULL OR d.abstract_openalex IS NOT NULL)))
UNION ALL
SELECT 'INVALID', d.paper_id, p.dblp_key,
       'quote no longer in the stored ' || d.quote_src || ': delete the decision and review the paper again'
FROM paper_decision d
JOIN paper_dblp p ON p.id = d.paper_id
JOIN paper_summary x ON x.paper_id = d.paper_id
WHERE d.verdict = 'accept'
  AND coalesce(CASE d.quote_src
                 WHEN 'title' THEN instr(lower(x.title), lower(trim(d.quote))) > 0
                 WHEN 'abstract' THEN instr(lower(x.abstract_s2), lower(trim(d.quote))) > 0
                                   OR instr(lower(x.abstract_openalex), lower(trim(d.quote))) > 0
                 ELSE instr(lower(x.tldr), lower(trim(d.quote))) > 0 END, 0) = 0;

DROP TRIGGER IF EXISTS paper_decision_fetched;
CREATE TRIGGER paper_decision_fetched BEFORE INSERT ON paper_decision
WHEN NOT EXISTS (SELECT 1 FROM paper_review r WHERE r.paper_id = NEW.paper_id AND r.issue = 'UNDECIDED')
BEGIN SELECT RAISE(ABORT, 'paper is not UNDECIDED in paper_review (unknown, not fetched yet, or already decided)'); END;

DROP TRIGGER IF EXISTS paper_decision_quote_ins;
CREATE TRIGGER paper_decision_quote_ins BEFORE INSERT ON paper_decision
WHEN NEW.verdict = 'accept' AND NOT EXISTS (
    SELECT 1 FROM paper_summary d
    WHERE d.paper_id = NEW.paper_id
      AND CASE NEW.quote_src
            WHEN 'title' THEN instr(lower(d.title), lower(trim(NEW.quote))) > 0
            WHEN 'abstract' THEN instr(lower(d.abstract_s2), lower(trim(NEW.quote))) > 0
                              OR instr(lower(d.abstract_openalex), lower(trim(NEW.quote))) > 0
            ELSE instr(lower(d.tldr), lower(trim(NEW.quote))) > 0 END
)
BEGIN SELECT RAISE(ABORT, 'quote not found verbatim in the stored quote_src text'); END;

DROP TRIGGER IF EXISTS paper_decision_quote_upd;
CREATE TRIGGER paper_decision_quote_upd BEFORE UPDATE ON paper_decision
WHEN NEW.verdict = 'accept' AND NOT EXISTS (
    SELECT 1 FROM paper_summary d
    WHERE d.paper_id = NEW.paper_id
      AND CASE NEW.quote_src
            WHEN 'title' THEN instr(lower(d.title), lower(trim(NEW.quote))) > 0
            WHEN 'abstract' THEN instr(lower(d.abstract_s2), lower(trim(NEW.quote))) > 0
                              OR instr(lower(d.abstract_openalex), lower(trim(NEW.quote))) > 0
            ELSE instr(lower(d.tldr), lower(trim(NEW.quote))) > 0 END
)
BEGIN SELECT RAISE(ABORT, 'quote not found verbatim in the stored quote_src text'); END;

DROP VIEW IF EXISTS paper_list;
CREATE VIEW paper_list AS
SELECT p.dblp_key, d.role, d.p, p.year, p.title, p.authors, p.venue, p.venue_key,
       coalesce((SELECT c.ranking FROM conf_list c WHERE c.dblp_key = p.venue_key),
                (SELECT j.quartile FROM journal_list j WHERE j.dblp_key = p.venue_key)) AS rank,
       p.doi, coalesce(s.arxiv_id, o.arxiv_id) AS arxiv_id, d.quote, d.reason
FROM paper_decision d
JOIN paper_dblp p ON p.id = d.paper_id
LEFT JOIN paper_details_openalex o ON o.paper_id = d.paper_id
LEFT JOIN paper_details_s2 s ON s.paper_id = d.paper_id
WHERE d.verdict = 'accept' AND NOT EXISTS (SELECT 1 FROM paper_review WHERE issue <> 'UNFETCHED')
ORDER BY CASE d.role WHEN 'baseline' THEN 0 WHEN 'benchmark' THEN 1 WHEN 'evaluation' THEN 2 ELSE 3 END,
         d.p DESC, p.dblp_key;
"""


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        sys.exit(f"foreign key violations: {broken[:10]}")
    counts = dict(conn.execute(
        "SELECT type, count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' GROUP BY type"))
    conn.close()
    print(f"{path}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
