#!/usr/bin/env python3
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
MIN_NAME_SCORE = 0.6
MIN_NAME_SHARE = 0.15
RECENT_NAME_YEARS = 3
METHODS = ("crossref", "issn", "name")
STOPWORDS = set(
    "an the of on in for and to at with from by its de des du d la le les et fur und "
    "der die das y e em".split()
)
OPTIONAL = {"acm", "ieee", "siam", "iet", "ieice", "ipsj", "informs", "ams", "elsevier", "springer"}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("&", " and ")


def tokens(s: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", fold(s)):
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if t not in STOPWORDS:
            out.append(t)
    return out


def title_variants(title: str) -> list[list[str]]:
    base = re.sub(r"\(.*?\)", " ", title)
    variants = {title, base, re.split(r":| - ", base)[0]}
    return [t for t in (tokens(v) for v in variants) if t]


def abbreviates(d: str, s: str) -> bool:
    if d == s or s.startswith(d):
        return True
    if len(d) >= 3 and d[0] == s[0]:
        it = iter(s)
        return all(c in it for c in d)
    return False


def align(d_toks: list[str], s_toks: list[str]) -> int:
    n, m = len(d_toks), len(s_toks)
    neg = -1
    best = [[neg] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        best[0][j] = 0
    for i in range(1, n + 1):
        d = d_toks[i - 1]
        if d in OPTIONAL:
            best[i][0] = best[i - 1][0]
        for j in range(1, m + 1):
            skip_s = best[i][j - 1]
            gain = 0 if s_toks[j - 1] in OPTIONAL else 1
            take = best[i - 1][j - 1] + gain if best[i - 1][j - 1] >= 0 and abbreviates(d, s_toks[j - 1]) else neg
            skip_d = best[i - 1][j] if d in OPTIONAL else neg
            best[i][j] = max(skip_s, take, skip_d)
    return best[n][m]


def name_score(d_toks: list[str], s_toks: list[str]) -> float:
    s_core = [t for t in s_toks if t not in OPTIONAL] or s_toks
    covered = align(d_toks, s_toks)
    if covered < 0:
        return 0.0
    return min(1.0, covered / len(s_core))


def propose(conn: sqlite3.Connection) -> list[tuple]:
    scopus = conn.execute("SELECT id, title, issn_1, issn_2 FROM journal_scopus").fetchall()
    by_issn = {i: sid for sid, _, i1, i2 in scopus for i in (i1, i2) if i}
    scopus_tokens = [(sid, title, title_variants(title)) for sid, title, _, _ in scopus]
    evidence: dict[tuple[str, int], dict[str, tuple[float, str]]] = defaultdict(dict)

    def add(key: str, sid: int, method: str, score: float, detail: str) -> None:
        old = evidence[(key, sid)].get(method)
        if old is None or old[0] < score:
            evidence[(key, sid)][method] = (score, detail)

    sampled: dict[str, list[tuple]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    for key, year, status, i1, i2, title in conn.execute(
            "SELECT dblp_key, year, status, issn_1, issn_2, title FROM journal_dblp_crossref ORDER BY dblp_key, year"):
        if status == 200:
            sampled[key].append((year, i1, i2, title))
        elif status == 404:
            missing[key] += 1
    for key, rows in sampled.items():
        hits: dict[int, list[tuple]] = defaultdict(list)
        for year, i1, i2, title in rows:
            for sid in {by_issn[i] for i in (i1, i2) if i in by_issn}:
                hits[sid].append((year, i1 if i1 in by_issn else i2, title))
        for sid, h in hits.items():
            years = [y for y, _, _ in h]
            absent = f", {missing[key]} not in Crossref" if missing[key] else ""
            add(key, sid, "crossref", round(len(h) / len(rows), 2),
                f"Crossref: ISSN {h[-1][1]} on {len(h)} of {len(rows)} found DOIs{absent} "
                f"({min(years)}-{max(years)}; key's newest {rows[-1][0]}), journal '{h[-1][2]}'")
    newest_issns = {key: f"{rows[-1][1] or '?'} {rows[-1][2] or ''}".strip() + f" '{rows[-1][3]}' ({rows[-1][0]})"
                    for key, rows in sampled.items()}

    issn_total: dict[str, int] = defaultdict(int)
    issn_rows = conn.execute("SELECT dblp_key, issn, papers FROM journal_dblp_issn").fetchall()
    for key, _, n in issn_rows:
        issn_total[key] += n
    for key, issn, n in issn_rows:
        sid = by_issn.get(issn)
        if sid is not None:
            add(key, sid, "issn", round(n / issn_total[key], 2),
                f"link ISSN {issn} in {n} of {issn_total[key]} ISSN-bearing links")

    names: dict[str, set[str]] = defaultdict(set)
    rows = conn.execute("SELECT dblp_key, name, papers, last_year FROM journal_dblp_name").fetchall()
    total: dict[str, int] = defaultdict(int)
    newest: dict[str, int] = defaultdict(int)
    for key, _, n, last in rows:
        total[key] += n
        newest[key] = max(newest[key], last or 0)
    for key, name, n, last in rows:
        if n / (total[key] or 1) >= MIN_NAME_SHARE or (last or 0) >= newest[key] - RECENT_NAME_YEARS:
            names[key].add(name)
    for key, title in conn.execute("SELECT DISTINCT dblp_key, title FROM journal_dblp_crossref "
                                   "WHERE status = 200 AND title IS NOT NULL"):
        names[key].add(title)
    for key, key_names in names.items():
        for name in key_names:
            d_toks = tokens(name)
            if not d_toks:
                continue
            for sid, title, variants in scopus_tokens:
                score = round(max(name_score(d_toks, v) for v in variants), 2)
                if score >= MIN_NAME_SCORE:
                    add(key, sid, "name", score, f"name '{name}' ~ '{title}'")

    pairs = []
    for (key, sid), found in evidence.items():
        method = next(m for m in METHODS if m in found)
        detail = "; ".join(found[m][1] for m in METHODS if m in found)
        if "crossref" not in found and key in newest_issns:
            detail += f"; NO CROSSREF ISSN MATCH: the key's newest Crossref DOI says {newest_issns[key]}"
        pairs.append((key, sid, method, found[method][0], detail))
    return pairs


def main() -> None:
    if not DB_PATH.exists():
        sys.exit(f"Database not found: {DB_PATH}  (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT count(*) FROM journal_dblp_venue").fetchone()[0]:
        sys.exit("No DBLP evidence in the database  (run a_extract.py first)")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Journal Candidate Matcher                    ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    pairs = propose(conn)
    with conn:
        conn.execute("DELETE FROM journal_candidate")
        conn.executemany("INSERT INTO journal_candidate VALUES (?, ?, ?, ?, ?)", pairs)
    issues = dict(conn.execute("SELECT issue, count(*) FROM journal_review GROUP BY issue").fetchall())
    decisions = conn.execute("SELECT count(*) FROM journal_decision").fetchone()[0]
    final = conn.execute("SELECT count(*) FROM journal_list").fetchone()[0]
    by_method = dict(conn.execute("SELECT method, count(*) FROM journal_candidate GROUP BY method").fetchall())
    print(f"  ✓  {len(pairs)} candidate pairs ({len({p[0] for p in pairs})} DBLP keys)"
          f"  |  Crossref {by_method.get('crossref', 0)}  |  link ISSN {by_method.get('issn', 0)}"
          f"  |  name {by_method.get('name', 0)}")
    print(f"  ├─ decisions:   {decisions}")
    print(f"  ├─ UNREVIEWED:  {issues.get('UNREVIEWED', 0)}")
    print(f"  ├─ UNCOVERED:   {issues.get('UNCOVERED', 0)}")
    print(f"  └─ final list:  {final} keys")
    print()
    if issues:
        print("  ✗ Review needed → give journal-hunter/c_journal_matching_guide.md to the AI")
    else:
        print("  ✓ Review complete → journal_list is final")
    print()
    conn.close()


if __name__ == "__main__":
    main()
