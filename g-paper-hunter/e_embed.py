#!/usr/bin/env python3
import argparse
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "a-database" / "paperhunter.db"
CHUNK = 512


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed papers at status D.")
    parser.add_argument("--limit", type=int, help="embed at most N papers (for testing)")
    args = parser.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  E · Embedder                                 ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    sem = semantic()
    conn = connect()
    sql = "SELECT paper_id, title, abstract FROM paper_summary WHERE status = 'D' ORDER BY paper_id"
    papers = conn.execute(sql + (" LIMIT ?" if args.limit is not None else ""),
                          (args.limit,) if args.limit is not None else ()).fetchall()
    if not papers:
        print("  No papers at status D.\n")
        return
    model = sem.load_model()
    print(f"  model {sem.MODEL} @ {sem.REVISION[:7]} on {model.device}  |  {len(papers):,} papers at status D\n")
    started = time.time()
    done = 0
    for i in range(0, len(papers), CHUNK):
        chunk = papers[i:i + CHUNK]
        texts = [sem.embed_text(title, abstract) for _, title, abstract in chunk]
        vectors = sem.encode(model, [t for _, t in texts])
        with conn:
            conn.executemany(
                "INSERT INTO paper_semantic_vector (paper_id, basis, vector) VALUES (?, ?, ?) "
                "ON CONFLICT (paper_id) DO UPDATE SET basis = excluded.basis, vector = excluded.vector",
                [(pid, basis, v.tobytes()) for (pid, _, _), (basis, _), v in zip(chunk, texts, vectors)])
            conn.executemany("UPDATE paper_status SET status = 'E' WHERE paper_id = ? AND status = 'D'",
                             [(pid,) for pid, _, _ in chunk])
        done += len(chunk)
        rate = done / max(time.time() - started, 1e-9)
        print(f"  {done:,}/{len(papers):,}  |  {rate:,.1f} papers/s  |  "
              f"~{(len(papers) - done) / rate / 60:,.0f} min left", end="\r", flush=True)
    conn.close()
    print(f"\n\n  ✓  {done:,} papers embedded and moved D → E  |  {time.time() - started:,.0f}s")
    print("━" * 70)
    print("  Done.  → Run f_semantic.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
