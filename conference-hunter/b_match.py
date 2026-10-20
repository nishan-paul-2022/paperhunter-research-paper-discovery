#!/usr/bin/env python3
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
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


def load_evidence(conn: sqlite3.Connection) -> dict[str, dict]:
    booktitles: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for key, booktitle, n in conn.execute(
        "SELECT dblp_key, booktitle, papers FROM dblp_booktitle ORDER BY dblp_key, papers DESC, booktitle"
    ):
        if len(booktitles[key]) < 5:
            booktitles[key].append((booktitle, n))
    proceedings: dict[str, list[str]] = defaultdict(list)
    for key, title in conn.execute(
        "SELECT dblp_key, title FROM dblp_proceedings ORDER BY dblp_key, year DESC, title DESC"
    ):
        if len(proceedings[key]) < 3:
            proceedings[key].append(title)
    evidence = {}
    for (key,) in conn.execute("SELECT dblp_key FROM dblp_venue"):
        bts = booktitles.get(key, [])
        total = sum(n for _, n in bts) or 1
        dominant = {
            norm(re.sub(r"\(.*?\)|@.*$", "", b)) for b, n in bts if n / total >= MIN_BOOKTITLE_SHARE
        }
        pts = proceedings.get(key, [])
        acronyms = {norm(key.split("/", 1)[1])} | dominant
        for p in pts:
            acronyms |= edition_acronyms(p)
        evidence[key] = {
            "acronyms": acronyms - {""},
            "pt_tokens": [tokens(p) for p in pts],
            "pts": pts,
            "co_located": bool(pts) and all(CO_LOCATED.search(p) for p in pts),
        }
    return evidence


def propose(conn: sqlite3.Connection) -> list[tuple]:
    evidence = load_evidence(conn)
    by_acronym: dict[str, set[str]] = defaultdict(set)
    for key, e in evidence.items():
        for a in e["acronyms"]:
            by_acronym[a].add(key)
    pairs = []
    for core_id, title, acronym in conn.execute("SELECT id, title, acronym FROM core_venue"):
        ct = tokens(strip_parens(title))
        if not ct:
            continue
        keys = set().union(*(by_acronym.get(a, set()) for a in acronym_forms(acronym)))
        for key in keys:
            e = evidence[key]
            if e["co_located"] and "workshop" not in title.lower():
                continue
            scores = [len(ct & pt) / len(ct) for pt in e["pt_tokens"]]
            if not scores or max(scores) < MIN_TITLE_SCORE:
                continue
            best = max(range(len(scores)), key=scores.__getitem__)
            pairs.append((key, core_id, round(scores[best], 2), e["pts"][best]))
    return pairs


def main() -> None:
    if not DB_PATH.exists():
        sys.exit(f"Database not found: {DB_PATH}  (run archive/schema.py first)")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT count(*) FROM dblp_venue").fetchone()[0]:
        sys.exit("No DBLP evidence in the database  (run a_extract.py first)")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Conference Candidate Matcher                 ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    pairs = propose(conn)
    with conn:
        conn.execute("DELETE FROM conf_candidate")
        conn.executemany("INSERT INTO conf_candidate VALUES (?, ?, ?, ?)", pairs)
    issues = dict(conn.execute("SELECT issue, count(*) FROM conf_needs_review GROUP BY issue").fetchall())
    decisions = conn.execute("SELECT count(*) FROM conf_decision").fetchone()[0]
    final = conn.execute("SELECT count(*) FROM list_of_conferences").fetchone()[0]
    print(f"  ✓  {len(pairs)} candidate pairs ({len({p[0] for p in pairs})} DBLP keys)")
    print(f"  ├─ decisions:   {decisions}")
    print(f"  ├─ UNREVIEWED:  {issues.get('UNREVIEWED', 0)}")
    print(f"  ├─ UNCOVERED:   {issues.get('UNCOVERED', 0)}")
    print(f"  └─ final list:  {final} keys")
    print()
    if issues:
        print("  ✗ Review needed → give conference-hunter/c_conference_matching_guide.md to the AI")
    else:
        print("  ✓ Review complete → list_of_conferences is final")
    print()
    conn.close()


if __name__ == "__main__":
    main()
