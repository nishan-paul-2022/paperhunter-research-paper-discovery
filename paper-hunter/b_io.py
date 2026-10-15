import csv
import html
import json
import os
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
RUNS_DIR = ROOT / "paper-supplement"
DEFAULT_OUTPUT = ROOT / "paper-reference"

MIN_ABSTRACT = 120


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


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
        if not self.dir.exists():
            raise SystemExit("ERROR: run directory %s does not exist" % self.dir)

        self.papers_path = self.dir / "papers.jsonl"
        self.state_path = self.dir / "state.json"

        self.dossier_path = self.papers_path
        self.decisions_path = self.papers_path
        self.enrichment_db_path = self.papers_path
        self.manual_overrides_path = self.papers_path
        self.fulltext_db_path = self.papers_path

        self._papers_map = None
        self._state = None
        self._meta = None

    def dec_path(self, p):
        return self.papers_path

    def _get_papers_map(self):
        if self._papers_map is None:
            papers = read_jsonl(self.papers_path)
            self._papers_map = {p["id"]: p for p in papers}

            dossier_file = self.dir / "dossiers.jsonl"
            if dossier_file.exists() and dossier_file != self.papers_path:
                for d in read_jsonl(dossier_file):
                    pid = d.get("id")
                    if pid in self._papers_map:
                        p = self._papers_map[pid]
                        for k, v in d.items():
                            if k not in p:
                                p[k] = v

            enrich_file = self.dir / "enrichment.db.jsonl"
            if enrich_file.exists() and enrich_file != self.papers_path:
                for e in read_jsonl(enrich_file):
                    pid = e.get("id")
                    if pid in self._papers_map:
                        if "enrichment" not in self._papers_map[pid]:
                            self._papers_map[pid]["enrichment"] = e

            manual_file = self.dir / "manual-overrides.jsonl"
            if manual_file.exists() and manual_file != self.papers_path:
                for m in read_jsonl(manual_file):
                    pid = m.get("id")
                    if pid in self._papers_map:
                        if "manual" not in self._papers_map[pid]:
                            self._papers_map[pid]["manual"] = m

            decisions_file = self.dir / "decisions.jsonl"
            if decisions_file.exists() and decisions_file != self.papers_path:
                for dec in read_jsonl(decisions_file):
                    pid = dec.get("id")
                    if pid in self._papers_map:
                        pass_name = dec.get("pass", "A")
                        p_decs = self._papers_map[pid].setdefault("decisions", {})
                        if pass_name not in p_decs:
                            p_decs[pass_name] = dec

            final_file = self.dir / "final.jsonl"
            if final_file.exists() and final_file != self.papers_path:
                for fin in read_jsonl(final_file):
                    pid = fin.get("id")
                    if pid in self._papers_map:
                        if "final" not in self._papers_map[pid]:
                            self._papers_map[pid]["final"] = fin

            fulltext_file = self.dir / "fulltext.db.jsonl"
            if fulltext_file.exists() and fulltext_file != self.papers_path:
                for ft in read_jsonl(fulltext_file):
                    pid = ft.get("id")
                    if (
                        pid in self._papers_map
                        and "fulltext" not in self._papers_map[pid]
                    ):
                        self._papers_map[pid]["fulltext"] = ft.get("text", "")
                        self._papers_map[pid]["fulltext_source"] = ft.get("source", "")

        return self._papers_map

    def papers(self):
        return list(self._get_papers_map().values())

    def get_paper(self, paper_id):
        return self._get_papers_map().get(paper_id)

    def dossiers(self):
        return self._get_papers_map()

    def save_papers(self):
        if self._papers_map is not None:
            write_jsonl(self.papers_path, list(self._papers_map.values()))

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

    def get_manual_override(self, paper_id):
        p = self.get_paper(paper_id)
        return p.get("manual", {}) if p else {}

    def save_manual_override(self, paper_id, data):
        p = self.get_paper(paper_id)
        if p is not None:
            data["id"] = paper_id
            p["manual"] = data
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
            self.save_papers()

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
            self.save_papers()

    def final_decisions(self):
        return {
            pid: paper["final"]
            for pid, paper in self._get_papers_map().items()
            if "final" in paper
        }

    def save_final(self, final_records):
        if isinstance(final_records, list):
            for r in final_records:
                p = self.get_paper(r["id"])
                if p is not None:
                    p["final"] = r
        elif isinstance(final_records, dict):
            for pid, r in final_records.items():
                p = self.get_paper(pid)
                if p is not None:
                    p["final"] = r
        self.save_papers()

    def state(self):
        if self._state is None:
            self._state = read_json(self.state_path, self._default_state())
        return self._state

    def update_state(self, **kwargs):
        state = self.state()
        state.update(kwargs)
        write_json(self.state_path, state)
        self._state = state

    @property
    def meta(self):
        if self._meta is None:
            self._meta = self.state().get("meta", {})
        return self._meta

    def plans(self):
        return self.state().get("plans", {})

    def save_plans(self, pl):
        state = self.state()
        state["plans"] = pl
        write_json(self.state_path, state)
        self._state = state

    def _default_state(self):
        meta_file = self.dir / "meta.json"
        meta = read_json(meta_file, {}) if meta_file.exists() else {}
        plans_file = self.dir / "plans.json"
        plans = read_json(plans_file, {}) if plans_file.exists() else {}
        return {
            "version": "3.0",
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
