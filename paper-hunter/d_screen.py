#!/usr/bin/env python3
import argparse
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
sys.path.insert(0, str(HUNTER_DIR))
import c_embed

MARGIN = 0.05
SAMPLE_REJECTED = 20

QUERIES = [
    "LLM-based agentic framework for autonomous black-box penetration testing over long-horizon attack chains",
    "large language model agents for automated penetration testing",
    "automated red teaming of networks with large language models",
    "LLM agents solving capture the flag CTF security challenges",
    "LLM-driven automated exploit generation and vulnerability exploitation",
    "AI attack planning, attack graphs, and privilege escalation chains",
    "autonomous cyber-operations and offensive security agents",
    "benchmark and environment for evaluating offensive-security AI agents",
    "survey of large language models for offensive security and penetration testing",
    "empirical evaluation of LLMs on cybersecurity and hacking tasks",
]

KEYWORDS = [
    "penetration test", "pentest", "red team", "red-team", "ctf", "capture the flag",
    "capture-the-flag", "exploit", "attack chain", "attack graph", "privilege escalation",
    "lateral movement", "metasploit", "offensive security", "cyber range", "kill chain",
]
KEYWORD_RES = [re.compile(r"\b" + re.escape(k).replace(r"\ ", r"[\s\-]") + r"s?\b", re.I) for k in KEYWORDS]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run paper-hunter/a_extract.py first)")
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def model_name(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT model FROM paper_embedding_model WHERE id = 1").fetchone()
    if row is None:
        sys.exit("paper_embedding_model is empty; run paper-hunter/c_embed.py first")
    return row[0]


def keyword_hit(*parts: str | None) -> bool:
    text = " ".join(p for p in parts if p)
    return any(r.search(text) for r in KEYWORD_RES)


def encode(model, texts: list[str]) -> np.ndarray:
    return model.encode(texts, batch_size=c_embed.BATCH_SIZE, normalize_embeddings=True,
                        convert_to_numpy=True).astype(np.float32)


def best_similarity(vectors: np.ndarray, queries: np.ndarray) -> np.ndarray:
    return (vectors @ queries.T).max(axis=1)


def calibrate(conn: sqlite3.Connection, model, queries: np.ndarray) -> dict[str, float]:
    seeds = conn.execute(
        "SELECT s.paper_id, s.category, p.title, a.abstract, p.venue, p.year "
        "FROM paper_seed s JOIN paper_dblp p ON p.paper_id = s.paper_id "
        "LEFT JOIN paper_abstract a ON a.paper_id = s.paper_id ORDER BY s.paper_id").fetchall()
    if not seeds:
        sys.exit("paper_seed is empty; add known-relevant papers (plan.md A.5) before screening")

    natural_texts, title_texts, meta = [], [], []
    for paper_id, category, title, abstract, venue, year in seeds:
        basis, natural = c_embed.build_text(title, abstract, venue, year)
        _, title_only = c_embed.build_text(title, None, venue, year)
        natural_texts.append(natural)
        title_texts.append(title_only)
        meta.append((paper_id, category, basis, title, abstract))

    natural_sim = best_similarity(encode(model, natural_texts), queries)
    title_sim = best_similarity(encode(model, title_texts), queries)

    abstract_scores = [natural_sim[i] for i, m in enumerate(meta) if m[2] == "abstract"]
    if not abstract_scores:
        sys.exit("no seed has an abstract, so the abstract-basis threshold cannot be calibrated.\n"
                 "Run b_fetch.py and c_embed.py so seed abstracts are fetched and embedded.")
    thresholds = {
        "abstract": float(min(abstract_scores)) - MARGIN,
        "title": float(min(title_sim)) - MARGIN,
    }

    print("  Seed calibration (similarity under each basis):")
    print(f"  {'paper_id':>8}  {'category':10s}  {'basis':8s}  {'natural':>8}  {'title':>8}  {'keyword':>7}  pass")
    failed = []
    for i, (paper_id, category, basis, title, abstract) in enumerate(meta):
        kw = keyword_hit(title, abstract)
        sim = natural_sim[i]
        semantic = sim >= thresholds[basis]
        passed = semantic or kw
        failed.append(paper_id) if not passed else None
        print(f"  {paper_id:>8}  {category:10s}  {basis:8s}  {natural_sim[i]:8.4f}  {title_sim[i]:8.4f}  "
              f"{'yes' if kw else 'no':>7}  {'PASS' if passed else 'FAIL'}")
    print(f"\n  thresholds: abstract {thresholds['abstract']:.4f}  title {thresholds['title']:.4f}  "
          f"(margin {MARGIN})")
    if failed:
        sys.exit(f"these seeds do not pass screening: {failed}. Lower the margin or review the seeds.")
    return thresholds


def store_thresholds(conn: sqlite3.Connection, thresholds: dict[str, float]) -> bool:
    previous = dict(conn.execute("SELECT embed_basis, threshold FROM paper_screen_threshold"))
    changed = any(abs(previous.get(b, -2.0) - t) > 1e-9 for b, t in thresholds.items())
    with conn:
        for basis, value in thresholds.items():
            conn.execute(
                "INSERT INTO paper_screen_threshold (embed_basis, threshold, computed_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT (embed_basis) "
                "DO UPDATE SET threshold = excluded.threshold, computed_at = excluded.computed_at",
                (basis, value))
        if changed:
            conn.execute("UPDATE paper_screen SET screened = 0, passed = NULL, passed_by = NULL, similarity = NULL")
    return changed


def screen(conn: sqlite3.Connection, model, queries: np.ndarray, thresholds: dict[str, float]) -> Counter:
    rows = conn.execute(
        "SELECT s.paper_id, s.embed_basis, e.vector, p.title, a.abstract "
        "FROM paper_screen s JOIN paper_embedding e ON e.paper_id = s.paper_id "
        "JOIN paper_dblp p ON p.paper_id = s.paper_id "
        "LEFT JOIN paper_abstract a ON a.paper_id = s.paper_id "
        "WHERE s.screened = 0").fetchall()
    stats: Counter = Counter()
    if not rows:
        return stats
    dim = model.get_sentence_embedding_dimension()
    vectors = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), dim)
    sims = best_similarity(vectors, queries)

    updates = []
    for i, (paper_id, basis, _vector, title, abstract) in enumerate(rows):
        similarity = float(sims[i])
        semantic = similarity >= thresholds[basis]
        keyword = keyword_hit(title, abstract)
        passed = semantic or keyword
        passed_by = ("both" if semantic and keyword else "semantic" if semantic else "keyword") if passed else None
        has_abstract = abstract is not None
        updates.append((paper_id, similarity, 1 if passed else 0, passed_by, has_abstract))
        stats["passed" if passed else "rejected"] += 1
        if passed:
            stats[f"basis_{basis}"] += 1
            stats[f"by_{passed_by}"] += 1

    with conn:
        for paper_id, similarity, passed, passed_by, has_abstract in updates:
            conn.execute(
                "UPDATE paper_screen SET screened = 1, similarity = ?, passed = ?, passed_by = ? WHERE paper_id = ?",
                (similarity, passed, passed_by, paper_id))
            if passed and has_abstract:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_verdict_with_abstract (paper_id) VALUES (?)", (paper_id,))
            elif not passed:
                conn.execute(
                    "DELETE FROM paper_verdict_with_abstract WHERE paper_id = ? AND reviewed = 0", (paper_id,))
    return stats


