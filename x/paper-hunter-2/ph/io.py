"""CSV/JSONL helpers, paper normalisation, run-directory layout."""
import csv, json, re, hashlib, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_YEAR, MAX_YEAR = 2023, 2026

def run_dir(csv_path):
    d = ROOT / "runs" / Path(csv_path).stem
    (d / "out").mkdir(parents=True, exist_ok=True)
    return d

def read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass  # tolerate a torn last line from an interrupted write
    return out

def append_jsonl(path, obj):
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def write_jsonl(path, rows):
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)

def latest_by_id(rows):
    """Later rows win (append-only logs)."""
    d = {}
    for r in rows:
        d[r["id"]] = r
    return d

def norm_title(t):
    t = unicodedata.normalize("NFKD", t or "").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def clean_title(t):
    t = re.sub(r"\s+", " ", (t or "").strip())
    return t[:-1] if t.endswith(".") and not t.endswith("..") else t

def doi_from(url):
    m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", url or "")
    return m.group(0).rstrip(".,;)") if m else ""

def read_input_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def load_papers(path):
    """Return (papers, report). Never drops a paper except exact duplicates or out-of-range year."""
    rows = read_input_csv(path)
    stem = Path(path).stem
    seen_key, seen_doi, seen_title = {}, {}, {}
    papers, rep = [], {"rows": len(rows), "kept": 0, "duplicates": [], "out_of_range": [], "no_doi": 0, "bad_year": []}
    for i, r in enumerate(rows, 1):
        title = clean_title(r.get("title", ""))
        try:
            year = int(str(r.get("year", "")).strip())
        except ValueError:
            year = None
            rep["bad_year"].append(r.get("dblp_key", i))
        if year is not None and not (MIN_YEAR <= year <= MAX_YEAR):
            rep["out_of_range"].append(r.get("dblp_key", i)); continue
        ee = (r.get("ee") or "").strip()
        doi = doi_from(ee)
        key = (r.get("dblp_key") or "").strip()
        nt = norm_title(title)
        dup_of = seen_key.get(key) if key else None
        dup_of = dup_of or (seen_doi.get(doi.lower()) if doi else None) or seen_title.get(nt)
        if dup_of:
            rep["duplicates"].append({"dropped_row": i, "title": title, "duplicate_of": dup_of}); continue
        pid = f"{stem}_{i:05d}"          # id = original row number: stable even if rows are later dropped
        if key: seen_key[key] = pid
        if doi: seen_doi[doi.lower()] = pid
        seen_title[nt] = pid
        if not doi: rep["no_doi"] += 1
        papers.append({
            "id": pid, "row": i, "dblp_key": key, "title": title,
            "authors": (r.get("authors") or "").strip(), "year": year,
            "venue": (r.get("venue") or "").strip(), "ee": ee, "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else "", "url": (r.get("url") or "").strip(),
            "dblp_type": key.split("/")[0] if key else "",
            "extra": {k: v for k, v in r.items() if k not in
                      ("dblp_key", "title", "authors", "year", "venue", "ee", "url")},
        })
    rep["kept"] = len(papers)
    return papers, rep
