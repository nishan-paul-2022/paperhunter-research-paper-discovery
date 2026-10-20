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

CREATE TABLE IF NOT EXISTS conf_candidate (
    dblp_key          TEXT NOT NULL REFERENCES conf_dblp_venue ON DELETE CASCADE,
    core_id           INTEGER NOT NULL REFERENCES conf_core_venue ON DELETE CASCADE,
    score             REAL NOT NULL,
    proceedings_title TEXT NOT NULL,
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
       c.score, c.proceedings_title AS detail,
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

CREATE TABLE IF NOT EXISTS sjr_journal (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    issn_1    TEXT,
    issn_2    TEXT,
    publisher TEXT,
    quartile  TEXT NOT NULL CHECK (quartile IN ('Q1', 'Q2', 'Q3', 'Q4'))
);

CREATE TABLE IF NOT EXISTS dblp_journal (
    dblp_key   TEXT PRIMARY KEY CHECK (dblp_key LIKE 'journals/%'),
    papers     INTEGER NOT NULL,
    first_year INTEGER,
    last_year  INTEGER
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dblp_journal_name (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    name     TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, name)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dblp_issn (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    issn     TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, issn)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dblp_doi_prefix (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    prefix   TEXT NOT NULL,
    papers   INTEGER NOT NULL,
    PRIMARY KEY (dblp_key, prefix)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS journal_candidate (
    dblp_key TEXT NOT NULL REFERENCES dblp_journal ON DELETE CASCADE,
    sjr_id   INTEGER NOT NULL REFERENCES sjr_journal ON DELETE CASCADE,
    method   TEXT NOT NULL CHECK (method IN ('issn', 'name')),
    score    REAL NOT NULL,
    detail   TEXT NOT NULL,
    PRIMARY KEY (dblp_key, sjr_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS journal_candidate_sjr ON journal_candidate (sjr_id);

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

CREATE TABLE IF NOT EXISTS dblp_paper (
    id        INTEGER PRIMARY KEY,
    dblp_key  TEXT NOT NULL UNIQUE,
    venue_key TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('conference', 'journal')),
    year      INTEGER NOT NULL,
    title     TEXT NOT NULL,
    authors   TEXT NOT NULL,
    venue     TEXT NOT NULL,
    ee        TEXT,
    url       TEXT,
    doi       TEXT
);
CREATE INDEX IF NOT EXISTS dblp_paper_venue ON dblp_paper (venue_key);
CREATE INDEX IF NOT EXISTS dblp_paper_year ON dblp_paper (year);

DROP VIEW IF EXISTS paper_inventory;
CREATE VIEW paper_inventory AS
SELECT p.id, p.dblp_key, p.venue_key, p.kind, p.year, p.title, p.authors, p.venue, p.ee, p.url, p.doi,
       r.rank,
       CASE r.rank WHEN 'A*' THEN 'a-star' ELSE lower(r.rank) END || '-' || p.year AS slice
FROM dblp_paper p
JOIN (SELECT dblp_key, ranking AS rank FROM conf_list WHERE dblp_key LIKE 'conf/%'
      UNION ALL
      SELECT dblp_key, quartile AS rank FROM list_of_journals) r ON r.dblp_key = p.venue_key;

CREATE TABLE IF NOT EXISTS paper_source (
    paper_id   INTEGER NOT NULL REFERENCES dblp_paper(id),
    src        TEXT NOT NULL CHECK (src IN ('openalex', 's2')),
    via        TEXT NOT NULL CHECK (via IN ('doi', 'title')),
    found      INTEGER NOT NULL CHECK (found IN (0, 1)),
    raw        BLOB,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (paper_id, src),
    CHECK ((found = 1) = (raw IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS paper_detail (
    paper_id      INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
    openalex_id   TEXT,
    s2_id         TEXT,
    corpus_id     INTEGER,
    arxiv_id      TEXT,
    abstract      TEXT,
    abstract_src  TEXT CHECK (abstract_src IN ('s2', 'openalex')),
    tldr          TEXT,
    language      TEXT,
    work_type     TEXT,
    is_retracted  INTEGER CHECK (is_retracted IN (0, 1)),
    is_paratext   INTEGER CHECK (is_paratext IN (0, 1)),
    is_oa         INTEGER CHECK (is_oa IN (0, 1)),
    oa_status     TEXT,
    oa_url        TEXT,
    pdf_url       TEXT,
    text_availability TEXT,
    primary_topic TEXT,
    subfield      TEXT,
    field         TEXT,
    domain        TEXT,
    reference_count INTEGER,
    citation_count  INTEGER,
    influential_citation_count INTEGER,
    built_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_term (
    paper_id INTEGER NOT NULL REFERENCES dblp_paper(id),
    kind     TEXT NOT NULL CHECK (kind IN ('keyword', 'topic', 'field')),
    name     TEXT NOT NULL,
    score    REAL,
    PRIMARY KEY (paper_id, kind, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS paper_term_name ON paper_term (kind, name);

CREATE TABLE IF NOT EXISTS paper_reference (
    paper_id INTEGER NOT NULL REFERENCES dblp_paper(id),
    src      TEXT NOT NULL CHECK (src IN ('openalex', 's2')),
    ref_id   TEXT NOT NULL,
    doi      TEXT,
    dblp_key TEXT,
    arxiv_id TEXT,
    PRIMARY KEY (paper_id, src, ref_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS paper_reference_ref ON paper_reference (ref_id);

DROP VIEW IF EXISTS paper_card;
CREATE VIEW paper_card AS
SELECT p.id AS paper_id, p.dblp_key, p.title, p.year, p.venue, p.kind, p.doi,
       d.abstract, d.abstract_src, d.tldr, d.work_type, d.is_paratext, d.language,
       d.primary_topic, d.subfield, d.field,
       (SELECT group_concat(name, '; ') FROM paper_term t WHERE t.paper_id = p.id AND t.kind = 'keyword') AS keywords,
       (SELECT group_concat(name, '; ') FROM paper_term t WHERE t.paper_id = p.id AND t.kind = 'topic') AS topics,
       (SELECT group_concat(name, '; ') FROM paper_term t WHERE t.paper_id = p.id AND t.kind = 'field') AS fields,
       d.arxiv_id, d.oa_url, d.pdf_url, d.openalex_id, d.s2_id
FROM dblp_paper p JOIN paper_detail d ON d.paper_id = p.id;

CREATE TABLE IF NOT EXISTS review_claim (
    paper_id INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
    owner    TEXT NOT NULL,
    until    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_accepted (
    paper_id     INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
    role         TEXT NOT NULL CHECK (role IN ('baseline', 'benchmark', 'evaluation', 'survey')),
    p            REAL NOT NULL CHECK (p >= 0.5 AND p <= 1),
    agent        TEXT NOT NULL CHECK (agent = 'y'),
    offense      TEXT NOT NULL CHECK (offense = 'y'),
    black_box    TEXT NOT NULL CHECK (black_box IN ('y', 'p', 'n', 'u')),
    long_horizon TEXT NOT NULL CHECK (long_horizon IN ('y', 'p', 'n', 'u')),
    quote        TEXT NOT NULL,
    quote_src    TEXT NOT NULL CHECK (quote_src IN ('title', 'abstract', 'tldr')),
    reason       TEXT NOT NULL,
    by           TEXT NOT NULL,
    at           INTEGER NOT NULL,
    CHECK (role <> 'baseline' OR (black_box <> 'n' AND long_horizon <> 'n'))
);

CREATE TABLE IF NOT EXISTS paper_rejected (
    paper_id     INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
    reject_class TEXT NOT NULL CHECK (reject_class IN ('off_domain', 'security_other', 'defensive_only', 'vuln_detection_or_code_scan', 'generic_llm_security', 'static_qa_benchmark', 'llm_no_offense', 'frontmatter')),
    p            REAL NOT NULL CHECK (p >= 0 AND p < 0.5),
    reason       TEXT NOT NULL,
    by           TEXT NOT NULL,
    at           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS paper_rejected_class ON paper_rejected (reject_class);

CREATE TRIGGER IF NOT EXISTS paper_accepted_not_rejected BEFORE INSERT ON paper_accepted
WHEN EXISTS (SELECT 1 FROM paper_rejected WHERE paper_id = NEW.paper_id)
BEGIN SELECT RAISE(ABORT, 'paper is already rejected'); END;
CREATE TRIGGER IF NOT EXISTS paper_rejected_not_accepted BEFORE INSERT ON paper_rejected
WHEN EXISTS (SELECT 1 FROM paper_accepted WHERE paper_id = NEW.paper_id)
BEGIN SELECT RAISE(ABORT, 'paper is already accepted'); END;
"""

REBUILT_EVIDENCE = {"conf_dblp_booktitle": "first_year", "conf_dblp_title": "proc_key"}


def migrate(conn: sqlite3.Connection) -> None:
    tables = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'"))
    stale = [t for t, col in REBUILT_EVIDENCE.items()
             if t in tables and col not in {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}]
    venue_stale = "conf_dblp_venue" in tables and "journals/" not in tables["conf_dblp_venue"]
    if not stale and not venue_stale:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    for (view,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
        conn.execute(f"DROP VIEW {view}")
    for t in stale:
        conn.execute(f"DROP TABLE {t}")
    if venue_stale:
        conn.execute("""CREATE TABLE conf_dblp_venue_new (
            dblp_key   TEXT PRIMARY KEY CHECK (dblp_key LIKE 'conf/%' OR dblp_key LIKE 'journals/%'),
            acronym    TEXT NOT NULL,
            papers     INTEGER NOT NULL,
            first_year INTEGER,
            last_year  INTEGER
        ) WITHOUT ROWID""")
        conn.execute("INSERT INTO conf_dblp_venue_new SELECT dblp_key, substr(dblp_key, instr(dblp_key, '/') + 1), "
                     "papers, first_year, last_year FROM conf_dblp_venue")
        conn.execute("DROP TABLE conf_dblp_venue")
        conn.execute("ALTER TABLE conf_dblp_venue_new RENAME TO conf_dblp_venue")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    print(f"migrated: rebuilt {', '.join(stale + ['conf_dblp_venue'] * venue_stale)}; "
          "run conference-hunter/a_extract.py to refill the DBLP evidence")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    conn = sqlite3.connect(path)
    migrate(conn)
    conn.executescript(SCHEMA)
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        sys.exit(f"foreign key violations after migration: {broken[:10]}")
    counts = dict(conn.execute(
        "SELECT type, count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' GROUP BY type"))
    conn.close()
    print(f"{path}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
