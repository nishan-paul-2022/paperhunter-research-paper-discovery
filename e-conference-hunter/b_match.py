#!/usr/bin/env python3
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "b-config" / "paperhunter.db"
MIN_BOOK_TITLE_SHARE = 0.15
MIN_SHARED_BOOK_TITLE_PAPERS = 20
MIN_FULL_TITLE_LEN = 20
METHODS = ("acronym", "related", "lineage", "title")
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


def strip_parens(s: str) -> str:
    return re.sub(r"\(.*?\)", "", s)


def acronym_forms(acronym: str, core_title: str = "") -> set[str]:
    a = strip_parens(acronym).strip()
    forms = {norm(a), norm(ORG_PREFIX.sub("", a))}
    for inner in re.findall(r"\((.*?)\)", f"{acronym} {core_title}"):
        forms |= {norm(x) for x in FORMER_ACRONYM.findall(inner)}
    return {f for f in forms if f} - ORGS


def key_forms(acronym: str) -> set[str]:
    a = norm(acronym)
    return {a, KEY_ORG_PREFIX.sub("", a), re.sub(r"\d+$", "", a)} - {""}


def edition_acronyms(proceeding_title: str) -> set[str]:
    text = re.sub(r"[^a-z0-9 ']", "", fold(proceeding_title).replace("-", ""))
    return set(EDITION.findall(text))


def load_evidence(conn: sqlite3.Connection) -> dict[str, dict]:
    book_titles: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for key, book_title, n in conn.execute("SELECT dblp_key, book_title, papers FROM conf_dblp_book_title"):
        book_titles[key].append((book_title, n))
    proceeding_titles: dict[str, list[str]] = defaultdict(list)
    for key, proceeding_title in conn.execute("SELECT dblp_key, proceeding_title FROM conf_dblp_proceeding_title"):
        proceeding_titles[key].append(proceeding_title)
    evidence = {}
    for key, dblp_acronym in conn.execute("SELECT dblp_key, dblp_acronym FROM conf_dblp_venue"):
        bts = book_titles.get(key, [])
        total = sum(n for _, n in bts) or 1
        acronyms = key_forms(dblp_acronym)
        primary = set(acronyms)
        for b, n in bts:
            acronyms.add(norm(re.sub(r"\(.*?\)|@.*$", "", b)))
            if "@" not in b and n / total >= MIN_BOOK_TITLE_SHARE:
                primary.add(norm(strip_parens(b)))
            if key.startswith("journals/") and n / total >= MIN_BOOK_TITLE_SHARE:
                acronyms |= {norm(t) for t in CAPS_TOKEN.findall(b)} - ORGS
        for p in proceeding_titles.get(key, []):
            acronyms |= edition_acronyms(p)
        evidence[key] = {"acronyms": acronyms - {""}, "primary": primary - {""}}
    return evidence


