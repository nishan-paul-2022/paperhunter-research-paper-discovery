#!/usr/bin/env python3
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
XML_PATH = REPO_ROOT / "d-download" / "dblp.xml"
DB_PATH = REPO_ROOT / "b-config" / "paperhunter.db"
RECORD_TAGS = ("article", "inproceedings", "proceedings", "book", "incollection",
               "phdthesis", "mastersthesis", "www", "person", "data")
YEAR_GAP = 3
CLOSE_SIM = 0.90
MIN_JACCARD = 0.50
WRITE_BATCH = 10_000


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run c-context/b_schema.py first)")
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def norm(title: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower()).split())


def scan_corr(xml_path: Path, first_year: int) -> list[tuple[str, str, int, frozenset[str]]]:
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
                    authors = frozenset(" ".join("".join(a.itertext()).split()) for a in el.findall("author"))
                    if arxiv and title and authors:
                        corr.append((arxiv, title, int(year), authors))
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]
    return corr


def match(papers: list[tuple], corr: list[tuple[str, str, int, frozenset[str]]]) -> dict[int, tuple[str, str]]:
    by_title, by_author = defaultdict(list), defaultdict(list)
    for i, (_, title, _, authors) in enumerate(corr):
        by_title[norm(title)].append(i)
        for a in authors:
            by_author[a].append(i)
    found = {}
    for pid, title, authors, year in papers:
        ours = set(json.loads(authors))
        if not ours or not title:
            continue
        exact = {corr[i][0] for i in by_title.get(norm(title), [])
                 if ours & corr[i][3] and abs(corr[i][2] - year) <= YEAR_GAP}
        if len(exact) == 1:
            found[pid] = (exact.pop(), "exact")
            continue
        if exact:
            continue
        close = set()
        for i in {i for a in ours for i in by_author.get(a, [])}:
            arxiv, ct, cy, theirs = corr[i]
            if abs(cy - year) > YEAR_GAP or len(ours & theirs) / len(ours | theirs) < MIN_JACCARD:
                continue
            sm = SequenceMatcher(None, norm(title), norm(ct))
            if sm.real_quick_ratio() >= CLOSE_SIM and sm.ratio() >= CLOSE_SIM:
                close.add(arxiv)
        if len(close) == 1:
            found[pid] = (close.pop(), "close")
    return found


def save(conn: sqlite3.Connection, papers: list[tuple], found: dict[int, tuple[str, str]]) -> None:
    ids = [p[0] for p in papers]
    for i in range(0, len(ids), WRITE_BATCH):
        chunk = ids[i:i + WRITE_BATCH]
        with conn:
            conn.executemany("UPDATE paper_field_arxiv SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL",
                             [(found[pid][0], pid) for pid in chunk if pid in found])
            conn.executemany("UPDATE paper_status SET status = 'B' WHERE paper_id = ? AND status = 'A'",
                             [(pid,) for pid in chunk])


def main() -> None:
    if not XML_PATH.exists():
        sys.exit(f"dblp.xml not found: {XML_PATH}")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  B · arXiv ID Matcher                         ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    papers = conn.execute("SELECT paper_id, title, authors, year FROM paper_summary "
                          "WHERE status = 'A' AND arxiv_id IS NULL").fetchall()
    queue = conn.execute("SELECT paper_id FROM paper_status WHERE status = 'A'").fetchall()
    waiting = len(queue)
    if waiting == 0:
        print("  No papers at status A.\n")
        return
    started = time.time()
    found = {}
    if papers:
        first_year = min(p[3] for p in papers) - YEAR_GAP
        print(f"  {waiting:,} papers at status A, {len(papers):,} without an arXiv ID  |  "
              f"scanning DBLP arXiv records from {first_year} on")
        corr = scan_corr(XML_PATH, first_year)
        print(f"  {len(corr):,} arXiv records loaded  |  {time.time() - started:,.0f}s")
        found = match(papers, corr)
    save(conn, queue, found)
    exact = sum(m == "exact" for _, m in found.values())
    print(f"\n  ✓  {len(found):,} arXiv IDs set (exact {exact:,}, close {len(found) - exact:,})  |  "
          f"{waiting:,} papers moved A → B  |  {time.time() - started:,.0f}s\n")
    conn.close()
    print("━" * 70)
    print("  Done.  → Run c_fetch.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
