#!/usr/bin/env python3
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
MIN_BOOKTITLE_SHARE = 0.15
MIN_SHARED_BOOKTITLE_PAPERS = 20
MIN_FULL_TITLE_LEN = 20
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
ORG_PREFIX = re.compile(r"^(IEEE|ACM|IFIP)[ _-]+", re.I)
KEY_ORG_PREFIX = re.compile(r"^(ieee|acm|ifip|acis)(?=[a-z]{2})")
FORMER_ACRONYM = re.compile(r"\b[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b")
CAPS_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]+\b")
ORGS = {"ieee", "acm", "ifip", "siam", "usenix", "iacr", "iet", "ieice", "ipsj", "aaai", "spie", "iapr", "lncs", "ceur"}
EDITION = re.compile(r"\b([a-z][a-z0-9]*)\s*(?:(?:19|20)\d{2}|'\d{2})\b")


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


def acronym_forms(acronym: str, title: str = "") -> set[str]:
    a = strip_parens(acronym).strip()
    forms = {norm(a), norm(ORG_PREFIX.sub("", a))}
    for inner in re.findall(r"\((.*?)\)", f"{acronym} {title}"):
        forms |= {norm(x) for x in FORMER_ACRONYM.findall(inner)}
    return {f for f in forms if f} - ORGS


def key_forms(acronym: str) -> set[str]:
    a = norm(acronym)
    return {a, KEY_ORG_PREFIX.sub("", a), re.sub(r"\d+$", "", a)} - {""}


def edition_acronyms(proceedings_title: str) -> set[str]:
    text = re.sub(r"[^a-z0-9 ']", "", fold(proceedings_title).replace("-", ""))
    return set(EDITION.findall(text))


def load_evidence(conn: sqlite3.Connection) -> dict[str, dict]:
    booktitles: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for key, booktitle, n in conn.execute("SELECT dblp_key, booktitle, papers FROM conf_dblp_booktitle"):
        booktitles[key].append((booktitle, n))
    proceedings: dict[str, list[str]] = defaultdict(list)
    for key, title in conn.execute("SELECT dblp_key, title FROM conf_dblp_title ORDER BY dblp_key, year DESC"):
        proceedings[key].append(title)
    evidence = {}
    for key, acronym in conn.execute("SELECT dblp_key, acronym FROM conf_dblp_venue"):
        bts = booktitles.get(key, [])
        total = sum(n for _, n in bts) or 1
        acronyms = key_forms(acronym)
        primary = set(acronyms)
        for b, n in bts:
            acronyms.add(norm(re.sub(r"\(.*?\)|@.*$", "", b)))
            if "@" not in b and n / total >= MIN_BOOKTITLE_SHARE:
                primary.add(norm(strip_parens(b)))
            if key.startswith("journals/") and n / total >= MIN_BOOKTITLE_SHARE:
                acronyms |= {norm(t) for t in CAPS_TOKEN.findall(b)} - ORGS
        pts = proceedings.get(key, [])
        for p in pts:
            acronyms |= edition_acronyms(p)
        texts = pts + [b for b, _ in bts]
        evidence[key] = {
            "acronyms": acronyms - {""},
            "primary": primary - {""},
            "texts": texts,
            "text_tokens": [tokens(t) for t in texts],
        }
    return evidence


