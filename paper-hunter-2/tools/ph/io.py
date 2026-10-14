"""
ph/io.py — file helpers and the Run context object.
"""
import csv, hashlib, html, io, json, os, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR  = Path(__file__).resolve().parent.parent   # tools/
ROOT       = TOOLS_DIR.parent                          # paper-hunter-2/
RUNS_DIR   = ROOT / "runs"
DEFAULT_OUTPUT = ROOT / "output"

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
            cur = RUNS_DIR / ".current"
            if not cur.exists():
                raise SystemExit(
                    "ERROR: no active run. Start with: python3 tools/ph.py init --csv <path>")
            name = cur.read_text().strip()
        self.name = name
        self.dir  = RUNS_DIR / name
        if not self.dir.exists():
            raise SystemExit("ERROR: run directory %s does not exist" % self.dir)
        self.papers_path  = self.dir / "papers.jsonl"
        self.dossier_path = self.dir / "dossiers.jsonl"
        self.cache_dir    = self.dir / "cache"
        self.manual_dir   = self.dir / "manual"
        self.fulltext_dir = self.dir / "fulltext"
        self.plans_path   = self.dir / "plans.json"
        self.meta         = read_json(self.dir / "meta.json", {})
        self._dos         = None

    def dec_path(self, p):
        return self.dir / ("decisions_%s.jsonl" % p)

    def papers(self):
        return read_jsonl(self.papers_path)

    def dossiers(self):
        if self._dos is None:
            rows     = read_jsonl(self.dossier_path)
            self._dos = {r["id"]: r for r in rows} if rows else {r["id"]: r for r in self.papers()}
        return self._dos

    def decisions(self, p):
        d = {}
        for r in read_jsonl(self.dec_path(p)):
            d[r["id"]] = r
        return d

    def plans(self):
        return read_json(self.plans_path, {})

    def save_plans(self, pl):
        write_json(self.plans_path, pl)

    def eligible(self):
        return [p["id"] for p in self.papers() if p.get("in_range") and not p.get("dup_of")]
