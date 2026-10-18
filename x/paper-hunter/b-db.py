"""Database storage layer.

Storage only. Decision policy is in h_flow. Schema is normalized with sparse tables
for content (abstract, fulltext) and work (task queue, reviews, results).

Design principles:
  * One fact, one place. No blobs repeating columns
  * Sparse tables for sparse facts (most papers never reviewed)
  * Append-only judgements. Reviews never updated
  * Parallel by construction. Leased work queue for concurrent workers
  * Integrity at schema level. CHECK and FOREIGN KEY constraints

Caller rules:
  * Never load whole tables; use iter_papers() and keyed accessors
  * All writes through tx(conn) (short transactions, BEGIN IMMEDIATE)
  * paper.id is the 1-based CSV row ordinal
"""

import csv
import hashlib
import io
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3
RULES_VERSION = 1
LEASE_SECONDS = 900
MAX_TRIES = 3
MIN_ABSTRACT = 120

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
SUPPLEMENT_DIR = ROOT / "paper-supplement"
REFERENCE_DIR = ROOT / "paper-reference"

# fetch_state.tried bitmask
TRIED_OPENALEX, TRIED_S2, TRIED_CROSSREF, TRIED_LANDING, TRIED_ARXIV = 1, 2, 4, 8, 16

DDL = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS paper(
  id     INTEGER PRIMARY KEY,
  title  TEXT NOT NULL,
  doi    TEXT,
  link   TEXT,
  dup_of INTEGER REFERENCES paper(id)
);

