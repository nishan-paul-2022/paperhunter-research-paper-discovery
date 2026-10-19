"""Database storage layer.

Storage only. Decision policy is in h_flow. Everything lives in archive/paperhunter.db,
next to the venue lists and dblp_paper, so papers, content and judgements join directly.

Tables (paper ids are dblp_paper.id, stable across re-extraction):
  paper_abstract, paper_fulltext, paper_fetch   content per paper, fetched once, shared by all runs
  hunt_run, hunt_run_paper                       a run adds one paper_inventory slice to the hunt
  hunt_paper                                     papers in the hunt; dup_of links title duplicates
  hunt_triage, hunt_task, hunt_review, hunt_result, hunt_meta   the review workflow
Views:
  hunt_item        a hunt paper with its title, DOI and link derived from dblp_paper
  reference_paper  the output: accepted papers with their DBLP record, rank and role

Design principles:
  * One fact, one place. Bibliographic data stays in dblp_paper; nothing is copied
  * Sparse tables for sparse facts (most papers never reviewed)
  * Append-only judgements. Reviews never updated
  * Parallel by construction. Leased work queue for concurrent workers
  * Integrity at schema level. CHECK and FOREIGN KEY constraints

Caller rules:
  * Never load whole tables; use iter_papers() and keyed accessors
  * All writes through tx(conn) (short transactions, BEGIN IMMEDIATE)
"""

import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 5
RULES_VERSION = 1
LEASE_SECONDS = 900
MAX_TRIES = 3
MIN_ABSTRACT = 120

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
DB_PATH = ROOT / "archive" / "paperhunter.db"

# A run is a slice of paper_inventory: "<rank>-<year>" (a-star-2025, q1-2024, ...) or "no-doi".
RUN_RE = re.compile(r"^(a-star|a|b|c|q[1-4])-(\d{4})$")

# paper_fetch.tried bitmask
TRIED_OPENALEX, TRIED_S2, TRIED_CROSSREF, TRIED_LANDING, TRIED_ARXIV = 1, 2, 4, 8, 16

