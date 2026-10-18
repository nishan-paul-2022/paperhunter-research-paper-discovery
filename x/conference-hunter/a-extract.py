#!/usr/bin/env python3
import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
SUPPLEMENT_DIR = REPO_ROOT / "conference-supplement"
CORE_SRC = ARCHIVE_DIR / "publication-core-2026.csv"
XML_PATH = ARCHIVE_DIR / "paper-dblp.xml"
OUT_1_VENUES = SUPPLEMENT_DIR / "1-dblp-venues.csv"
OUT_2_EXACT = SUPPLEMENT_DIR / "2-exact-matched.csv"
OUT_3_TODO = SUPPLEMENT_DIR / "3-dblp-unmatched.csv"
OUT_4_CORE_POOL = SUPPLEMENT_DIR / "4-source-pool.csv"
OUT_FIELDS_VENUES = [
    "dblp_key",
    "dblp_acronym",
    "papers",
    "first_year",
    "last_year",
    "booktitles",
    "proceedings_titles",
]
OUT_FIELDS_EXACT = [
    "dblp_key",
    "dblp_acronym",
    "title",
    "acronym",
    "ranking",
    "score",
    "proceedings_title",
]
OUT_FIELDS_DBLP = OUT_FIELDS_VENUES
OUT_FIELDS_CORE = ["title", "acronym", "ranking"]
VALID_RANKINGS = {"A*", "A", "B", "C"}
SEP = " | "
MIN_TITLE_SCORE = 0.6
MIN_BOOKTITLE_SHARE = 0.15
STOPWORDS = set(
    "a an the of on in for and to at with from by its their international "
    "conference conf symposium workshop workshops congress meeting annual "
    "joint ieee acm ifip usenix siam proceedings th st nd rd was previously "
    "changed".split()
)
SPELLING = {
    "modelling": "modeling",
    "optimisation": "optimization",
    "visualisation": "visualization",
    "organisation": "organization",
    "behaviour": "behavior",
    "analyse": "analyze",
}
CO_LOCATED = re.compile(r"co-located|colocated|in conjunction|held at|workshop", re.I)
ORG_PREFIX = re.compile(r"^(IEEE|ACM|IFIP)[ _-]+", re.I)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("&", " and ")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(s))


