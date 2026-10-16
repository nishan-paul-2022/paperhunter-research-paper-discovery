import csv
import html
import json
import os
import re
import shutil
import sqlite3
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
RUNS_DIR = ROOT / "paper-supplement"
DEFAULT_OUTPUT = ROOT / "paper-reference"

MIN_ABSTRACT = 120

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id              TEXT PRIMARY KEY,
    row             INTEGER,
    dblp_key        TEXT,
    title           TEXT,
    authors         TEXT,
    year            INTEGER,
    venue           TEXT,
    ee              TEXT,
    url             TEXT,
    doi             TEXT,
    in_range        INTEGER DEFAULT 1,
    pub_hint        TEXT,
    dup_of          TEXT,
    arxiv_id        TEXT,
    arxiv_url       TEXT,
    pdf_urls        TEXT,
    crossref_type   TEXT,
    container       TEXT,
    published_date  TEXT,
    cited_by        INTEGER,
    retracted       INTEGER DEFAULT 0,
    abstract        TEXT,
    abstract_source TEXT,
    abstract_snippet TEXT,
    abstract_candidates TEXT,
    keywords        TEXT,
    topics          TEXT,
    tldr            TEXT,
    manual_notes    TEXT DEFAULT '',
    lane            TEXT DEFAULT 'C',
    signal_score    INTEGER DEFAULT 0,
    signal_flags    TEXT,
    safety_net      INTEGER DEFAULT 0,
    role_hints      TEXT,
    seed_fuzzy      REAL DEFAULT 0,
    seed_match      TEXT DEFAULT '',
    typo_hits       TEXT,
    demote_hits     TEXT,
    bm25_pct        REAL DEFAULT 0,
    sim             TEXT,
    sim_domain_pct  REAL DEFAULT 0,
    emb_domain      REAL DEFAULT 0,
    extra_columns   TEXT,
    enrichment_blob TEXT,
    fulltext        TEXT,
    fulltext_source TEXT,
    fulltext_ts     TEXT
);

CREATE INDEX IF NOT EXISTS idx_papers_lane ON papers(lane);
CREATE INDEX IF NOT EXISTS idx_papers_signal ON papers(signal_score DESC);
CREATE INDEX IF NOT EXISTS idx_papers_in_range ON papers(in_range);

CREATE TABLE IF NOT EXISTS decisions (
    paper_id        TEXT REFERENCES papers(id),
    pass_name       TEXT,
    verdict         TEXT,
    reject_class    TEXT,
    primary_role    TEXT,
    confidence      REAL,
    blob            TEXT,
    PRIMARY KEY (paper_id, pass_name)
);

CREATE INDEX IF NOT EXISTS idx_decisions_pass ON decisions(pass_name);
CREATE INDEX IF NOT EXISTS idx_decisions_pass_verdict ON decisions(pass_name, verdict);

CREATE TABLE IF NOT EXISTS final_decisions (
    paper_id        TEXT PRIMARY KEY REFERENCES papers(id),
    verdict         TEXT,
    reject_class    TEXT,
    primary_role    TEXT,
    roles           TEXT,
    primary_contribution TEXT,
    subject_scope   TEXT,
    llm_agent       TEXT,
    offensive_security TEXT,
    black_box       TEXT,
    long_horizon    TEXT,
    publication_status TEXT,
    evidence_level  TEXT,
    confidence      REAL,
    review_passes   TEXT,
    full_text_read  TEXT,
    evidence_quote  TEXT,
    reason          TEXT,
    system_name     TEXT,
    attack_surfaces TEXT,
    blob            TEXT
);

CREATE INDEX IF NOT EXISTS idx_final_verdict ON final_decisions(verdict);
CREATE INDEX IF NOT EXISTS idx_final_role ON final_decisions(primary_role);