CREATE TABLE IF NOT EXISTS abstract(
  paper_id INTEGER PRIMARY KEY REFERENCES paper(id),
  text TEXT NOT NULL,
  src  TEXT NOT NULL CHECK(src IN ('openalex','s2','crossref','landing','arxiv','web','legacy')),
  at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fulltext(
  paper_id INTEGER PRIMARY KEY REFERENCES paper(id),
  text TEXT NOT NULL CHECK(length(text) >= 3000),
  src  TEXT NOT NULL,
  at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fetch_state(
  paper_id INTEGER PRIMARY KEY REFERENCES paper(id),
  tried    INTEGER NOT NULL DEFAULT 0,
  pdf      TEXT,
  arxiv    TEXT
);

CREATE TABLE IF NOT EXISTS triage(
  paper_id INTEGER PRIMARY KEY REFERENCES paper(id),
  tier  INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 3),
  cues  TEXT NOT NULL,
  prior TEXT,
  ha    INTEGER NOT NULL,
  rv    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS triage_hot ON triage(tier) WHERE tier >= 1;

CREATE TABLE IF NOT EXISTS task(
  paper_id INTEGER NOT NULL REFERENCES paper(id),
  stage TEXT NOT NULL CHECK(stage IN ('review','blind','final')),
  state TEXT NOT NULL DEFAULT 'todo' CHECK(state IN ('todo','leased','done','failed')),
  prio  INTEGER NOT NULL DEFAULT 5,
  owner TEXT,
  until INTEGER,
  tries INTEGER NOT NULL DEFAULT 0,
  why   TEXT NOT NULL,
  PRIMARY KEY(paper_id, stage)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS task_open ON task(stage, prio, paper_id) WHERE state IN ('todo','leased');

CREATE TABLE IF NOT EXISTS review(
  id      INTEGER PRIMARY KEY,
  paper_id INTEGER NOT NULL REFERENCES paper(id),
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
CREATE INDEX IF NOT EXISTS review_paper ON review(paper_id, stage);

CREATE TABLE IF NOT EXISTS result(
  paper_id INTEGER PRIMARY KEY REFERENCES paper(id),
  verdict TEXT NOT NULL CHECK(verdict IN ('accept','reject')),
  role    TEXT CHECK(role IN ('baseline','benchmark','evaluation','survey')),
  p       REAL NOT NULL,
  by      TEXT NOT NULL CHECK(by IN ('review','consensus','final','fallback')),
  at      INTEGER NOT NULL
);

CREATE VIEW IF NOT EXISTS v_verdict AS
  SELECT p.id, p.title, COALESCE(r.verdict,'reject') AS verdict, r.role, COALESCE(r.by,'rule') AS decided_by, t.tier
  FROM paper p LEFT JOIN result r ON r.paper_id=p.id LEFT JOIN triage t ON t.paper_id=p.id
  WHERE p.dup_of IS NULL;
CREATE VIEW IF NOT EXISTS v_audit AS
  SELECT v.id, v.title, v.verdict, v.role, v.decided_by, v.tier, a.text AS abstract, a.src AS abstract_src
  FROM v_verdict v LEFT JOIN abstract a ON a.paper_id=v.id;
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

def open_db(path, create=False, readonly=False):
    """Open database. Sets pragmas; creates schema when create=True."""
    path = str(path)
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, isolation_level=None,
                               timeout=60, check_same_thread=False)
    else:
        if not create and not os.path.exists(path):
            raise FileNotFoundError("database not found: %s (run `a_cli.py init`)" % path)
        conn = sqlite3.connect(path, isolation_level=None, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")
    if create and not readonly:
        create_schema(conn)
    return conn


def create_schema(conn):
    conn.executescript(DDL)
    conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    meta_set(conn, "schema", str(SCHEMA_VERSION))


def user_version(path):
    c = sqlite3.connect("file:%s?mode=ro" % str(path), uri=True)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


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
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(conn, k, v):
    with tx(conn):
        conn.execute(
            "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, str(v)),
        )


# --------------------------------------------------------------------------- ingest

def run_paths(csv_path=None):
    """Resolve (csv_path, db_path) from an explicit CSV or the .current file."""
    if not csv_path:
        cur = TOOLS_DIR / ".current"
        if not cur.exists():
            raise SystemExit("ERROR: no CSV given and paper-hunter/.current does not exist")
        csv_path = cur.read_text().strip()
    src = Path(csv_path).expanduser()
    return src, SUPPLEMENT_DIR / ("%s.db" % src.stem)


def init_from_csv(csv_path, db_path, handle_cols=("ee", "url", "doi"), fresh=False):
    """Parse CSV and create database.

    Only title and handle columns are read. Every other column is pass-through.
    Returns stats dict. Raises FileExistsError if database exists unless fresh=True.
    """
    csv_path, db_path = Path(csv_path), Path(db_path)
    if not csv_path.exists():
        raise SystemExit("ERROR: CSV not found: %s" % csv_path)
    raw = csv_path.read_bytes()
    sha1 = hashlib.sha1(raw).hexdigest()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    legacy_renamed = None
    if db_path.exists():
        ver = user_version(db_path)
        if ver >= SCHEMA_VERSION and not fresh:
            raise FileExistsError(str(db_path))
        if ver >= SCHEMA_VERSION and fresh:
            for suf in ("", "-wal", "-shm"):
                p = Path(str(db_path) + suf)
                if p.exists():
                    p.unlink()
        else:
            target = db_path.with_name(db_path.stem + ".legacy.db")
            n = 1
            while target.exists():
                n += 1
                target = db_path.with_name("%s.legacy%d.db" % (db_path.stem, n))
            db_path.rename(target)
            for suf in ("-wal", "-shm"):
                p = Path(str(db_path) + suf)
                if p.exists():
                    p.rename(Path(str(target) + suf))
            legacy_renamed = str(target)

    text = raw.decode("utf-8-sig", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames:
        raise SystemExit("ERROR: CSV has no header row")
    cols = {c.strip().lower(): c for c in rdr.fieldnames if c is not None}
    if "title" not in cols:
        raise SystemExit("ERROR: CSV needs a 'title' column; found %s" % list(cols))
    hcols = [cols[h.lower()] for h in handle_cols if h.lower() in cols]
    doi_col = cols.get("doi")

    rows, seen = [], {}
    n_data = 0
    for i, row in enumerate(rdr, 1):
        n_data = i
        title = clean_title(row.get(cols["title"]) or "")
        if not title:
            continue
        hv = [(row.get(c) or "").strip() for c in hcols]
        doi = extract_doi(*(([row.get(doi_col) or ""] if doi_col else []) + hv))
        link = next((v for v in hv if v.lower().startswith("http")), None)
        if link and doi and link.lower().rstrip("/") == ("https://doi.org/" + doi):
            link = None
        key = norm_title(title)
        dup = seen.get(key)
        if dup is None:
            seen[key] = i
        rows.append((i, title, doi, link, dup))

    conn = open_db(db_path, create=True)
    with tx(conn):
        conn.executemany("INSERT INTO paper(id,title,doi,link,dup_of) VALUES(?,?,?,?,NULL)",
                         [(r[0], r[1], r[2], r[3]) for r in rows])
        conn.executemany("UPDATE paper SET dup_of=? WHERE id=?",
                         [(r[4], r[0]) for r in rows if r[4] is not None])
    for k, v in (("csv_path", str(csv_path.resolve())), ("csv_sha1", sha1), ("n_rows", n_data),
                 ("rules_version", RULES_VERSION), ("created_at", now())):
        meta_set(conn, k, v)
    conn.close()
    return {
        "db": str(db_path), "rows": n_data, "papers": len(rows),
        "duplicates": sum(1 for r in rows if r[4] is not None),
        "with_doi": sum(1 for r in rows if r[2]),
        "legacy_renamed_to": legacy_renamed,
    }


def reuse_abstracts(conn, legacy_db):
    """Copy abstracts (>= MIN_ABSTRACT chars) from a legacy DB by normalised title."""
    lc = sqlite3.connect("file:%s?mode=ro" % str(legacy_db), uri=True)
    try:
        legacy = {}
        for title, ab in lc.execute("SELECT title, abstract FROM papers WHERE abstract IS NOT NULL"):
            if ab and len(ab.strip()) >= MIN_ABSTRACT:
                legacy.setdefault(norm_title(title), ab.strip())
    finally:
        lc.close()
    items = []
    for pid, title in conn.execute("SELECT id,title FROM paper WHERE dup_of IS NULL"):
        ab = legacy.get(norm_title(title))
        if ab:
            items.append((pid, re.sub(r"\s+", " ", ab), "legacy"))
    return put_abstracts(conn, items)


# --------------------------------------------------------------------------- paper access

def count_papers(conn, canonical=True):
    q = "SELECT COUNT(*) FROM paper" + (" WHERE dup_of IS NULL" if canonical else "")
    return conn.execute(q).fetchone()[0]


def iter_papers(conn, chunk=5000, canonical=True, where="", params=()):
    """Stream canonical papers as lists of dict(id,title,abstract,abstract_src,ha,tier,rv).

    `where` is an extra SQL condition over aliases p (paper), a (abstract), t (triage).
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
        "FROM paper p LEFT JOIN abstract a ON a.paper_id=p.id LEFT JOIN triage t ON t.paper_id=p.id "
        "WHERE " + " AND ".join(cond) + " ORDER BY p.id LIMIT ?"
    )
    while True:
        rows = conn.execute(sql, (last,) + tuple(params) + (chunk,)).fetchall()
        if not rows:
            return
        yield [dict(r) for r in rows]
        last = rows[-1]["id"]


def get_paper(conn, pid):
    r = conn.execute("SELECT * FROM paper WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def dossiers(conn, ids, *, blind=False, with_reviews=False):
    """Content-only dossiers for reviewers.

    Keys: id, title, abstract, abstract_src, has_fulltext, handle (retrieval handle, never
    a decision input), and (unless blind) tier, cues, prior. with_reviews adds `reviews`.
    """
    out = []
    for pid in ids:
        r = conn.execute(
            "SELECT p.id, p.title, p.doi, p.link, a.text AS abstract, a.src AS abstract_src, "
            "EXISTS(SELECT 1 FROM fulltext f WHERE f.paper_id=p.id) AS has_ft, "
            "t.tier, t.cues, t.prior "
            "FROM paper p LEFT JOIN abstract a ON a.paper_id=p.id LEFT JOIN triage t ON t.paper_id=p.id "
            "WHERE p.id=?", (pid,)).fetchone()
        if not r:
            continue
        handle = r["link"] or (("https://doi.org/" + r["doi"]) if r["doi"] else None)
        d = {
            "id": r["id"], "title": r["title"], "abstract": r["abstract"],
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

def put_abstract(conn, pid, text, src, replace=False):
    """Store an abstract. Returns True if stored. Existing abstracts are kept unless replace."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return False
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with tx(conn):
        cur = conn.execute(verb + " INTO abstract(paper_id,text,src,at) VALUES(?,?,?,?)",
                           (pid, text, src, now()))
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
            cur = conn.executemany(verb + " INTO abstract(paper_id,text,src,at) VALUES(?,?,?,?)",
                                   rows[i:i + 500])
            n += max(cur.rowcount, 0)
    return n


def get_abstract(conn, pid):
    r = conn.execute("SELECT text, src FROM abstract WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else None


def put_fulltext(conn, pid, text, src):
    with tx(conn):
        conn.execute("INSERT OR REPLACE INTO fulltext(paper_id,text,src,at) VALUES(?,?,?,?)",
                     (pid, text, src, now()))


def get_fulltext(conn, pid):
    r = conn.execute("SELECT text, src FROM fulltext WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else None


def get_fetch_state(conn, pid):
    r = conn.execute("SELECT tried, pdf, arxiv FROM fetch_state WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else {"tried": 0, "pdf": None, "arxiv": None}


def mark_fetched(conn, items):
    """items: iterable of (paper_id, tried_bits_to_or, pdf_or_None, arxiv_or_None)."""
    with tx(conn):
        for pid, bits, pdf, arx in items:
            conn.execute(
                "INSERT INTO fetch_state(paper_id,tried,pdf,arxiv) VALUES(?,?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET tried=tried|excluded.tried, "
                "pdf=COALESCE(excluded.pdf,pdf), arxiv=COALESCE(excluded.arxiv,arxiv)",
                (pid, bits, pdf, arx))


def fetch_candidates(conn, *, deep, min_tier=1):
    """Canonical papers with no abstract.

    deep=False -> papers with a DOI whose `tried` lacks both OpenAlex and S2 bits.
    deep=True  -> papers whose triage tier >= min_tier (title cues) and that lack at least one
                  of the deep-provider bits (crossref, landing, arxiv, or openalex title search);
                  the caller decides per provider (e.g. arXiv only for tier >= 2).
    Returns list of dict(id,title,doi,link,tried,tier,pdf,arxiv).
    """
    if deep:
        sql = (
            "SELECT p.id,p.title,p.doi,p.link,COALESCE(f.tried,0) AS tried,t.tier AS tier,f.pdf,f.arxiv "
            "FROM paper p JOIN triage t ON t.paper_id=p.id "
            "LEFT JOIN fetch_state f ON f.paper_id=p.id "
            "WHERE p.dup_of IS NULL AND t.tier>=? "
            "AND NOT EXISTS(SELECT 1 FROM abstract a WHERE a.paper_id=p.id)"
        )
        rows = conn.execute(sql, (min_tier,)).fetchall()
    else:
        sql = (
            "SELECT p.id,p.title,p.doi,p.link,COALESCE(f.tried,0) AS tried,NULL AS tier,f.pdf,f.arxiv "
            "FROM paper p LEFT JOIN fetch_state f ON f.paper_id=p.id "
            "WHERE p.dup_of IS NULL AND p.doi IS NOT NULL AND (COALESCE(f.tried,0) & 3)=0 "
            "AND NOT EXISTS(SELECT 1 FROM abstract a WHERE a.paper_id=p.id)"
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
                "INSERT INTO triage(paper_id,tier,cues,prior,ha,rv) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET tier=excluded.tier, cues=excluded.cues, "
                "prior=excluded.prior, ha=excluded.ha, rv=excluded.rv",
                rows[i:i + 500])


# --------------------------------------------------------------------------- tasks

def add_task(conn, pid, stage, prio, why):
    """INSERT OR IGNORE. Returns True when a new task row was created."""
    with tx(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO task(paper_id,stage,state,prio,why) VALUES(?,?, 'todo', ?, ?)",
            (pid, stage, prio, why))
    return cur.rowcount > 0


def get_task(conn, pid, stage):
    r = conn.execute("SELECT * FROM task WHERE paper_id=? AND stage=?", (pid, stage)).fetchone()
    return dict(r) if r else None


def claim(conn, stage, owner, n=8, now_ts=None, needs_web=None):
    """Atomically lease up to n tasks of `stage` for `owner`. Returns list of paper ids.

    blind tasks are never given to an owner that already has a review row on the paper.
    needs_web: None = any, 1 = only papers without an abstract, 0 = only papers with one.
    """
    t = now_ts if now_ts is not None else now()
    extra = ""
    if needs_web == 1:
        extra = " AND NOT EXISTS (SELECT 1 FROM abstract a WHERE a.paper_id=t.paper_id)"
    elif needs_web == 0:
        extra = " AND EXISTS (SELECT 1 FROM abstract a WHERE a.paper_id=t.paper_id)"
    sql = (
        "UPDATE task SET state='leased', owner=:w, until=:until, tries=tries+1 "
        "WHERE stage=:stage AND paper_id IN ("
        " SELECT paper_id FROM task t WHERE stage=:stage "
        "  AND (state='todo' OR (state='leased' AND until<:now)) AND tries<:maxt "
        "  AND (:stage<>'blind' OR NOT EXISTS "
        "       (SELECT 1 FROM review r WHERE r.paper_id=t.paper_id AND r.by=:w))" + extra +
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
        "UPDATE task SET state='done' WHERE paper_id=? AND stage=? AND owner=? AND state='leased'",
        (pid, stage, owner))
    return cur.rowcount == 1


def close_task(conn, pid, stage, state="done"):
    """Unconditional close (used by advance for fallbacks)."""
    conn.execute("UPDATE task SET state=? WHERE paper_id=? AND stage=?", (state, pid, stage))


def fail_exhausted(conn, now_ts=None):
    """Mark leased tasks that used all tries and whose lease expired as failed. Returns [(pid, stage)]."""
    t = now_ts if now_ts is not None else now()
    with tx(conn):
        rows = conn.execute(
            "UPDATE task SET state='failed' WHERE state='leased' AND tries>=? AND until<? "
            "RETURNING paper_id, stage", (MAX_TRIES, t)).fetchall()
    return [(r[0], r[1]) for r in rows]


def task_counts(conn):
    """{stage: {state: n}}"""
    out = {}
    for stage, state, n in conn.execute("SELECT stage, state, COUNT(*) FROM task GROUP BY 1,2"):
        out.setdefault(stage, {})[state] = n
    return out


def open_task_ids(conn, stage):
    return [r[0] for r in conn.execute(
        "SELECT paper_id FROM task WHERE stage=? AND state IN ('todo','leased') ORDER BY prio, paper_id",
        (stage,))]


# --------------------------------------------------------------------------- reviews and results

def add_review(conn, rec):
    """Insert a review row. `rec` has the REVIEW_COLS keys (at defaults to now). Returns row id."""
    row = {k: rec.get(k) for k in REVIEW_COLS}
    if row["at"] is None:
        row["at"] = now()
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO review(%s) VALUES(%s)" % (",".join(REVIEW_COLS), ",".join("?" * len(REVIEW_COLS))),
            [row[k] for k in REVIEW_COLS])
    return cur.lastrowid


def reviews_for(conn, pid, stage=None):
    q = "SELECT * FROM review WHERE paper_id=?"
    args = [pid]
    if stage:
        q += " AND stage=?"
        args.append(stage)
    return [dict(r) for r in conn.execute(q + " ORDER BY id", args)]


def latest_review(conn, pid, stage):
    r = conn.execute("SELECT * FROM review WHERE paper_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                     (pid, stage)).fetchone()
    return dict(r) if r else None


def upsert_result(conn, pid, verdict, role, p, by):
    with tx(conn):
        conn.execute(
            "INSERT INTO result(paper_id,verdict,role,p,by,at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(paper_id) DO UPDATE SET verdict=excluded.verdict, role=excluded.role, "
            "p=excluded.p, by=excluded.by, at=excluded.at",
            (pid, verdict, role, p, by, now()))


def get_result(conn, pid):
    r = conn.execute("SELECT * FROM result WHERE paper_id=?", (pid,)).fetchone()
    return dict(r) if r else None


def accepted(conn):
    """Accepted canonical papers: list of dict(id, role, p), export order."""
    order = "CASE r.role WHEN 'baseline' THEN 0 WHEN 'benchmark' THEN 1 WHEN 'evaluation' THEN 2 ELSE 3 END"
    return [dict(x) for x in conn.execute(
        "SELECT r.paper_id AS id, r.role, r.p FROM result r JOIN paper p ON p.id=r.paper_id "
        "WHERE r.verdict='accept' AND p.dup_of IS NULL ORDER BY %s, r.p DESC, r.paper_id" % order)]


def tier_histogram(conn):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT t.tier, COUNT(*) FROM triage t JOIN paper p ON p.id=t.paper_id "
        "WHERE p.dup_of IS NULL GROUP BY 1 ORDER BY 1")}