DDL = """
CREATE TABLE IF NOT EXISTS hunt_meta(k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID;

-- ---------------------------------------------------------------- content per paper
CREATE TABLE IF NOT EXISTS paper_abstract(
  paper_id INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
  text TEXT NOT NULL,
  src  TEXT NOT NULL CHECK(src IN ('openalex','s2','crossref','landing','arxiv','web')),
  url  TEXT,                                   -- where a web abstract was read
  at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fulltext(
  paper_id INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
  text TEXT NOT NULL CHECK(length(text) >= 3000),
  src  TEXT NOT NULL,
  at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fetch(
  paper_id INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
  tried    INTEGER NOT NULL DEFAULT 0,          -- TRIED_* bits
  pdf      TEXT,
  arxiv    TEXT
);

-- ---------------------------------------------------------------- hunt membership
CREATE TABLE IF NOT EXISTS hunt_run(
  name            TEXT PRIMARY KEY,             -- a paper_inventory slice or no-doi
  created_at      INTEGER NOT NULL,
  n_papers        INTEGER NOT NULL,
  deep_fetch_done INTEGER NOT NULL DEFAULT 0 CHECK(deep_fetch_done IN (0,1))
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hunt_paper(
  paper_id   INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
  norm_title TEXT NOT NULL,
  dup_of     INTEGER REFERENCES hunt_paper(paper_id),
  added_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS hunt_paper_title ON hunt_paper(norm_title);
CREATE TABLE IF NOT EXISTS hunt_run_paper(
  run      TEXT NOT NULL REFERENCES hunt_run(name),
  paper_id INTEGER NOT NULL REFERENCES hunt_paper(paper_id),
  PRIMARY KEY(run, paper_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS hunt_run_paper_paper ON hunt_run_paper(paper_id);

-- ---------------------------------------------------------------- review workflow
CREATE TABLE IF NOT EXISTS hunt_triage(
  paper_id INTEGER PRIMARY KEY REFERENCES hunt_paper(paper_id),
  tier  INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 3),
  cues  TEXT NOT NULL,
  prior TEXT,
  ha    INTEGER NOT NULL,                       -- 1 if triaged with an abstract
  rv    INTEGER NOT NULL                        -- RULES_VERSION used
);
CREATE INDEX IF NOT EXISTS hunt_triage_hot ON hunt_triage(tier) WHERE tier >= 1;

CREATE TABLE IF NOT EXISTS hunt_task(
  paper_id INTEGER NOT NULL REFERENCES hunt_paper(paper_id),
  stage TEXT NOT NULL CHECK(stage IN ('review','blind','final')),
  state TEXT NOT NULL DEFAULT 'todo' CHECK(state IN ('todo','leased','done','failed')),
  prio  INTEGER NOT NULL DEFAULT 5,
  owner TEXT,
  until INTEGER,
  tries INTEGER NOT NULL DEFAULT 0,
  why   TEXT NOT NULL,
  PRIMARY KEY(paper_id, stage)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS hunt_task_open ON hunt_task(stage, prio, paper_id) WHERE state IN ('todo','leased');

CREATE TABLE IF NOT EXISTS hunt_review(
  id      INTEGER PRIMARY KEY,
  paper_id INTEGER NOT NULL REFERENCES hunt_paper(paper_id),
  stage   TEXT NOT NULL CHECK(stage IN ('review','blind','final')),
  by      TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('accept','reject')),
  p       REAL NOT NULL CHECK(p BETWEEN 0 AND 1),
  role    TEXT CHECK(role IN ('baseline','benchmark','evaluation','survey')),
  rclass  TEXT CHECK(rclass IN ('off_domain','security_other','defensive_only','vuln_detection_or_code_scan',
                                'generic_llm_security','static_qa_benchmark','llm_no_offense','frontmatter')),
  c_agent TEXT CHECK(c_agent IN ('y','p','n','u')),
  c_off   TEXT CHECK(c_off   IN ('y','p','n','u')),
  c_bb    TEXT CHECK(c_bb    IN ('y','p','n','u')),
  c_lh    TEXT CHECK(c_lh    IN ('y','p','n','u')),
  lvl     TEXT NOT NULL CHECK(lvl  IN ('title','abstract','fulltext')),
  qsrc    TEXT CHECK(qsrc IN ('title','abstract','fulltext')),
  quote   TEXT,
  vs_prior TEXT CHECK(vs_prior IN ('agree','override')),
  reason  TEXT NOT NULL,
  at      INTEGER NOT NULL,
  CHECK((verdict='accept' AND role IS NOT NULL AND rclass IS NULL AND p >= 0.5)
     OR (verdict='reject' AND role IS NULL     AND rclass IS NOT NULL AND p <  0.5))
);
CREATE INDEX IF NOT EXISTS hunt_review_paper ON hunt_review(paper_id, stage);

CREATE TABLE IF NOT EXISTS hunt_result(
  paper_id INTEGER PRIMARY KEY REFERENCES hunt_paper(paper_id),
  verdict TEXT NOT NULL CHECK(verdict IN ('accept','reject')),
  role    TEXT CHECK(role IN ('baseline','benchmark','evaluation','survey')),
  p       REAL NOT NULL,
  by      TEXT NOT NULL CHECK(by IN ('review','consensus','final','fallback')),
  at      INTEGER NOT NULL
);

-- ---------------------------------------------------------------- views
-- A hunt paper as reviewers see it: title without a trailing period, DOI, and a link
-- only when it adds something beyond the DOI.
DROP VIEW IF EXISTS hunt_item;
CREATE VIEW hunt_item AS
SELECT h.paper_id AS id, d.dblp_key,
       CASE WHEN d.title LIKE '%.' AND d.title NOT LIKE '%..'
            THEN substr(d.title, 1, length(d.title) - 1) ELSE d.title END AS title,
       d.doi,
       CASE WHEN d.ee LIKE 'http%'
             AND (d.doi IS NULL OR lower(rtrim(d.ee, '/')) <> 'https://doi.org/' || d.doi)
            THEN d.ee END AS link,
       h.dup_of
FROM hunt_paper h JOIN dblp_paper d ON d.id = h.paper_id;

-- The output of the hunt: accepted canonical papers with their full DBLP record.
DROP VIEW IF EXISTS reference_paper;
CREATE VIEW reference_paper AS
SELECT d.dblp_key, d.title, d.authors, d.year, d.venue, d.venue_key, d.kind, i.rank,
       d.ee, d.url, d.doi, r.role, r.p, r.by AS decided_by, r.at AS decided_at, d.id AS paper_id
FROM hunt_result r
JOIN hunt_paper h ON h.paper_id = r.paper_id AND h.dup_of IS NULL
JOIN dblp_paper d ON d.id = r.paper_id
LEFT JOIN paper_inventory i ON i.dblp_key = d.dblp_key
WHERE r.verdict = 'accept'
ORDER BY CASE r.role WHEN 'baseline' THEN 0 WHEN 'benchmark' THEN 1 WHEN 'evaluation' THEN 2 ELSE 3 END,
         r.p DESC, d.dblp_key;
"""

