#!/usr/bin/env python3
import importlib.util
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "a-database" / "paperhunter.db"
CHUNK = 20_000


def semantic():
    spec = importlib.util.spec_from_file_location("c_threshold", REPO_ROOT / "c-context" / "c_threshold.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run c-context/b_schema.py first)")
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def keyword_patterns(keywords: list[str]) -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(k).replace(r"\ ", r"[\s\-]") + r"s?\b", re.I) for k in keywords]


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  F · Semantic Shortlist                       ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    sem = semantic()
    conn = connect()
    thresholds = dict(conn.execute("SELECT basis, threshold FROM paper_semantic_threshold"))
    if set(thresholds) != {"title", "title_abstract"}:
        sys.exit("paper_semantic_threshold is empty (run c-context/c_threshold.py first)")
    ids = [r[0] for r in conn.execute("SELECT paper_id FROM paper_status WHERE status = 'E' ORDER BY paper_id")]
    if not ids:
        print("  No papers at status E.\n")
        return
    data = sem.settings()
    model = sem.load_model()
    queries = sem.encode(model, data["queries"])
    patterns = keyword_patterns(data["keywords"])
    dim = queries.shape[1]
    print(f"  {len(ids):,} papers at status E  |  {len(data['queries'])} queries, {len(patterns)} keywords  |  "
          f"thresholds: title_abstract {thresholds['title_abstract']:.4f}, title {thresholds['title']:.4f}\n")
    started = time.time()
    stats: Counter = Counter()
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        rows = conn.execute(
            "SELECT v.paper_id, v.basis, v.vector, s.title, s.abstract FROM paper_semantic_vector v "
            "JOIN paper_summary s ON s.paper_id = v.paper_id WHERE v.paper_id IN (SELECT value FROM json_each(?))",
            (str(chunk),)).fetchall()
        if len(rows) != len(chunk):
            sys.exit(f"{len(chunk) - len(rows)} papers at status E have no vector (run e_embed.py)")
        vectors = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), dim)
        scores = sem.best_similarity(vectors, queries)
        shortlist = []
        for (pid, basis, _, title, abstract), score in zip(rows, scores):
            semantic_hit = score >= thresholds[basis]
            text = f"{title or ''} {abstract or ''}"
            keyword_hit = any(p.search(text) for p in patterns)
            if semantic_hit or keyword_hit:
                shortlist.append((pid,))
                stats["both" if semantic_hit and keyword_hit else "semantic" if semantic_hit else "keyword"] += 1
        with conn:
            conn.executemany("INSERT OR IGNORE INTO paper_semantic_shortlist (paper_id) VALUES (?)", shortlist)
            conn.executemany("UPDATE paper_status SET status = 'F' WHERE paper_id = ? AND status = 'E'",
                             [(pid,) for pid in chunk])
        stats["screened"] += len(chunk)
    conn.close()
    passed = stats["semantic"] + stats["keyword"] + stats["both"]
    print(f"  ✓  {stats['screened']:,} screened and moved E → F  |  {passed:,} shortlisted "
          f"(semantic {stats['semantic']:,}, keyword {stats['keyword']:,}, both {stats['both']:,})  |  "
          f"{time.time() - started:,.0f}s")
    print("━" * 70)
    print("  Done.  → Give g_search.md to the AI next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
