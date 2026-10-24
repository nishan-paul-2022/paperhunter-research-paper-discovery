#!/usr/bin/env python3
import importlib.util
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
XML_PATH = REPO_ROOT / "archive" / "dblp.xml"
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
SCHEMA_PATH = REPO_ROOT / "circumstance" / "b_schema.py"
RECORD_TAGS = ("article", "inproceedings", "proceedings", "book", "incollection",
               "phdthesis", "mastersthesis", "www", "person", "data")
YEAR_GAP = 3
CLOSE_SIM = 0.90
CAND_SIM = 0.60
CAND_JACCARD = 0.50
PER_PAPER = 3


def paper_schema() -> str:
    spec = importlib.util.spec_from_file_location("b_schema", SCHEMA_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PAPER_SCHEMA


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run paper-hunter/a_extract.py first)")
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def norm(title: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower()).split())


def name_prefix(title: str | None) -> str:
    head, sep, _ = (title or "").partition(":")
    return norm(head) if sep and len(head.split()) <= 3 else ""


def scan_corr(xml_path: Path, first_year: int) -> list[tuple[str, str, str, int, list[str]]]:
    from lxml import etree

    corr = []
    with open(xml_path, "rb") as f:
        for _, el in etree.iterparse(f, events=("end",), tag=RECORD_TAGS, load_dtd=True,
                                     resolve_entities=True, huge_tree=True):
            key = el.get("key", "")
            if el.tag == "article" and key.startswith("journals/corr/"):
                year = (el.findtext("year") or "").strip()
                if year.isdigit() and int(year) >= first_year:
                    arxiv = None
                    for e in el.findall("ee"):
                        m = re.search(r"arxiv\.org/abs/([^\s?#]+?)(?:v\d+)?$", (e.text or "").strip())
                        if m:
                            arxiv = m.group(1).lower()
                            break
                    if arxiv is None:
                        m = re.match(r"journals/corr/abs-(\d{4})-(\d{4,5})$", key)
                        arxiv = f"{m.group(1)}.{m.group(2)}" if m else None
                    t = el.find("title")
                    title = " ".join("".join(t.itertext()).split()) if t is not None else ""
                    authors = [" ".join("".join(a.itertext()).split()) for a in el.findall("author")]
                    if arxiv and title:
                        corr.append((key, arxiv, title, int(year), authors))
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]
    return corr


def load_corr(conn: sqlite3.Connection, xml_path: Path) -> list[tuple[str, str, str, int, list[str]]]:
    print("━" * 70)
    print("  Phase 1 – DBLP arXiv records → paper_corr")
    print("━" * 70)
    first_year = conn.execute("SELECT min(year) FROM paper_dblp").fetchone()[0] - YEAR_GAP
    started = time.time()
    corr = scan_corr(xml_path, first_year)
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO paper_corr (dblp_key, arxiv_id, title, year, authors) VALUES (?, ?, ?, ?, ?)",
            [(k, a, t, y, json.dumps(au, ensure_ascii=False)) for k, a, t, y, au in corr])
    print(f"  ✓  {len(corr):,} arXiv records from {first_year} on  |  {time.time() - started:,.0f}s\n")
    return corr


def match(conn: sqlite3.Connection, corr: list[tuple[str, str, str, int, list[str]]]) -> None:
    print("━" * 70)
    print("  Phase 2 – Match papers to their arXiv preprints → paper_arxiv_match, paper_arxiv_candidate")
    print("━" * 70)
    started = time.time()
    by_title, by_author = defaultdict(list), defaultdict(list)
    for i, (_, _, t, _, au) in enumerate(corr):
        by_title[norm(t)].append(i)
        for a in au:
            by_author[a].append(i)

    papers = conn.execute(
        "SELECT paper_id, title, authors, year FROM paper_dblp "
        "WHERE arxiv_id IS NULL AND paper_id NOT IN (SELECT paper_id FROM paper_arxiv_match)").fetchall()
    matches, candidates = [], []
    for pid, title, authors, year in papers:
        ours = set(json.loads(authors))
        if not ours:
            continue
        hit = next((i for i in by_title.get(norm(title), [])
                    if ours & set(corr[i][4]) and abs(corr[i][3] - year) <= YEAR_GAP), None)
        if hit is not None:
            matches.append((pid, corr[hit], "exact", "same title, shared author"))
            continue
        scored = []
        for i in {i for a in ours for i in by_author.get(a, [])}:
            _, _, ct, cy, cau = corr[i]
            if abs(cy - year) > YEAR_GAP:
                continue
            theirs = set(cau)
            jaccard = len(ours & theirs) / len(ours | theirs)
            if jaccard < CAND_JACCARD:
                continue
            sm = SequenceMatcher(None, norm(title), norm(ct))
            sim = sm.ratio() if sm.real_quick_ratio() >= CAND_SIM * 0.8 else 0.0
            same_name = bool(name_prefix(title)) and name_prefix(title) == name_prefix(ct)
            if sim >= CAND_SIM or same_name:
                scored.append((sim, jaccard, i))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= CLOSE_SIM and (len(scored) == 1 or scored[1][0] < CLOSE_SIM):
            matches.append((pid, corr[scored[0][2]], "close",
                            f"title similarity {scored[0][0]:.2f}, author overlap {scored[0][1]:.2f}"))
            continue
        candidates += [(pid, corr[i][0], round(sim, 4), round(jac, 4)) for sim, jac, i in scored[:PER_PAPER]]

    with conn:
        for pid, (key, arxiv, *_), method, reason in matches:
            conn.execute("INSERT INTO paper_arxiv_match (paper_id, arxiv_id, method, source, reason) "
                         "VALUES (?, ?, ?, ?, ?)", (pid, arxiv, method, key, reason))
            conn.execute("UPDATE paper_dblp SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL", (arxiv, pid))
        conn.execute("DELETE FROM paper_arxiv_candidate WHERE paper_id IN (SELECT paper_id FROM paper_arxiv_match)")
        conn.executemany("INSERT OR REPLACE INTO paper_arxiv_candidate (paper_id, dblp_key, title_sim, author_jaccard) "
                         "VALUES (?, ?, ?, ?)", candidates)
    exact = sum(m[2] == "exact" for m in matches)
    print(f"  ✓  {len(papers):,} papers checked  |  exact {exact:,}  |  close {len(matches) - exact:,}  |  "
          f"{len(candidates):,} candidate pairs for {len({c[0] for c in candidates}):,} papers left for "
          f"d_arxiv.md  |  {time.time() - started:,.0f}s\n")


def report(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT method, count(*) FROM paper_arxiv_match GROUP BY method ORDER BY count(*) DESC").fetchall()
    total = conn.execute("SELECT count(*) FROM paper_dblp WHERE arxiv_id IS NOT NULL").fetchone()[0]
    print("  decisions: " + "  |  ".join(f"{m} {n:,}" for m, n in rows))
    print(f"  papers with an arXiv ID: {total:,}\n")


def main() -> None:
    if not XML_PATH.exists():
        sys.exit(f"dblp.xml not found: {XML_PATH}")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  arXiv ID Matcher                             ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    conn.executescript(paper_schema())
    corr = load_corr(conn, XML_PATH)
    match(conn, corr)
    report(conn)
    conn.close()
    print("━" * 70)
    print("  Done.  → Run c_fetch.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