REVIEW_COLS = (
    "paper_id", "stage", "by", "verdict", "p", "role", "rclass", "c_agent", "c_off",
    "c_bb", "c_lh", "lvl", "qsrc", "quote", "vs_prior", "reason", "at",
)


def now():
    return int(time.time())


# --------------------------------------------------------------------------- text helpers

def norm_title(s):
    """Normalised title: the dedup key (content only)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def clean_title(t):
    t = re.sub(r"\s+", " ", (t or "").strip())
    return t[:-1] if t.endswith(".") and not t.endswith("..") else t


_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


def extract_doi(*values):
    for v in values:
        m = _DOI_RE.search(v or "")
        if m:
            return m.group(0).rstrip(".,;)").lower()
    return None


# --------------------------------------------------------------------------- connection

def open_db(path=DB_PATH, readonly=False):
    """Open the database and set pragmas; creates or upgrades the hunt schema when needed."""
    path = str(path)
    if not Path(path).exists():
        raise SystemExit("ERROR: database not found: %s (run the venue pipeline and run/d_extract.py first)" % path)
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, isolation_level=None,
                               timeout=60, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, isolation_level=None, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='paper_inventory'").fetchone():
        raise SystemExit("ERROR: %s has no paper_inventory (run run/d_extract.py first)" % path)
    if not readonly and schema_version(conn) != SCHEMA_VERSION:
        create_schema(conn)
    return conn


def schema_version(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='hunt_meta'").fetchone():
        return 0
    return int(meta_get(conn, "schema", 0))


def create_schema(conn):
    conn.executescript(DDL)  # every statement is idempotent (IF NOT EXISTS / DROP VIEW IF EXISTS)
    meta_set(conn, "schema", SCHEMA_VERSION)


@contextmanager
def tx(conn):
    """Short write transaction. Re-entrant: joins an open transaction."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def meta_get(conn, k, default=None):
    r = conn.execute("SELECT v FROM hunt_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(conn, k, v):
    with tx(conn):
        conn.execute(
            "INSERT INTO hunt_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, str(v)),
        )


# --------------------------------------------------------------------------- runs

def slice_papers(conn, run):
    """[(paper_id, title)] of a run's slice of paper_inventory, in DBLP order."""
    if RUN_RE.match(run):
        where, params = "i.slice = ?", (run,)
    elif run == "no-doi":
        where, params = "i.doi IS NULL", ()
    else:
        raise SystemExit("ERROR: unknown run '%s'; use <rank>-<year> (a-star-2025, q1-2024, ...) or no-doi" % run)
    return conn.execute("SELECT i.id, i.title FROM paper_inventory i WHERE " + where + " ORDER BY i.id",
                        params).fetchall()


def add_run(conn, run):
    """Add the papers of a run's slice to the hunt (idempotent).

    New papers get a normalised title; a paper whose title matches a paper already in the
    hunt (or an earlier paper of this slice) becomes its duplicate and is never reviewed.
    Returns {"run", "papers", "added", "duplicates"}.
    """
    papers = slice_papers(conn, run)
    if not papers:
        raise SystemExit("ERROR: run '%s' has no papers in paper_inventory" % run)
    t = now()
    added = dups = 0
    with tx(conn):
        conn.execute("INSERT INTO hunt_run(name, created_at, n_papers) VALUES(?,?,?) "
                     "ON CONFLICT(name) DO UPDATE SET n_papers=excluded.n_papers", (run, t, len(papers)))
        have = {r[0] for r in conn.execute(
            "SELECT h.paper_id FROM hunt_paper h JOIN hunt_run_paper rp ON rp.paper_id=h.paper_id "
            "WHERE rp.run=?", (run,))}
        canon = {}
        for pid, title in papers:
            if pid in have:
                continue
            exists = conn.execute("SELECT 1 FROM hunt_paper WHERE paper_id=?", (pid,)).fetchone()
            if not exists:
                key = norm_title(clean_title(title))
                if key not in canon:
                    r = conn.execute("SELECT paper_id FROM hunt_paper WHERE norm_title=? AND dup_of IS NULL "
                                     "ORDER BY paper_id LIMIT 1", (key,)).fetchone()
                    canon[key] = r[0] if r else None
                dup = canon[key]
                conn.execute("INSERT INTO hunt_paper(paper_id, norm_title, dup_of, added_at) VALUES(?,?,?,?)",
                             (pid, key, dup, t))
                if dup is None:
                    canon[key] = pid
                else:
                    dups += 1
                added += 1
            conn.execute("INSERT OR IGNORE INTO hunt_run_paper(run, paper_id) VALUES(?,?)", (run, pid))
    return {"run": run, "papers": len(papers), "added": added, "duplicates": dups}


def runs(conn):
    """[dict(name, n_papers, deep_fetch_done, created_at)] in creation order."""
    return [dict(r) for r in conn.execute("SELECT * FROM hunt_run ORDER BY created_at, name")]


def mark_deep_fetch_done(conn):
    """A complete deep-fetch pass covers every paper in the hunt, so every run is done."""
    with tx(conn):
        conn.execute("UPDATE hunt_run SET deep_fetch_done=1")


# --------------------------------------------------------------------------- paper access

def count_papers(conn, canonical=True):
    q = "SELECT COUNT(*) FROM hunt_paper" + (" WHERE dup_of IS NULL" if canonical else "")
    return conn.execute(q).fetchone()[0]


def iter_papers(conn, chunk=5000, canonical=True, where="", params=()):
    """Stream canonical papers as lists of dict(id,title,abstract,abstract_src,ha,tier,rv).

    `where` is an extra SQL condition over aliases p (hunt_item), a (paper_abstract), t (hunt_triage).
    """
    last = 0
    cond = ["p.id > ?"]
    if canonical:
        cond.append("p.dup_of IS NULL")
    if where:
        cond.append("(%s)" % where)
    sql = (
        "SELECT p.id, p.title, a.text AS abstract, a.src AS abstract_src, "
        "t.tier AS tier, t.ha AS ha, t.rv AS rv "
        "FROM hunt_item p LEFT JOIN paper_abstract a ON a.paper_id=p.id "
        "LEFT JOIN hunt_triage t ON t.paper_id=p.id "
        "WHERE " + " AND ".join(cond) + " ORDER BY p.id LIMIT ?"
    )
    while True:
        rows = conn.execute(sql, (last,) + tuple(params) + (chunk,)).fetchall()
        if not rows:
            return
        yield [dict(r) for r in rows]
        last = rows[-1]["id"]


def get_paper(conn, pid):
    r = conn.execute("SELECT * FROM hunt_item WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def dossiers(conn, ids, *, blind=False, with_reviews=False):
    """Content-only dossiers for reviewers.

    Keys: id, dblp_key, title, abstract, abstract_src, has_fulltext, handle (retrieval handle,
    never a decision input), and (unless blind) tier, cues, prior. with_reviews adds `reviews`.
    """
    out = []
    for pid in ids:
        r = conn.execute(
            "SELECT p.id, p.dblp_key, p.title, p.doi, p.link, a.text AS abstract, a.src AS abstract_src, "
            "EXISTS(SELECT 1 FROM paper_fulltext f WHERE f.paper_id=p.id) AS has_ft, "
            "t.tier, t.cues, t.prior "
            "FROM hunt_item p LEFT JOIN paper_abstract a ON a.paper_id=p.id "
            "LEFT JOIN hunt_triage t ON t.paper_id=p.id WHERE p.id=?", (pid,)).fetchone()
        if not r:
            continue
        handle = r["link"] or (("https://doi.org/" + r["doi"]) if r["doi"] else None)
        d = {
            "id": r["id"], "dblp_key": r["dblp_key"], "title": r["title"], "abstract": r["abstract"],
            "abstract_src": r["abstract_src"], "has_fulltext": bool(r["has_ft"]),
            "handle": handle,
        }
        if not blind:
            d.update(tier=r["tier"], cues=r["cues"] or "", prior=r["prior"])
        if with_reviews:
            d["reviews"] = reviews_for(conn, pid)
        out.append(d)
    return out


# --------------------------------------------------------------------------- content

def put_abstract(conn, pid, text, src, replace=False, url=None):
    """Store an abstract. Returns True if stored. Existing abstracts are kept unless replace."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return False
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with tx(conn):
        cur = conn.execute(verb + " INTO paper_abstract(paper_id,text,src,url,at) VALUES(?,?,?,?,?)",
                           (pid, text, src, url, now()))
    return cur.rowcount > 0


def put_abstracts(conn, items, replace=False):
    """Bulk variant. items: iterable of (paper_id, text, src). Returns rows stored."""
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    t = now()
    rows = [(pid, re.sub(r"\s+", " ", txt or "").strip(), src, t) for pid, txt, src in items]
    rows = [r for r in rows if r[1]]
    n = 0
    with tx(conn):
        for i in range(0, len(rows), 500):
            cur = conn.executemany(verb + " INTO paper_abstract(paper_id,text,src,at) VALUES(?,?,?,?)",
                                   rows[i:i + 500])
            n += max(cur.rowcount, 0)
    return n


def get_abstract(conn, pid):
    r = conn.execute("SELECT text, src FROM paper_abstract WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else None


def put_fulltext(conn, pid, text, src):
    with tx(conn):
        conn.execute("INSERT OR REPLACE INTO paper_fulltext(paper_id,text,src,at) VALUES(?,?,?,?)",
                     (pid, text, src, now()))


def get_fulltext(conn, pid):
    r = conn.execute("SELECT text, src FROM paper_fulltext WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else None


def get_fetch_state(conn, pid):
    r = conn.execute("SELECT tried, pdf, arxiv FROM paper_fetch WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else {"tried": 0, "pdf": None, "arxiv": None}


def mark_fetched(conn, items):
    """items: iterable of (paper_id, tried_bits_to_or, pdf_or_None, arxiv_or_None)."""
    with tx(conn):
        for pid, bits, pdf, arx in items:
            conn.execute(
                "INSERT INTO paper_fetch(paper_id,tried,pdf,arxiv) VALUES(?,?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET tried=tried|excluded.tried, "
                "pdf=COALESCE(excluded.pdf,pdf), arxiv=COALESCE(excluded.arxiv,arxiv)",
                (pid, bits, pdf, arx))


def fetch_candidates(conn, *, deep, min_tier=1):
    """Canonical hunt papers with no abstract.

    deep=False -> papers with a DOI whose `tried` lacks both OpenAlex and S2 bits.
    deep=True  -> papers whose triage tier >= min_tier (title cues) and that lack at least one
                  of the deep-provider bits (crossref, landing, arxiv, or openalex title search);
                  the caller decides per provider (e.g. arXiv only for tier >= 2).
    Returns list of dict(id,title,doi,link,tried,tier,pdf,arxiv).
    """
    if deep:
        sql = (
            "SELECT p.id,p.title,p.doi,p.link,COALESCE(f.tried,0) AS tried,t.tier AS tier,f.pdf,f.arxiv "
            "FROM hunt_item p JOIN hunt_triage t ON t.paper_id=p.id "
            "LEFT JOIN paper_fetch f ON f.paper_id=p.id "
            "WHERE p.dup_of IS NULL AND t.tier>=? "
            "AND NOT EXISTS(SELECT 1 FROM paper_abstract a WHERE a.paper_id=p.id)"
        )
        rows = conn.execute(sql, (min_tier,)).fetchall()
    else:
        sql = (
            "SELECT p.id,p.title,p.doi,p.link,COALESCE(f.tried,0) AS tried,NULL AS tier,f.pdf,f.arxiv "
            "FROM hunt_item p LEFT JOIN paper_fetch f ON f.paper_id=p.id "
            "WHERE p.dup_of IS NULL AND p.doi IS NOT NULL AND (COALESCE(f.tried,0) & 3)=0 "
            "AND NOT EXISTS(SELECT 1 FROM paper_abstract a WHERE a.paper_id=p.id)"
        )
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- triage

def upsert_triage(conn, rows):
    """rows: iterable of (paper_id, tier, cues, prior, ha, rv)."""
    rows = list(rows)
    with tx(conn):
        for i in range(0, len(rows), 500):
            conn.executemany(
                "INSERT INTO hunt_triage(paper_id,tier,cues,prior,ha,rv) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET tier=excluded.tier, cues=excluded.cues, "
                "prior=excluded.prior, ha=excluded.ha, rv=excluded.rv",
                rows[i:i + 500])


# --------------------------------------------------------------------------- tasks

def add_task(conn, pid, stage, prio, why):
    """INSERT OR IGNORE. Returns True when a new task row was created."""
    with tx(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO hunt_task(paper_id,stage,state,prio,why) VALUES(?,?, 'todo', ?, ?)",
            (pid, stage, prio, why))
    return cur.rowcount > 0


def get_task(conn, pid, stage):
    r = conn.execute("SELECT * FROM hunt_task WHERE paper_id=? AND stage=?", (pid, stage)).fetchone()
    return dict(r) if r else None


def claim(conn, stage, owner, n=8, now_ts=None, needs_web=None):
    """Atomically lease up to n tasks of `stage` for `owner`. Returns list of paper ids.

    blind tasks are never given to an owner that already has a review row on the paper.
    needs_web: None = any, 1 = only papers without an abstract, 0 = only papers with one.
    """
    t = now_ts if now_ts is not None else now()
    extra = ""
    if needs_web == 1:
        extra = " AND NOT EXISTS (SELECT 1 FROM paper_abstract a WHERE a.paper_id=t.paper_id)"
    elif needs_web == 0:
        extra = " AND EXISTS (SELECT 1 FROM paper_abstract a WHERE a.paper_id=t.paper_id)"
    sql = (
        "UPDATE hunt_task SET state='leased', owner=:w, until=:until, tries=tries+1 "
        "WHERE stage=:stage AND paper_id IN ("
        " SELECT paper_id FROM hunt_task t WHERE stage=:stage "
        "  AND (state='todo' OR (state='leased' AND until<:now)) AND tries<:maxt "
        "  AND (:stage<>'blind' OR NOT EXISTS "
        "       (SELECT 1 FROM hunt_review r WHERE r.paper_id=t.paper_id AND r.by=:w))" + extra +
        " ORDER BY prio, paper_id LIMIT :n) "
        "RETURNING paper_id, prio"
    )
    with tx(conn):
        rows = conn.execute(sql, {"w": owner, "until": t + LEASE_SECONDS, "stage": stage,
                                  "now": t, "maxt": MAX_TRIES, "n": n}).fetchall()
    return [r[0] for r in sorted(rows, key=lambda r: (r[1], r[0]))]


def finish_task(conn, pid, stage, owner):
    """Mark a leased task done if `owner` holds the lease. Returns True on success."""
    cur = conn.execute(
        "UPDATE hunt_task SET state='done' WHERE paper_id=? AND stage=? AND owner=? AND state='leased'",
        (pid, stage, owner))
    return cur.rowcount == 1


def close_task(conn, pid, stage, state="done"):
    """Unconditional close (used by advance for fallbacks)."""
    conn.execute("UPDATE hunt_task SET state=? WHERE paper_id=? AND stage=?", (state, pid, stage))


def fail_exhausted(conn, now_ts=None):
    """Mark leased tasks that used all tries and whose lease expired as failed. Returns [(pid, stage)]."""
    t = now_ts if now_ts is not None else now()
    with tx(conn):
        rows = conn.execute(
            "UPDATE hunt_task SET state='failed' WHERE state='leased' AND tries>=? AND until<? "
            "RETURNING paper_id, stage", (MAX_TRIES, t)).fetchall()
    return [(r[0], r[1]) for r in rows]


def task_counts(conn):
    """{stage: {state: n}}"""
    out = {}
    for stage, state, n in conn.execute("SELECT stage, state, COUNT(*) FROM hunt_task GROUP BY 1,2"):
        out.setdefault(stage, {})[state] = n
    return out


def open_task_ids(conn, stage):
    return [r[0] for r in conn.execute(
        "SELECT paper_id FROM hunt_task WHERE stage=? AND state IN ('todo','leased') ORDER BY prio, paper_id",
        (stage,))]


# --------------------------------------------------------------------------- reviews and results

def add_review(conn, rec):
    """Insert a review row. `rec` has the REVIEW_COLS keys (at defaults to now). Returns row id."""
    row = {k: rec.get(k) for k in REVIEW_COLS}
    if row["at"] is None:
        row["at"] = now()
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO hunt_review(%s) VALUES(%s)" % (",".join(REVIEW_COLS), ",".join("?" * len(REVIEW_COLS))),
            [row[k] for k in REVIEW_COLS])
    return cur.lastrowid


def reviews_for(conn, pid, stage=None):
    q = "SELECT * FROM hunt_review WHERE paper_id=?"
    args = [pid]
    if stage:
        q += " AND stage=?"
        args.append(stage)
    return [dict(r) for r in conn.execute(q + " ORDER BY id", args)]


def latest_review(conn, pid, stage):
    r = conn.execute("SELECT * FROM hunt_review WHERE paper_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                     (pid, stage)).fetchone()
    return dict(r) if r else None


def upsert_result(conn, pid, verdict, role, p, by):
    with tx(conn):
        conn.execute(
            "INSERT INTO hunt_result(paper_id,verdict,role,p,by,at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(paper_id) DO UPDATE SET verdict=excluded.verdict, role=excluded.role, "
            "p=excluded.p, by=excluded.by, at=excluded.at",
            (pid, verdict, role, p, by, now()))


def get_result(conn, pid):
    r = conn.execute("SELECT * FROM hunt_result WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else None


def reference_papers(conn):
    """Rows of the reference_paper view (the hunt's output), as dicts."""
    return [dict(r) for r in conn.execute("SELECT * FROM reference_paper")]


def tier_histogram(conn):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT t.tier, COUNT(*) FROM hunt_triage t JOIN hunt_paper p ON p.paper_id=t.paper_id "
        "WHERE p.dup_of IS NULL GROUP BY 1 ORDER BY 1")}
