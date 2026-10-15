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
    blob            TEXT,
    PRIMARY KEY (paper_id, pass_name)
);

CREATE INDEX IF NOT EXISTS idx_decisions_pass ON decisions(pass_name);

CREATE TABLE IF NOT EXISTS final_decisions (
    paper_id        TEXT PRIMARY KEY REFERENCES papers(id),
    blob            TEXT
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    paper_id        TEXT PRIMARY KEY REFERENCES papers(id),
    blob            TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_state (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    paper_id            TEXT PRIMARY KEY REFERENCES papers(id),
    dblp_key            TEXT,
    title               TEXT,
    authors             TEXT,
    year                INTEGER,
    venue               TEXT,
    doi                 TEXT,
    arxiv_url           TEXT,
    ee                  TEXT,
    verdict             TEXT,
    reject_class        TEXT,
    primary_role        TEXT,
    roles               TEXT,
    primary_contribution TEXT,
    subject_scope       TEXT,
    llm_agent           TEXT,
    offensive_security  TEXT,
    black_box           TEXT,
    long_horizon        TEXT,
    publication_status  TEXT,
    evidence_level      TEXT,
    confidence          REAL,
    review_passes       TEXT,
    signal_score        INTEGER,
    lane                TEXT,
    bm25_pct            REAL,
    seed_fuzzy          REAL,
    abstract_source     TEXT,
    signal_flags        TEXT,
    full_text_read      TEXT,
    evidence_quote      TEXT,
    reason              TEXT,
    abstract            TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_verdict ON audit(verdict);
CREATE INDEX IF NOT EXISTS idx_audit_role ON audit(primary_role);
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


def _open_db(path):
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
    conn.executescript(_SCHEMA)
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
    def __init__(self, name=None):
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

        if not self.db_path.exists() and not self.dir.exists():
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
                db.execute("BEGIN")
                try:
                    for p in rows:
                        self._upsert_paper(p)
                        pid = p["id"]
                        for pass_name, dec in p.get("decisions", {}).items():
                            db.execute(
                                "INSERT INTO decisions(paper_id, pass_name, blob) VALUES(?,?,?) "
                                "ON CONFLICT(paper_id, pass_name) DO UPDATE SET blob=excluded.blob",
                                (pid, pass_name, json.dumps(dec, ensure_ascii=False)),
                            )
                        if "final" in p:
                            db.execute(
                                "INSERT INTO final_decisions(paper_id, blob) VALUES(?,?) "
                                "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                                (pid, json.dumps(p["final"], ensure_ascii=False)),
                            )
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
                    db.execute(
                        "INSERT INTO pipeline_state(key, value) VALUES('state', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (json.dumps(st, ensure_ascii=False),),
                    )

            audit_csv = self.dir / f"{self.name}-audit-all.csv"
            if audit_csv.exists():
                try:
                    with open(audit_csv, encoding="utf-8-sig") as f:
                        rdr = csv.DictReader(f)
                        audit_rows = list(rdr)
                        if audit_rows:
                            self.save_audit(audit_rows)
                except Exception:
                    pass

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
        return self._get_papers_map().get(paper_id)

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
        db.execute("BEGIN")
        try:
            for p in self._papers_map.values():
                self._upsert_paper(p)
                pid = p["id"]
                for pass_name, dec in p.get("decisions", {}).items():
                    db.execute(
                        "INSERT INTO decisions(paper_id, pass_name, blob) VALUES(?,?,?) "
                        "ON CONFLICT(paper_id, pass_name) DO UPDATE SET blob=excluded.blob",
                        (pid, pass_name, json.dumps(dec, ensure_ascii=False)),
                    )
                if "final" in p:
                    db.execute(
                        "INSERT INTO final_decisions(paper_id, blob) VALUES(?,?) "
                        "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                        (pid, json.dumps(p["final"], ensure_ascii=False)),
                    )
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
                "INSERT INTO decisions(paper_id, pass_name, blob) VALUES(?,?,?) "
                "ON CONFLICT(paper_id, pass_name) DO UPDATE SET blob=excluded.blob",
                (pid, pass_name, json.dumps(decision, ensure_ascii=False)),
            )

    def final_decisions(self):
        return {
            pid: paper["final"]
            for pid, paper in self._get_papers_map().items()
            if "final" in paper
        }

    def save_final(self, final_records):
        db = self._db()
        if isinstance(final_records, list):
            for r in final_records:
                p = self.get_paper(r["id"])
                if p is not None:
                    p["final"] = r
                    db.execute(
                        "INSERT INTO final_decisions(paper_id, blob) VALUES(?,?) "
                        "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                        (r["id"], json.dumps(r, ensure_ascii=False)),
                    )
        elif isinstance(final_records, dict):
            for pid, r in final_records.items():
                p = self.get_paper(pid)
                if p is not None:
                    p["final"] = r
                    db.execute(
                        "INSERT INTO final_decisions(paper_id, blob) VALUES(?,?) "
                        "ON CONFLICT(paper_id) DO UPDATE SET blob=excluded.blob",
                        (pid, json.dumps(r, ensure_ascii=False)),
                    )

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
        return self._state

    def save_audit(self, rows):
        db = self._db()
        db.execute("BEGIN")
        try:
            for r in rows:
                data = dict(r)
                if "id" in data and "paper_id" not in data:
                    data["paper_id"] = data["id"]
                vals = [data.get(c) for c in _AUDIT_COLS]
                placeholders = ", ".join("?" for _ in _AUDIT_COLS)
                sql = (
                    f"INSERT INTO audit ({', '.join(_AUDIT_COLS)}) VALUES ({placeholders}) "
                    f"ON CONFLICT(paper_id) DO UPDATE SET "
                    + ", ".join(f"{c}=excluded.{c}" for c in _AUDIT_COLS if c != "paper_id")
                )
                db.execute(sql, vals)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

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
        return self.state().get("plans", {})

    def save_plans(self, pl):
        s = self.state()
        s["plans"] = pl
        self._db().execute(
            "INSERT INTO pipeline_state(key, value) VALUES('state', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(s, ensure_ascii=False),),
        )
        self._state = s

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