def report_margin(conn: sqlite3.Connection, thresholds: dict[str, float]) -> None:
    print(f"\n  {SAMPLE_REJECTED} rejected papers with the highest similarity just below threshold:")
    rows = conn.execute(
        "SELECT s.paper_id, s.embed_basis, s.similarity, p.title FROM paper_screen s "
        "JOIN paper_dblp p ON p.paper_id = s.paper_id WHERE s.passed = 0 ORDER BY random() LIMIT 2000").fetchall()
    scored = sorted(((r[2] - thresholds[r[1]], r) for r in rows if r[2] is not None), reverse=True)
    for _gap, (paper_id, basis, similarity, title) in scored[:SAMPLE_REJECTED]:
        print(f"    [{paper_id:>7}] {basis:8s} sim {similarity:.4f} (thr {thresholds[basis]:.4f})  {title[:90]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen every paper against the research topic.")
    parser.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Paper Screener                              ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    conn.executescript(c_embed.paper_schema())
    name = model_name(conn)
    print(f"  model {name}")
    model = c_embed.load_model(name)
    queries = encode(model, QUERIES)
    print(f"  {len(QUERIES)} topic queries embedded ({model.get_sentence_embedding_dimension()} dims, device {model.device})\n")

    started = time.time()
    thresholds = calibrate(conn, model, queries)
    if store_thresholds(conn, thresholds):
        print("  thresholds changed: every paper will be screened again")
    stats = screen(conn, model, queries, thresholds)
    report_margin(conn, thresholds)

    passed = stats["passed"]
    print("\n" + "━" * 70)
    print(f"  screened {passed + stats['rejected']:,}  |  passed {passed:,}  |  rejected {stats['rejected']:,}  |  "
          f"{time.time() - started:,.0f}s")
    print(f"  passed by basis:  abstract {stats['basis_abstract']:,}  title {stats['basis_title']:,}")
    print(f"  passed by test:   semantic {stats['by_semantic']:,}  keyword {stats['by_keyword']:,}  both {stats['by_both']:,}")
    print("━" * 70)
    print()
    conn.close()


if __name__ == "__main__":
    main()