CREATE TABLE IF NOT EXISTS manual_overrides (
    paper_id        TEXT PRIMARY KEY REFERENCES papers(id),
    blob            TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_state (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS pass_queue (
    paper_id        TEXT NOT NULL,
    pass_name       TEXT NOT NULL,
    why             TEXT DEFAULT '',
    created_at      TEXT,
    PRIMARY KEY (paper_id, pass_name)
);

CREATE INDEX IF NOT EXISTS idx_pass_queue_pass ON pass_queue(pass_name);

CREATE VIEW IF NOT EXISTS audit AS
SELECT
    p.id AS paper_id,
    p.dblp_key,
    p.title,
    p.authors,
    p.year,
    p.venue,
    case when p.doi != '' and p.doi is not null then 'https://doi.org/' || p.doi else '' end AS doi,
    p.arxiv_url,
    p.ee,
    coalesce(f.verdict, '') AS verdict,
    coalesce(f.reject_class, '') AS reject_class,
    coalesce(f.primary_role, '') AS primary_role,
    coalesce(f.roles, '') AS roles,
    coalesce(f.primary_contribution, '') AS primary_contribution,
    coalesce(f.subject_scope, '') AS subject_scope,
    coalesce(f.llm_agent, '') AS llm_agent,
    coalesce(f.offensive_security, '') AS offensive_security,
    coalesce(f.black_box, '') AS black_box,
    coalesce(f.long_horizon, '') AS long_horizon,
    coalesce(f.publication_status, '') AS publication_status,
    coalesce(f.evidence_level, '') AS evidence_level,
    case when f.confidence is not null then f.confidence else '' end AS confidence,
    coalesce(f.review_passes, '') AS review_passes,
    p.signal_score,
    p.lane,
    p.bm25_pct,
    p.seed_fuzzy,
    p.abstract_source,
    case when p.signal_flags like '[%]' then (SELECT coalesce(group_concat(value, ';'), '') FROM json_each(p.signal_flags)) else coalesce(p.signal_flags, '') end AS signal_flags,
    coalesce(f.full_text_read, 'no') AS full_text_read,
    coalesce(f.evidence_quote, '') AS evidence_quote,
    coalesce(f.reason, '') AS reason,
    p.abstract
FROM papers p
LEFT JOIN final_decisions f ON p.id = f.paper_id;

CREATE TRIGGER IF NOT EXISTS trg_audit_insert INSTEAD OF INSERT ON audit BEGIN SELECT 1; END;
CREATE TRIGGER IF NOT EXISTS trg_audit_update INSTEAD OF UPDATE ON audit BEGIN SELECT 1; END;
CREATE TRIGGER IF NOT EXISTS trg_audit_delete INSTEAD OF DELETE ON audit BEGIN SELECT 1; END;
"""

_AUDIT_COLS = [
    "paper_id", "dblp_key", "title", "authors", "year", "venue", "doi",
    "arxiv_url", "ee", "verdict", "reject_class", "primary_role", "roles",
    "primary_contribution", "subject_scope", "llm_agent", "offensive_security",
    "black_box", "long_horizon", "publication_status", "evidence_level",
    "confidence", "review_passes", "signal_score", "lane", "bm25_pct",
    "seed_fuzzy", "abstract_source", "signal_flags", "full_text_read",
    "evidence_quote", "reason", "abstract",
]

_FINAL_COLS = [
    "paper_id", "verdict", "reject_class", "primary_role", "roles",
    "primary_contribution", "subject_scope", "llm_agent", "offensive_security",
    "black_box", "long_horizon", "publication_status", "evidence_level",
    "confidence", "review_passes", "full_text_read", "evidence_quote",
    "reason", "system_name", "attack_surfaces", "blob",
]


def _extract_final_fields(r):
    crit = r.get("criteria") or {}
    passes = r.get("passes", [])
    review_passes = "".join(passes) if passes != ["auto"] else "auto"
    ft = "yes" if r.get("fulltext_read") else "no"
    roles = ";".join(r.get("roles", [])) if isinstance(r.get("roles"), list) else (r.get("roles") or "")
    attack_surfaces = (
        json.dumps(r.get("attack_surfaces", []), ensure_ascii=False)
        if isinstance(r.get("attack_surfaces"), list)
        else (r.get("attack_surfaces") or "")
    )
    return {
        "paper_id": r.get("id") or r.get("paper_id"),
        "verdict": r.get("verdict"),
        "reject_class": r.get("reject_class"),
        "primary_role": r.get("primary_role"),
        "roles": roles,
        "primary_contribution": r.get("primary_contribution"),
        "subject_scope": r.get("subject_scope"),
        "llm_agent": crit.get("llm_agent"),
        "offensive_security": crit.get("offensive_security"),
        "black_box": crit.get("black_box"),
        "long_horizon": crit.get("long_horizon"),
        "publication_status": r.get("publication_status"),
        "evidence_level": r.get("evidence_level"),
        "confidence": r.get("confidence"),
        "review_passes": review_passes,
        "full_text_read": ft,
        "evidence_quote": r.get("evidence"),
        "reason": r.get("reason"),
        "system_name": r.get("system_name"),
        "attack_surfaces": attack_surfaces,
        "blob": json.dumps(r, ensure_ascii=False),
    }

_PAPER_COLS = [
    "id", "row", "dblp_key", "title", "authors", "year", "venue", "ee", "url",
    "doi", "in_range", "pub_hint", "dup_of", "arxiv_id", "arxiv_url", "pdf_urls",
    "crossref_type", "container", "published_date", "cited_by", "retracted",
    "abstract", "abstract_source", "abstract_snippet", "abstract_candidates",
    "keywords", "topics", "tldr", "manual_notes", "lane", "signal_score",
    "signal_flags", "safety_net", "role_hints", "seed_fuzzy", "seed_match",
    "typo_hits", "demote_hits", "bm25_pct", "sim", "sim_domain_pct", "emb_domain",
    "extra_columns", "enrichment_blob", "fulltext", "fulltext_source", "fulltext_ts",
]

_JSON_COLS = {
    "pdf_urls", "abstract_candidates", "keywords", "topics", "signal_flags",
    "role_hints", "typo_hits", "demote_hits", "sim", "extra_columns",
}
_BOOL_COLS = {"in_range", "retracted", "safety_net"}


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def clean_title(t):
    t = re.sub(r"\s+", " ", (t or "").strip())
    return t[:-1] if t.endswith(".") and not t.endswith("..") else t


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"^\s*abstract[:.\\s-]*", "", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip()


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


_DICT_COLS = {"sim", "abstract_candidates", "extra_columns"}


def _row_to_paper(row):
    if row is None:
        return None
    p = dict(row)
    for col in _JSON_COLS:
        if col in p and p[col] is not None:
            try:
                p[col] = json.loads(p[col])
            except Exception:
                p[col] = {} if col in _DICT_COLS else []
        elif col in p:
            p[col] = {} if col in _DICT_COLS else []
    for col in _BOOL_COLS:
        if col in p:
            p[col] = bool(p[col])
    if p.get("enrichment_blob"):
        try:
            p["enrichment"] = json.loads(p["enrichment_blob"])
        except Exception:
            p["enrichment"] = {}
    p.pop("enrichment_blob", None)
    if p.get("fulltext") is None:
        p["fulltext"] = ""
    return p


def _paper_to_row(p):
    row = {}
    for col in _PAPER_COLS:
        val = p.get(col)
        if col == "enrichment_blob":
            if "enrichment" in p and p["enrichment"] is not None:
                val = json.dumps(p["enrichment"], ensure_ascii=False)
            elif "enrichment_blob" in p:
                val = p["enrichment_blob"]
            else:
                val = "{}"
        elif col in _JSON_COLS:
            if val is None:
                val = "{}" if col in _DICT_COLS else "[]"
            else:
                val = json.dumps(val, ensure_ascii=False)
        elif col in _BOOL_COLS:
            val = 1 if val else 0
        row[col] = val
    if "enrichment" in p and p["enrichment"] is not None:
        row["enrichment_blob"] = json.dumps(p["enrichment"], ensure_ascii=False)
    return row


def _migrate_db(conn):
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver >= 2:
        return
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "papers" not in tables:
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA user_version = 2")
        return

    # 1. Migrate decisions table
    dec_cols = {r[1] for r in cur.execute("PRAGMA table_info(decisions)").fetchall()}
    if "verdict" not in dec_cols:
        conn.execute("ALTER TABLE decisions ADD COLUMN verdict TEXT")
        conn.execute("ALTER TABLE decisions ADD COLUMN reject_class TEXT")
        conn.execute("ALTER TABLE decisions ADD COLUMN primary_role TEXT")
        conn.execute("ALTER TABLE decisions ADD COLUMN confidence REAL")
        rows = cur.execute("SELECT paper_id, pass_name, blob FROM decisions WHERE blob IS NOT NULL").fetchall()
        for r in rows:
            try:
                d = json.loads(r[2])
                conn.execute(
                    "UPDATE decisions SET verdict=?, reject_class=?, primary_role=?, confidence=? WHERE paper_id=? AND pass_name=?",
                    (d.get("verdict"), d.get("reject_class"), d.get("primary_role"), d.get("confidence"), r[0], r[1]),
                )
            except Exception:
                pass

    # 2. Migrate final_decisions table
    fin_cols = {r[1] for r in cur.execute("PRAGMA table_info(final_decisions)").fetchall()}
    if "verdict" not in fin_cols:
        new_cols = [
            ("verdict", "TEXT"), ("reject_class", "TEXT"), ("primary_role", "TEXT"),
            ("roles", "TEXT"), ("primary_contribution", "TEXT"), ("subject_scope", "TEXT"),
            ("llm_agent", "TEXT"), ("offensive_security", "TEXT"), ("black_box", "TEXT"),
            ("long_horizon", "TEXT"), ("publication_status", "TEXT"), ("evidence_level", "TEXT"),
            ("confidence", "REAL"), ("review_passes", "TEXT"), ("full_text_read", "TEXT"),
            ("evidence_quote", "TEXT"), ("reason", "TEXT"), ("system_name", "TEXT"),
            ("attack_surfaces", "TEXT"),
        ]
        for col_name, col_type in new_cols:
            if col_name not in fin_cols:
                conn.execute(f"ALTER TABLE final_decisions ADD COLUMN {col_name} {col_type}")

        rows = cur.execute("SELECT paper_id, blob FROM final_decisions WHERE blob IS NOT NULL").fetchall()
        for r in rows:
            try:
                pid, b = r[0], r[1]
                d = json.loads(b)
                fields = _extract_final_fields(d)
                conn.execute(
                    """UPDATE final_decisions SET
                    verdict=?, reject_class=?, primary_role=?, roles=?, primary_contribution=?,
                    subject_scope=?, llm_agent=?, offensive_security=?, black_box=?, long_horizon=?,
                    publication_status=?, evidence_level=?, confidence=?, review_passes=?,
                    full_text_read=?, evidence_quote=?, reason=?, system_name=?, attack_surfaces=?
                    WHERE paper_id=?""",
                    (
                        fields["verdict"], fields["reject_class"], fields["primary_role"],
                        fields["roles"], fields["primary_contribution"], fields["subject_scope"],
                        fields["llm_agent"], fields["offensive_security"], fields["black_box"],
                        fields["long_horizon"], fields["publication_status"], fields["evidence_level"],
                        fields["confidence"], fields["review_passes"], fields["full_text_read"],
                        fields["evidence_quote"], fields["reason"], fields["system_name"],
                        fields["attack_surfaces"], pid,
                    ),
                )
            except Exception:
                pass

    # 3. Create pass_queue table and backfill
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pass_queue (
        paper_id        TEXT NOT NULL,
        pass_name       TEXT NOT NULL,
        why             TEXT DEFAULT '',
        created_at      TEXT,
        PRIMARY KEY (paper_id, pass_name)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pass_queue_pass ON pass_queue(pass_name)")

    pq_count = cur.execute("SELECT COUNT(*) FROM pass_queue").fetchone()[0]
    if pq_count == 0:
        row = cur.execute("SELECT value FROM pipeline_state WHERE key='state'").fetchone()
        if row:
            try:
                st = json.loads(row[0])
                pl = st.get("plans", {})
                f_why = pl.get("F_why", {})
                now_ts = now()
                rows_to_insert = []
                for pass_name, ids in pl.items():
                    if pass_name == "F_why" or not isinstance(ids, list):
                        continue
                    for pid in ids:
                        why = f_why.get(pid, "") if pass_name == "F" else ""
                        rows_to_insert.append((pid, pass_name, why, now_ts))
                if rows_to_insert:
                    cur.executemany("INSERT OR IGNORE INTO pass_queue(paper_id, pass_name, why, created_at) VALUES(?,?,?,?)", rows_to_insert)
                    st["plans"] = {}
                    cur.execute("UPDATE pipeline_state SET value=? WHERE key='state'", (json.dumps(st, ensure_ascii=False),))
            except Exception:
                pass

    # 4. Drop physical audit table if present
    audit_type = cur.execute("SELECT type FROM sqlite_master WHERE name='audit'").fetchone()
    if audit_type and audit_type[0] == "table":
        cur.execute("DROP TABLE audit")

    # 5. Ensure audit view, triggers, and performance indexes exist
    conn.executescript("""
    CREATE VIEW IF NOT EXISTS audit AS
    SELECT
        p.id AS paper_id,
        p.dblp_key,
        p.title,
        p.authors,
        p.year,
        p.venue,
        case when p.doi != '' and p.doi is not null then 'https://doi.org/' || p.doi else '' end AS doi,
        p.arxiv_url,
        p.ee,
        coalesce(f.verdict, '') AS verdict,
        coalesce(f.reject_class, '') AS reject_class,
        coalesce(f.primary_role, '') AS primary_role,
        coalesce(f.roles, '') AS roles,
        coalesce(f.primary_contribution, '') AS primary_contribution,
        coalesce(f.subject_scope, '') AS subject_scope,
        coalesce(f.llm_agent, '') AS llm_agent,
        coalesce(f.offensive_security, '') AS offensive_security,
        coalesce(f.black_box, '') AS black_box,
        coalesce(f.long_horizon, '') AS long_horizon,
        coalesce(f.publication_status, '') AS publication_status,
        coalesce(f.evidence_level, '') AS evidence_level,
        case when f.confidence is not null then f.confidence else '' end AS confidence,
        coalesce(f.review_passes, '') AS review_passes,
        p.signal_score,
        p.lane,
        p.bm25_pct,
        p.seed_fuzzy,
        p.abstract_source,
        case when p.signal_flags like '[%]' then (SELECT coalesce(group_concat(value, ';'), '') FROM json_each(p.signal_flags)) else coalesce(p.signal_flags, '') end AS signal_flags,
        coalesce(f.full_text_read, 'no') AS full_text_read,
        coalesce(f.evidence_quote, '') AS evidence_quote,
        coalesce(f.reason, '') AS reason,
        p.abstract
    FROM papers p
    LEFT JOIN final_decisions f ON p.id = f.paper_id;

    CREATE TRIGGER IF NOT EXISTS trg_audit_insert INSTEAD OF INSERT ON audit BEGIN SELECT 1; END;
    CREATE TRIGGER IF NOT EXISTS trg_audit_update INSTEAD OF UPDATE ON audit BEGIN SELECT 1; END;
    CREATE TRIGGER IF NOT EXISTS trg_audit_delete INSTEAD OF DELETE ON audit BEGIN SELECT 1; END;

    CREATE INDEX IF NOT EXISTS idx_decisions_pass_verdict ON decisions(pass_name, verdict);
    CREATE INDEX IF NOT EXISTS idx_final_verdict ON final_decisions(verdict);
    CREATE INDEX IF NOT EXISTS idx_final_role ON final_decisions(primary_role);
    """)

    conn.execute("PRAGMA user_version = 2")
    try:
        conn.execute("VACUUM")
    except Exception:
        pass


def _open_db(path):
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
    _migrate_db(conn)
    return conn


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    return out


def write_jsonl(path, rows):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    os.replace(tmp, path)


def write_json(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


class Run:
    def __init__(self, name=None, create=False):
        if not name:
            cur = TOOLS_DIR / ".current"
            if not cur.exists():
                raise SystemExit(
                    "ERROR: no active run. Create .current file with CSV path"
                )
            csv_path = cur.read_text().strip()
            if not csv_path or not Path(csv_path).exists():
                raise SystemExit(
                    "ERROR: CSV path in .current does not exist: %s" % csv_path
                )
            name = Path(csv_path).stem

        self.name = name
        self.dir = RUNS_DIR / name
        self.db_path = RUNS_DIR / f"{name}.db"

        if not self.db_path.exists():
            legacy_named = self.dir / f"{name}.db"
            legacy_papers = self.dir / "papers.db"
            if legacy_named.exists():
                shutil.move(str(legacy_named), str(self.db_path))
            elif legacy_papers.exists():
                shutil.move(str(legacy_papers), str(self.db_path))

        if not create and not self.db_path.exists() and not self.dir.exists():
            raise SystemExit("ERROR: run %s does not exist" % name)

        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self._db_path = self.db_path
        self._conn = None

        self.papers_path = self.dir / "papers.jsonl"
        self.state_path = self.dir / "state.json"
        self.dossier_path = self._db_path
        self.decisions_path = self._db_path
        self.enrichment_db_path = self._db_path
        self.manual_overrides_path = self._db_path
        self.fulltext_db_path = self._db_path

        self._papers_map = None
        self._state = None
        self._meta = None

    def _db(self):
        if self._conn is None:
            self._conn = _open_db(self._db_path)
        return self._conn

    def dec_path(self, p):
        return self._db_path

    def _migrate_from_flat_files_if_needed(self):
        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        if count == 0 and self.papers_path.exists():
            rows = read_jsonl(self.papers_path)
            if rows:
                cols_fin = _FINAL_COLS
                placeholders_fin = ", ".join("?" for _ in cols_fin)
                updates_fin = ", ".join(f"{c}=excluded.{c}" for c in cols_fin if c != "paper_id")
                sql_final = (
                    f"INSERT INTO final_decisions ({', '.join(cols_fin)}) VALUES ({placeholders_fin}) "
                    f"ON CONFLICT(paper_id) DO UPDATE SET {updates_fin}"
                )
                db.execute("BEGIN")
                try:
                    for p in rows:
                        self._upsert_paper(p)
                        pid = p["id"]
                        for pass_name, dec in p.get("decisions", {}).items():
                            db.execute(
                                "INSERT INTO decisions(paper_id, pass_name, verdict, reject_class, primary_role, confidence, blob) "
                                "VALUES(?,?,?,?,?,?,?) "
                                "ON CONFLICT(paper_id, pass_name) DO UPDATE SET "
                                "verdict=excluded.verdict, reject_class=excluded.reject_class, "
                                "primary_role=excluded.primary_role, confidence=excluded.confidence, blob=excluded.blob",
                                (pid, pass_name, dec.get("verdict"), dec.get("reject_class"),
                                 dec.get("primary_role"), dec.get("confidence"),
                                 json.dumps(dec, ensure_ascii=False)),
                            )
                        if "final" in p:
                            row_fin = _extract_final_fields(p["final"])
                            db.execute(sql_final, [row_fin[c] for c in cols_fin])
                        if "manual" in p:
                            db.execute(
                                "INSERT INTO manual_overrides(paper_id, blob) VALUES(?,?) "
                                "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                                (pid, json.dumps(p["manual"], ensure_ascii=False)),
                            )
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise

            if self.state_path.exists():
                st = read_json(self.state_path)
                if st:
                    if "plans" in st and st["plans"]:
                        self.save_plans(st["plans"])
                    db.execute(
                        "INSERT INTO pipeline_state(key, value) VALUES('state', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (json.dumps(st, ensure_ascii=False),),
                    )

    def _load_papers_map(self):
        self._migrate_from_flat_files_if_needed()
        db = self._db()
        rows = db.execute("SELECT * FROM papers").fetchall()
        pmap = {}
        for row in rows:
            p = _row_to_paper(row)
            p["decisions"] = {}
            pmap[p["id"]] = p

        for dr in db.execute("SELECT paper_id, pass_name, blob FROM decisions").fetchall():
            pid = dr["paper_id"]
            if pid in pmap:
                try:
                    pmap[pid]["decisions"][dr["pass_name"]] = json.loads(dr["blob"])
                except Exception:
                    pass

        for fr in db.execute("SELECT paper_id, blob FROM final_decisions").fetchall():
            pid = fr["paper_id"]
            if pid in pmap:
                try:
                    pmap[pid]["final"] = json.loads(fr["blob"])
                except Exception:
                    pass

        for mr in db.execute("SELECT paper_id, blob FROM manual_overrides").fetchall():
            pid = mr["paper_id"]
            if pid in pmap:
                try:
                    pmap[pid]["manual"] = json.loads(mr["blob"])
                except Exception:
                    pass

        return pmap

    def _get_papers_map(self):
        if self._papers_map is None:
            self._papers_map = self._load_papers_map()
        return self._papers_map

    def papers(self):
        return list(self._get_papers_map().values())

    def get_paper(self, paper_id):
        if self._papers_map is not None:
            return self._papers_map.get(paper_id)
        cur = self._db().execute("SELECT * FROM papers WHERE id=?", (paper_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return _row_to_paper(dict(zip(cols, row)))

    def dossiers(self):
        return self._get_papers_map()

    def _upsert_paper(self, p):
        row = _paper_to_row(p)
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join("%s = excluded.%s" % (c, c) for c in cols if c != "id")
        sql = (
            "INSERT INTO papers (%s) VALUES (%s) ON CONFLICT(id) DO UPDATE SET %s"
            % (", ".join(cols), placeholders, updates)
        )
        self._db().execute(sql, [row[c] for c in cols])

    def save_papers(self):
        if self._papers_map is None:
            return
        db = self._db()
        cols_fin = _FINAL_COLS
        placeholders_fin = ", ".join("?" for _ in cols_fin)
        updates_fin = ", ".join(f"{c}=excluded.{c}" for c in cols_fin if c != "paper_id")
        sql_final = (
            f"INSERT INTO final_decisions ({', '.join(cols_fin)}) VALUES ({placeholders_fin}) "
            f"ON CONFLICT(paper_id) DO UPDATE SET {updates_fin}"
        )
        db.execute("BEGIN")
        try:
            for p in self._papers_map.values():
                self._upsert_paper(p)
                pid = p["id"]
                for pass_name, dec in p.get("decisions", {}).items():
                    db.execute(
                        "INSERT INTO decisions(paper_id, pass_name, verdict, reject_class, primary_role, confidence, blob) "
                        "VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(paper_id, pass_name) DO UPDATE SET "
                        "verdict=excluded.verdict, reject_class=excluded.reject_class, "
                        "primary_role=excluded.primary_role, confidence=excluded.confidence, blob=excluded.blob",
                        (pid, pass_name, dec.get("verdict"), dec.get("reject_class"),
                         dec.get("primary_role"), dec.get("confidence"),
                         json.dumps(dec, ensure_ascii=False)),
                    )
                if "final" in p:
                    row_fin = _extract_final_fields(p["final"])
                    db.execute(sql_final, [row_fin[c] for c in cols_fin])
                if "manual" in p:
                    db.execute(
                        "INSERT INTO manual_overrides(paper_id, blob) VALUES(?,?) "
                        "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                        (pid, json.dumps(p["manual"], ensure_ascii=False)),
                    )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def get_enrichment(self, paper_id):
        p = self.get_paper(paper_id)
        return p.get("enrichment") if p else None

    def save_enrichment(self, paper_id, data):
        p = self.get_paper(paper_id)
        if p is not None:
            data["id"] = paper_id
            if "ts" not in data:
                data["ts"] = now()
            p["enrichment"] = data
            db = self._db()
            db.execute(
                "UPDATE papers SET enrichment_blob = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), paper_id),
            )

    def get_manual_override(self, paper_id):
        p = self.get_paper(paper_id)
        return p.get("manual", {}) if p else {}

    def save_manual_override(self, paper_id, data):
        p = self.get_paper(paper_id)
        if p is not None:
            data["id"] = paper_id
            p["manual"] = data
            db = self._db()
            db.execute(
                "INSERT INTO manual_overrides(paper_id, blob) VALUES(?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                (paper_id, json.dumps(data, ensure_ascii=False)),
            )
            self.save_papers()

    def get_fulltext(self, paper_id):
        p = self.get_paper(paper_id)
        return p.get("fulltext", "") if p else ""

    def save_fulltext(self, paper_id, text, source=""):
        p = self.get_paper(paper_id)
        if p is not None:
            p["fulltext"] = text
            p["fulltext_source"] = source
            p["fulltext_ts"] = now()
            db = self._db()
            db.execute(
                "UPDATE papers SET fulltext=?, fulltext_source=?, fulltext_ts=? WHERE id=?",
                (text, source, now(), paper_id),
            )

    def decisions(self, p=None):
        res = {}
        for pid, paper in self._get_papers_map().items():
            paper_decs = paper.get("decisions", {})
            if p is not None:
                if p in paper_decs:
                    res[pid] = paper_decs[p]
            else:
                for pass_name, dec in paper_decs.items():
                    res[pid] = dec
        return res

    def save_decision(self, decision):
        pid = decision.get("id")
        p = self.get_paper(pid)
        if p is not None:
            if "ts" not in decision:
                decision["ts"] = now()
            pass_name = decision.get("pass", "A")
            p.setdefault("decisions", {})[pass_name] = decision
            db = self._db()
            db.execute(
                "INSERT INTO decisions(paper_id, pass_name, verdict, reject_class, primary_role, confidence, blob) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(paper_id, pass_name) DO UPDATE SET "
                "verdict=excluded.verdict, reject_class=excluded.reject_class, "
                "primary_role=excluded.primary_role, confidence=excluded.confidence, blob=excluded.blob",
                (pid, pass_name, decision.get("verdict"), decision.get("reject_class"),
                 decision.get("primary_role"), decision.get("confidence"),
                 json.dumps(decision, ensure_ascii=False)),
            )

    def final_decisions(self):
        return {
            pid: paper["final"]
            for pid, paper in self._get_papers_map().items()
            if "final" in paper
        }

    def save_final(self, final_records):
        db = self._db()
        items = final_records if isinstance(final_records, list) else list(final_records.values())
        cols_fin = _FINAL_COLS
        placeholders_fin = ", ".join("?" for _ in cols_fin)
        updates_fin = ", ".join(f"{c}=excluded.{c}" for c in cols_fin if c != "paper_id")
        sql_final = (
            f"INSERT INTO final_decisions ({', '.join(cols_fin)}) VALUES ({placeholders_fin}) "
            f"ON CONFLICT(paper_id) DO UPDATE SET {updates_fin}"
        )
        db.execute("BEGIN")
        try:
            for r in items:
                p = self.get_paper(r["id"])
                if p is not None:
                    p["final"] = r
                row_fin = _extract_final_fields(r)
                db.execute(sql_final, [row_fin[c] for c in cols_fin])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def state(self):
        if self._state is None:
            self._migrate_from_flat_files_if_needed()
            row = self._db().execute(
                "SELECT value FROM pipeline_state WHERE key='state'"
            ).fetchone()
            if row:
                try:
                    self._state = json.loads(row["value"])
                except Exception:
                    self._state = self._default_state()
            else:
                self._state = self._default_state()
            if not self._state.get("plans"):
                self._state["plans"] = self.plans()
        return self._state

    def save_audit(self, rows=None):
        """
        audit is implemented as a dynamic, zero-overhead SQL VIEW over papers + final_decisions.
        Calling save_audit is a no-op kept for complete backward compatibility.
        """
        pass

    def audit(self):
        rows = self._db().execute("SELECT * FROM audit ORDER BY paper_id").fetchall()
        return [dict(r) for r in rows]

    def update_state(self, **kwargs):
        s = self.state()
        s.update(kwargs)
        self._db().execute(
            "INSERT INTO pipeline_state(key, value) VALUES('state', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(s, ensure_ascii=False),),
        )
        self._state = s

    @property
    def meta(self):
        if self._meta is None:
            self._meta = self.state().get("meta", {})
        return self._meta

    def plans(self):
        db = self._db()
        rows = db.execute("SELECT paper_id, pass_name, why FROM pass_queue ORDER BY rowid").fetchall()
        if rows:
            pl = {}
            for r in rows:
                pname = r["pass_name"]
                pid = r["paper_id"]
                why = r["why"]
                pl.setdefault(pname, []).append(pid)
                if pname == "F" and why:
                    pl.setdefault("F_why", {})[pid] = why
            return pl
        return self.state().get("plans", {})

    def save_plans(self, pl):
        db = self._db()
        db.execute("BEGIN")
        try:
            db.execute("DELETE FROM pass_queue")
            now_ts = now()
            f_why = pl.get("F_why", {})
            rows_to_insert = []
            for pass_name, ids in pl.items():
                if pass_name == "F_why" or not isinstance(ids, list):
                    continue
                for pid in ids:
                    why = f_why.get(pid, "") if pass_name == "F" else ""
                    rows_to_insert.append((pid, pass_name, why, now_ts))
            if rows_to_insert:
                db.executemany(
                    "INSERT INTO pass_queue(paper_id, pass_name, why, created_at) VALUES(?,?,?,?)",
                    rows_to_insert,
                )
            s = self.state()
            s["plans"] = {}
            db.execute(
                "INSERT INTO pipeline_state(key, value) VALUES('state', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(s, ensure_ascii=False),),
            )
            s["plans"] = pl
            self._state = s
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def _default_state(self):
        return {
            "version": "3.0",
            "meta": {},
            "plans": {},
            "current_stage": "init",
            "stages": {
                "enrichment": {"status": "not_started"},
                "pass_a_screening": {"status": "not_started"},
                "pass_b_screening": {"status": "not_started"},
                "pass_c_screening": {"status": "not_started"},
                "reconciliation": {"status": "not_started"},
                "validation": {"status": "not_started"},
                "export": {"status": "not_started"},
            },
            "passes": {
                "A": {"planned": None, "completed": 0},
                "B": {"planned": None, "completed": 0},
                "C": {"planned": None, "completed": 0},
                "F": {"planned": None, "completed": 0},
            },
        }

    def eligible(self):
        return [
            p["id"] for p in self.papers() if p.get("in_range") and not p.get("dup_of")
        ]

    def init_papers(self, papers):
        db = self._db()
        db.execute("BEGIN")
        try:
            for p in papers:
                self._upsert_paper(p)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        self._papers_map = None