def main_book_titles(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for key, book_title in conn.execute(
        "SELECT dblp_key, book_title FROM conf_dblp_book_title WHERE book_title NOT LIKE '%@%' AND papers >= ?",
        (MIN_SHARED_BOOK_TITLE_PAPERS,),
    ):
        out[key].add(book_title)
    return out


def related_keys(keys: set[str], all_keys: list[str], book_titles: dict[str, set[str]],
                 by_book_title: dict[str, set[str]]) -> dict[str, set[str]]:
    related: dict[str, set[str]] = defaultdict(set)
    for key in keys:
        if key.startswith("conf/"):
            for k in all_keys:
                if k.startswith(key + "-"):
                    related[k].add(f"track of {key}")
        for b in book_titles.get(key, ()):
            for k in by_book_title[b] - {key}:
                related[k].add(f'shares book_title "{b}" with {key}')
    return related


def full_title_keys(core_title: str, proceeding_title_blobs: dict[str, str]) -> set[str]:
    name = fold(strip_parens(core_title)).strip()
    if len(name) < MIN_FULL_TITLE_LEN:
        return set()
    return {key for key, blob in proceeding_title_blobs.items() if name in blob}


def lineage_graph(conn: sqlite3.Connection) -> dict[str, dict[str, set[str]]]:
    graph: dict[str, dict[str, set[str]]] = {"predecessor": defaultdict(set), "successor": defaultdict(set)}
    for pred, succ in conn.execute("SELECT predecessor_key, successor_key FROM conf_dblp_lineage"):
        graph["successor"][pred].add(succ)
        graph["predecessor"][succ].add(pred)
    return graph


def lineage_keys(keys: set[str], graph: dict[str, dict[str, set[str]]]) -> dict[str, set[str]]:
    reached: dict[str, set[str]] = defaultdict(set)
    for source in keys:
        for relation, edges in graph.items():
            seen, queue = {source}, [source]
            while queue:
                for other in edges.get(queue.pop(), ()):
                    if other not in seen:
                        seen.add(other)
                        queue.append(other)
            for k in seen - {source}:
                reached[k].add(f"{relation} of {source}")
    return reached


def propose(conn: sqlite3.Connection) -> list[tuple]:
    evidence = load_evidence(conn)
    by_acronym: dict[str, set[str]] = defaultdict(set)
    by_primary: dict[str, set[str]] = defaultdict(set)
    for key, e in evidence.items():
        for a in e["acronyms"]:
            by_acronym[a].add(key)
        for a in e["primary"]:
            by_primary[a].add(key)
    book_titles = main_book_titles(conn)
    by_book_title: dict[str, set[str]] = defaultdict(set)
    for key, bts in book_titles.items():
        for b in bts:
            by_book_title[b].add(key)
    all_keys = sorted(evidence)
    lineage = lineage_graph(conn)
    proceeding_title_blobs: dict[str, str] = defaultdict(str)
    for key, proceeding_title in conn.execute("SELECT dblp_key, proceeding_title FROM conf_dblp_proceeding_title"):
        proceeding_title_blobs[key] += fold(proceeding_title) + "\n"
    pairs = []
    for core_id, core_title, core_acronym in conn.execute("SELECT id, core_title, core_acronym FROM conf_core"):
        forms = acronym_forms(core_acronym, core_title)
        found: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for a in forms:
            for key in by_acronym.get(a, ()):
                found[key]["acronym"].add(f'acronym "{a}"')
        primary = set().union(*(by_primary.get(a, set()) for a in forms))
        for key, why in related_keys(primary, all_keys, book_titles, by_book_title).items():
            found[key]["related"] |= why
        for key, why in lineage_keys(primary, lineage).items():
            found[key]["lineage"] |= why
        for key in full_title_keys(core_title, proceeding_title_blobs):
            found[key]["title"].add("proceeding_titles contain the core_title")
        for key, hits in found.items():
            methods = [m for m in METHODS if m in hits]
            detail = "; ".join(d for m in methods for d in sorted(hits[m]))
            pairs.append((key, core_id, ",".join(methods), detail))
    return pairs


def main() -> None:
    if not DB_PATH.exists():
        sys.exit(f"Database not found: {DB_PATH}  (run c-context/b_schema.py first)")
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
        conn.execute("DELETE FROM conf_candidate_rule")
        conn.executemany("INSERT INTO conf_candidate_rule VALUES (?, ?, ?, ?)", pairs)
    issues = dict(conn.execute("SELECT issue, count(*) FROM conf_review GROUP BY issue").fetchall())
    decisions = conn.execute("SELECT count(*) FROM conf_candidate_ai").fetchone()[0]
    final = conn.execute("SELECT count(*) FROM conf_list").fetchone()[0]
    print(f"  ✓  {len(pairs)} candidate pairs ({len({p[0] for p in pairs})} DBLP keys)")
    print(f"  ├─ decisions:   {decisions}")
    print(f"  ├─ UNREVIEWED:  {issues.get('UNREVIEWED', 0)}")
    print(f"  ├─ UNCOVERED:   {issues.get('UNCOVERED', 0)}")
    print(f"  └─ final list:  {final} keys")
    print()
    if issues:
        print("  ✗ Review needed → give e-conference-hunter/c_search.md to the AI")
    else:
        print("  ✓ Review complete → conf_list is final")
    print()
    conn.close()


if __name__ == "__main__":
    main()
