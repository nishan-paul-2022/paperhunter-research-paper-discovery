"""
b_io.py — file helpers and the Run context object for paper-hunter.
"""
import csv, hashlib, html, io, json, os, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR  = Path(__file__).resolve().parent          # paper-hunter/
ROOT       = TOOLS_DIR.parent                          # paperhunter-research-paper-discovery/
RUNS_DIR   = ROOT / "paper-dump"
DEFAULT_OUTPUT = ROOT / "paper-reference"

MIN_ABSTRACT = 120


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── JSON-L helpers ────────────────────────────────────────────────────────────

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
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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


# ── string helpers ────────────────────────────────────────────────────────────

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
    s = re.sub(r"^\s*abstract[:.\\s-]*", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ── Run context ───────────────────────────────────────────────────────────────

class Run:
    def __init__(self, name=None):
        if not name:
            # Read CSV path from .current file in paper-hunter/
            cur = TOOLS_DIR / ".current"
            if not cur.exists():
                raise SystemExit(
                    "ERROR: no active run. Create .current file with CSV path")
            csv_path = cur.read_text().strip()
            if not csv_path or not Path(csv_path).exists():
                raise SystemExit(
                    "ERROR: CSV path in .current does not exist: %s" % csv_path)
            # Derive run name from CSV filename
            name = Path(csv_path).stem
        self.name = name
        self.dir  = RUNS_DIR / name
        if not self.dir.exists():
            raise SystemExit("ERROR: run directory %s does not exist" % self.dir)
        self.papers_path  = self.dir / "papers.jsonl"
        self.dossier_path = self.dir / "dossiers.jsonl"
        # Unified files (no legacy subdirectories)
        self.enrichment_db_path = self.dir / "enrichment.db.jsonl"
        self.decisions_path = self.dir / "decisions.jsonl"
        self.state_path = self.dir / "state.json"
        self.manual_overrides_path = self.dir / "manual-overrides.jsonl"
        self.fulltext_db_path = self.dir / "fulltext.db.jsonl"
        self._dos         = None
        self._enrichment_index = None
        self._state = None
        self._meta = None

    def dec_path(self, p):
        """Legacy method for backward compatibility"""
        return self.dir / ("decisions-%s.jsonl" % p.lower())

    def papers(self):
        return read_jsonl(self.papers_path)

    def dossiers(self):
        if self._dos is None:
            rows     = read_jsonl(self.dossier_path)
            self._dos = {r["id"]: r for r in rows} if rows else {r["id"]: r for r in self.papers()}
        return self._dos

    def enrichment_index(self):
        """Lazy-load enrichment index into memory for fast lookups"""
        if self._enrichment_index is None:
            self._enrichment_index = {}
            if self.enrichment_db_path.exists():
                for record in read_jsonl(self.enrichment_db_path):
                    self._enrichment_index[record["id"]] = record
        return self._enrichment_index

    def get_enrichment(self, paper_id):
        """Get cached enrichment for a paper"""
        return self.enrichment_index().get(paper_id)

    def save_enrichment(self, paper_id, data):
        """Save enrichment data to unified DB"""
        data["id"] = paper_id
        if "ts" not in data:
            data["ts"] = now()
        append_jsonl(self.enrichment_db_path, data)
        # Update in-memory index if loaded
        if self._enrichment_index is not None:
            self._enrichment_index[paper_id] = data

    def decisions(self, p=None):
        """Get decisions, optionally filtered by pass"""
        if not self.decisions_path.exists():
            return {}
        decisions = {}
        for record in read_jsonl(self.decisions_path):
            if p is None or record.get("pass") == p:
                # Latest decision wins for each paper
                decisions[record["id"]] = record
        return decisions

    def save_decision(self, decision):
        """Append decision to unified log"""
        if "ts" not in decision:
            decision["ts"] = now()
        append_jsonl(self.decisions_path, decision)

    def state(self):
        """Get current run state"""
        if self._state is None:
            self._state = read_json(self.state_path, self._default_state())
        return self._state

    def update_state(self, **kwargs):
        """Update run state"""
        state = self.state()
        state.update(kwargs)
        write_json(self.state_path, state)
        self._state = state
    
    @property
    def meta(self):
        """Get metadata"""
        if self._meta is None:
            self._meta = self.state().get("meta", {})
        return self._meta

    def get_manual_override(self, paper_id):
        """Get manual override data for a paper"""
        if not self.manual_overrides_path.exists():
            return {}
        
        for record in read_jsonl(self.manual_overrides_path):
            if record.get("id") == paper_id:
                return record
        return {}
    
    def save_manual_override(self, paper_id, data):
        """Save manual override data"""
        # Read existing overrides
        existing = {}
        if self.manual_overrides_path.exists():
            for record in read_jsonl(self.manual_overrides_path):
                existing[record["id"]] = record
        
        # Update or add
        data["id"] = paper_id
        existing[paper_id] = data
        
        # Write back
        write_jsonl(self.manual_overrides_path, list(existing.values()))
    
    def get_fulltext(self, paper_id):
        """Get fulltext for a paper"""
        if not self.fulltext_db_path.exists():
            return None
        
        for record in read_jsonl(self.fulltext_db_path):
            if record.get("id") == paper_id:
                return record.get("text", "")
        return None
    
    def save_fulltext(self, paper_id, text, source=""):
        """Save fulltext"""
        # Read existing
        existing = {}
        if self.fulltext_db_path.exists():
            for record in read_jsonl(self.fulltext_db_path):
                existing[record["id"]] = record
        
        # Update or add
        existing[paper_id] = {
            "id": paper_id,
            "text": text,
            "source": source,
            "ts": now()
        }
        
        # Write back
        write_jsonl(self.fulltext_db_path, list(existing.values()))

    def _default_state(self):
        """Default state structure"""
        # Try to load legacy meta.json and plans.json for migration
        meta_file = self.dir / "meta.json"
        meta = read_json(meta_file, {}) if meta_file.exists() else {}
        plans_file = self.dir / "plans.json"
        plans = read_json(plans_file, {}) if plans_file.exists() else {}
        return {
            "version": "2.0",
            "meta": meta,
            "plans": plans,
            "current_stage": "init",
            "stages": {
                "enrichment": {"status": "not_started"},
                "pass_a_screening": {"status": "not_started"},
                "pass_b_screening": {"status": "not_started"},
                "pass_c_screening": {"status": "not_started"},
                "reconciliation": {"status": "not_started"},
                "validation": {"status": "not_started"},
                "export": {"status": "not_started"}
            },
            "passes": {
                "A": {"planned": None, "completed": 0},
                "B": {"planned": None, "completed": 0},
                "C": {"planned": None, "completed": 0},
                "F": {"planned": None, "completed": 0}
            }
        }

    def plans(self):
        return self.state().get("plans", {})

    def save_plans(self, pl):
        state = self.state()
        state["plans"] = pl
        write_json(self.state_path, state)
        self._state = state

    def eligible(self):
        return [p["id"] for p in self.papers() if p.get("in_range") and not p.get("dup_of")]