def tokens(s: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", fold(s)):
        t = SPELLING.get(t, t)
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if t not in STOPWORDS and not t.isdigit():
            out.add(t)
    return out


def strip_parens(s: str) -> str:
    return re.sub(r"\(.*?\)", "", s)


def acronym_forms(acronym: str) -> set[str]:
    a = strip_parens(acronym).strip()
    return {f for f in (norm(a), norm(ORG_PREFIX.sub("", a))) if f}


def edition_acronyms(proceedings_title: str) -> set[str]:
    text = re.sub(r"[^a-z0-9 ']", "", fold(proceedings_title).replace("-", ""))
    return set(re.findall(r"\b([a-z][a-z0-9]*)\s*'?(?:19|20)?\d{2}\b", text))


def phase_clean(core_path: Path) -> list[dict]:
    print("━" * 70)
    print("  Phase 1 – Cleaning CORE CSV")
    print("━" * 70)
    rows = []
    dropped = 0
    with open(core_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ranking = row.get("Ranking", "").strip()
            if ranking not in VALID_RANKINGS:
                dropped += 1
                continue
            rows.append(
                {
                    "title": row["Title"].strip(),
                    "acronym": row["Acronym"].strip(),
                    "ranking": ranking,
                }
            )
    titles = Counter(r["title"] for r in rows)
    duplicates = [t for t, n in titles.items() if n > 1]
    if duplicates:
        sys.exit(f"Duplicate CORE titles (titles must be unique ids): {duplicates}")
    print(f"  ✓  {len(rows)} valid conferences  |  {dropped} invalid rows dropped\n")
    return rows


def _scan_xml(xml_path: Path) -> dict[str, dict]:
    from lxml import etree

    size = os.path.getsize(xml_path)
    seen = 0
    papers: Counter = Counter()
    booktitles: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, list[int]] = {}
    proceedings: dict[str, list[tuple[int, str]]] = defaultdict(list)
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag=("inproceedings", "proceedings"),
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        parts = key.split("/")
        if key.startswith("conf/") and len(parts) >= 3:
            prefix = "/".join(parts[:2])
            year_text = el.findtext("year") or ""
            year = int(year_text) if year_text.isdigit() else 0
            if el.tag == "inproceedings":
                papers[prefix] += 1
                booktitle = (el.findtext("booktitle") or "").strip()
                if booktitle:
                    booktitles[prefix][booktitle] += 1
                if year:
                    lo, hi = years.get(prefix, (year, year))
                    years[prefix] = [min(lo, year), max(hi, year)]
            else:
                title_el = el.find("title")
                if title_el is not None:
                    title = " ".join("".join(title_el.itertext()).split())
                    if title:
                        proceedings[prefix].append((year, title))
        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(
                f"  {pct:3}%  |  records scanned: {seen:,}"
                f"  |  conference keys: {len(papers):,}",
                end="\r",
                flush=True,
            )
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    f.close()
    print(f"  100%  |  records scanned: {seen:,}  |  conference keys: {len(papers):,}")
    venues = {}
    for prefix, n in papers.items():
        latest = sorted(proceedings.get(prefix, []), reverse=True)[:3]
        first, last = years.get(prefix, ("", ""))
        venues[prefix] = {
            "dblp_key": prefix,
            "dblp_acronym": prefix.split("/", 1)[1].upper(),
            "papers": n,
            "first_year": first,
            "last_year": last,
            "booktitles": SEP.join(
                f"{b} ({c})" for b, c in booktitles[prefix].most_common(5)
            ),
            "proceedings_titles": SEP.join(t for _, t in latest),
        }
    return venues


def phase_index(xml_path: Path, reuse: bool) -> dict[str, dict]:
    print("━" * 70)
    print("  Phase 2 – DBLP Venue Evidence Index")
    print("━" * 70)
    if reuse and OUT_1_VENUES.exists():
        with open(OUT_1_VENUES, encoding="utf-8", newline="") as f:
            venues = {r["dblp_key"]: r for r in csv.DictReader(f)}
        print(f"  Reusing {OUT_1_VENUES.name}  ({len(venues):,} keys)\n")
        return venues
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB)")
    print("  (This takes ~15–20 min.)\n")
    venues = _scan_xml(xml_path)
    SUPPLEMENT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_1_VENUES, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS_VENUES)
        writer.writeheader()
        writer.writerows(sorted(venues.values(), key=lambda r: r["dblp_key"]))
    print(f"\n  ✓  {len(venues):,} conference keys indexed → {OUT_1_VENUES.name}\n")
    return venues


def _booktitle_share(field: str) -> list[tuple[str, int]]:
    out = []
    for part in field.split(SEP) if field else []:
        m = re.match(r"^(.*) \((\d+)\)$", part)
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def venue_evidence(v: dict) -> dict:
    bts = _booktitle_share(v["booktitles"])
    total = sum(n for _, n in bts) or 1
    dominant = {
        norm(re.sub(r"\(.*?\)|@.*$", "", b)) for b, n in bts if n / total >= MIN_BOOKTITLE_SHARE
    }
    pts = [p for p in v["proceedings_titles"].split(SEP) if p]
    acronyms = {norm(v["dblp_key"].split("/", 1)[1])} | dominant
    for p in pts:
        acronyms |= edition_acronyms(p)
    return {
        "acronyms": acronyms - {""},
        "pt_tokens": [tokens(p) for p in pts],
        "pts": pts,
        "co_located": bool(pts) and all(CO_LOCATED.search(p) for p in pts),
    }