def main_booktitles(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for key, booktitle in conn.execute(
        "SELECT dblp_key, booktitle FROM conf_dblp_booktitle WHERE booktitle NOT LIKE '%@%' AND papers >= ?",
        (MIN_SHARED_BOOKTITLE_PAPERS,),
    ):
        out[key].add(booktitle)
    return out


def related_keys(keys: set[str], all_keys: list[str], booktitles: dict[str, set[str]],
                 by_booktitle: dict[str, set[str]]) -> set[str]:
    related = set()
    for key in keys:
        if key.startswith("conf/"):
            related |= {k for k in all_keys if k.startswith(key + "-")}
        for b in booktitles.get(key, ()):
            related |= by_booktitle[b]
    return related


def full_title_keys(title: str, title_blobs: dict[str, str]) -> set[str]:
    name = fold(strip_parens(title)).strip()
    if len(name) < MIN_FULL_TITLE_LEN:
        return set()
    return {key for key, blob in title_blobs.items() if name in blob}


def lineage_graph(conn: sqlite3.Connection) -> dict[str, dict[str, set[str]]]:
    graph: dict[str, dict[str, set[str]]] = {"predecessor": defaultdict(set), "successor": defaultdict(set)}
    for pred, succ in conn.execute("SELECT predecessor_key, successor_key FROM conf_dblp_lineage"):
        graph["successor"][pred].add(succ)
        graph["predecessor"][succ].add(pred)
    return graph


def lineage_keys(keys: set[str], graph: dict[str, dict[str, set[str]]]) -> set[str]:
    reached = set()
    for edges in graph.values():
        seen, queue = set(keys), list(keys)
        while queue:
            for other in edges.get(queue.pop(), ()):
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        reached |= seen
    return reached - keys


def propose(conn: sqlite3.Connection) -> list[tuple]:
    evidence = load_evidence(conn)
    by_acronym: dict[str, set[str]] = defaultdict(set)
    by_primary: dict[str, set[str]] = defaultdict(set)
    for key, e in evidence.items():
        for a in e["acronyms"]:
            by_acronym[a].add(key)
        for a in e["primary"]:
            by_primary[a].add(key)
    booktitles = main_booktitles(conn)
    by_booktitle: dict[str, set[str]] = defaultdict(set)
    for key, bts in booktitles.items():
        for b in bts:
            by_booktitle[b].add(key)
    all_keys = sorted(evidence)
    lineage = lineage_graph(conn)
    title_blobs: dict[str, str] = defaultdict(str)
    for key, title in conn.execute("SELECT dblp_key, title FROM conf_dblp_title"):
        title_blobs[key] += fold(title) + "\n"
    pairs = []
    for core_id, title, acronym in conn.execute("SELECT id, title, acronym FROM conf_core_venue"):
        ct = tokens(strip_parens(title)) or tokens(title)
        forms = acronym_forms(acronym, title)
        keys = set().union(*(by_acronym.get(a, set()) for a in forms))
        primary = set().union(*(by_primary.get(a, set()) for a in forms))
        keys |= related_keys(primary, all_keys, booktitles, by_booktitle)
        keys |= lineage_keys(primary, lineage)
        keys |= full_title_keys(title, title_blobs)
        for key in keys:
            e = evidence[key]
            scores = [len(ct & t) / len(ct) if ct else 0.0 for t in e["text_tokens"]] or [0.0]
            best = max(range(len(scores)), key=scores.__getitem__)
            pairs.append((key, core_id, round(scores[best], 2), e["texts"][best] if e["texts"] else ""))
    return pairs


def main() -> None:
    if not DB_PATH.exists():
        sys.exit(f"Database not found: {DB_PATH}  (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT count(*) FROM conf_dblp_venue").fetchone()[0]:
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
    issues = dict(conn.execute("SELECT issue, count(*) FROM conf_review GROUP BY issue").fetchall())
    decisions = conn.execute("SELECT count(*) FROM conf_decision").fetchone()[0]
    final = conn.execute("SELECT count(*) FROM conf_list").fetchone()[0]
    print(f"  ✓  {len(pairs)} candidate pairs ({len({p[0] for p in pairs})} DBLP keys)")
    print(f"  ├─ decisions:   {decisions}")
    print(f"  ├─ UNREVIEWED:  {issues.get('UNREVIEWED', 0)}")
    print(f"  ├─ UNCOVERED:   {issues.get('UNCOVERED', 0)}")
    print(f"  └─ final list:  {final} keys")
    print()
    if issues:
        print("  ✗ Review needed → give conference-hunter/c_search.md to the AI")
    else:
        print("  ✓ Review complete → conf_list is final")
    print()
    conn.close()


if __name__ == "__main__":
    main()