def phase_match(
    venues: dict[str, dict], core_rows: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    print("━" * 70)
    print("  Phase 3 – Evidence Matching (acronym AND proceedings-title agreement)")
    print("━" * 70)
    evidence = {k: venue_evidence(v) for k, v in venues.items()}
    by_acronym: dict[str, set[str]] = defaultdict(set)
    for k, e in evidence.items():
        for a in e["acronyms"]:
            by_acronym[a].add(k)
    candidates = []
    for c in core_rows:
        ct = tokens(strip_parens(c["title"]))
        if not ct:
            continue
        keys = set().union(*(by_acronym.get(a, set()) for a in acronym_forms(c["acronym"])))
        for k in keys:
            e = evidence[k]
            if e["co_located"] and "workshop" not in c["title"].lower():
                continue
            scores = [len(ct & pt) / len(ct) for pt in e["pt_tokens"]]
            if not scores or max(scores) < MIN_TITLE_SCORE:
                continue
            best = max(range(len(scores)), key=scores.__getitem__)
            candidates.append(
                {
                    "dblp_key": k,
                    "dblp_acronym": venues[k]["dblp_acronym"],
                    "title": c["title"],
                    "acronym": c["acronym"],
                    "ranking": c["ranking"],
                    "score": f"{scores[best]:.2f}",
                    "proceedings_title": e["pts"][best],
                }
            )
    matched_keys = {r["dblp_key"] for r in candidates}
    matched_titles = {r["title"] for r in candidates}
    dblp_unmatched = [v for k, v in venues.items() if k not in matched_keys]
    core_available = [c for c in core_rows if c["title"] not in matched_titles]
    print(f"  ✓  {len(candidates):,} candidate pairs  ({len(matched_keys):,} keys)")
    print(f"  ├─ DBLP keys without candidate: {len(dblp_unmatched):,}")
    print(f"  └─ CORE venues without candidate: {len(core_available):,}\n")
    return candidates, dblp_unmatched, core_available


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def phase_write(
    candidates: list[dict], dblp_unmatched: list[dict], core_available: list[dict]
) -> None:
    print("━" * 70)
    print("  Phase 4 – Writing Output Files")
    print("━" * 70)
    order = {"A*": 0, "A": 1, "B": 2, "C": 3}
    SUPPLEMENT_DIR.mkdir(parents=True, exist_ok=True)
    _write(
        OUT_2_EXACT,
        OUT_FIELDS_EXACT,
        sorted(candidates, key=lambda r: (order[r["ranking"]], r["title"].lower(), r["dblp_key"])),
    )
    _write(OUT_3_TODO, OUT_FIELDS_DBLP, sorted(dblp_unmatched, key=lambda r: r["dblp_key"]))
    _write(
        OUT_4_CORE_POOL,
        OUT_FIELDS_CORE,
        sorted(core_available, key=lambda r: (order[r["ranking"]], r["title"].lower())),
    )
    print(f"  File 2: {OUT_2_EXACT.name}  ({len(candidates)} evidence candidates, NOT auto-approved)")
    print(f"  File 3: {OUT_3_TODO.name}  ({len(dblp_unmatched)} keys, with evidence columns)")
    print(f"  File 4: {OUT_4_CORE_POOL.name}  ({len(core_available)} CORE venues)\n")
    print("  NEXT STEP:")
    print("  → Run b-match.py (applies reviewed decisions in 7-decisions.csv)\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", default=str(XML_PATH), help=f"Path to paper-dblp.xml  (default: {XML_PATH.name})")
    ap.add_argument("--core", default=str(CORE_SRC), help=f"Path to CORE CSV  (default: {CORE_SRC.name})")
    ap.add_argument(
        "--reuse-index",
        action="store_true",
        help=f"Reuse {OUT_1_VENUES.name} instead of rescanning the XML",
    )
    args = ap.parse_args()
    xml_path = Path(args.xml)
    core_path = Path(args.core)
    if not core_path.exists():
        sys.exit(f"CORE CSV not found: {core_path}")
    if not (args.reuse_index and OUT_1_VENUES.exists()) and not xml_path.exists():
        sys.exit(f"paper-dblp.xml not found: {xml_path}\nRun the downloader first.")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Conference List Builder                      ║")
    print("║                                                                      ║")
    print("║  Strategy: DBLP evidence (booktitles + proceedings titles) vs CORE   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    core_rows = phase_clean(core_path)
    venues = phase_index(xml_path, args.reuse_index)
    candidates, dblp_unmatched, core_available = phase_match(venues, core_rows)
    phase_write(candidates, dblp_unmatched, core_available)
    print("━" * 70)
    print("  Done.  → Run b-match.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
