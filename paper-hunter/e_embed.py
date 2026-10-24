#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
SCHEMA_PATH = REPO_ROOT / "circumstance" / "b_schema.py"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
CHUNK = 512
BATCH_SIZE = 8


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


def build_text(title: str | None, abstract: str | None, venue: str | None, year: int | None) -> tuple[str, str]:
    title = " ".join((title or "").split())
    if abstract:
        return "abstract", f"{title}\n{' '.join(abstract.split())}"
    return "title", "\n".join(part for part in (title, " ".join(str(x) for x in (venue, year) if x)) if part)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pick_device() -> str:
    import torch

    if not torch.cuda.is_available():
        return "cpu"
    try:
        x = torch.ones(8, 8, device="cuda")
        (x @ x).sum().item()
        return "cuda"
    except Exception:
        return "cpu"


def load_model(name: str = MODEL_NAME, device: str | None = None):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name, device=device or pick_device())


def check_model(conn: sqlite3.Connection, name: str, dim: int | None = None) -> None:
    row = conn.execute("SELECT model, dim FROM paper_embedding_model WHERE id = 1").fetchone()
    if row is None:
        if dim is not None:
            with conn:
                conn.execute("INSERT INTO paper_embedding_model (id, model, dim) VALUES (1, ?, ?)", (name, dim))
        return
    if row[0] != name or (dim is not None and row[1] != dim):
        sys.exit(f"paper_embedding holds vectors from {row[0]} ({row[1]} dims), not {name}"
                 f"{f' ({dim} dims)' if dim else ''}. Refusing to mix models.")


def texts_for(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple[str, str]]:
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT p.paper_id, p.title, a.abstract, p.venue, p.year FROM paper_dblp p "
        f"LEFT JOIN paper_abstract a ON a.paper_id = p.paper_id WHERE p.paper_id IN ({marks})", ids)
    return {pid: build_text(title, abstract, venue, year) for pid, title, abstract, venue, year in rows}


def pending(conn: sqlite3.Connection, limit: int | None) -> list[int]:
    sql = ("SELECT p.paper_id, p.title, a.abstract, p.venue, p.year, s.embed_hash, "
           "EXISTS (SELECT 1 FROM paper_embedding e WHERE e.paper_id = p.paper_id) "
           "FROM paper_dblp p LEFT JOIN paper_abstract a ON a.paper_id = p.paper_id "
           "LEFT JOIN paper_screen s ON s.paper_id = p.paper_id")
    if limit is not None:
        sql += " WHERE p.paper_id IN (SELECT paper_id FROM paper_dblp ORDER BY paper_id LIMIT ?)"
    sql += " ORDER BY p.paper_id"
    out = []
    for pid, title, abstract, venue, year, stored, has_vector in conn.execute(sql, (limit,) if limit is not None else ()):
        if not has_vector or stored != text_hash(build_text(title, abstract, venue, year)[1]):
            out.append(pid)
    return out


def write_chunk(conn: sqlite3.Connection, rows: list[tuple[int, str, str, bytes]]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO paper_embedding (paper_id, vector) VALUES (?, ?) "
            "ON CONFLICT (paper_id) DO UPDATE SET vector = excluded.vector",
            [(pid, vector) for pid, _, _, vector in rows],
        )
        conn.executemany(
            "INSERT INTO paper_screen (paper_id, embed_basis, embed_hash) VALUES (?, ?, ?) "
            "ON CONFLICT (paper_id) DO UPDATE SET embed_basis = excluded.embed_basis, "
            "embed_hash = excluded.embed_hash, similarity = NULL, passed_by = NULL, screened = 0, passed = NULL",
            [(pid, basis, h) for pid, basis, h, _ in rows],
        )


def embed(conn: sqlite3.Connection, limit: int | None, model=None, model_name: str = MODEL_NAME) -> Counter:
    conn.executescript(paper_schema())
    check_model(conn, model_name)
    started = time.time()
    todo = pending(conn, limit)
    print(f"  {len(todo):,} papers to embed  (scan {time.time() - started:.1f}s)")
    stats: Counter = Counter()
    if not todo:
        return stats
    model = model or load_model(model_name)
    dim = model.get_sentence_embedding_dimension()
    check_model(conn, model_name, dim)
    print(f"  model {model_name}  |  {dim} dims  |  max {model.max_seq_length} tokens  |  device {model.device}\n")
    encode_seconds = 0.0
    for i in range(0, len(todo), CHUNK):
        ids = todo[i:i + CHUNK]
        texts = texts_for(conn, ids)
        t = time.time()
        vectors = model.encode([texts[pid][1] for pid in ids], batch_size=BATCH_SIZE,
                               normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)
        encode_seconds += time.time() - t
        write_chunk(conn, [(pid, texts[pid][0], text_hash(texts[pid][1]), vectors[k].tobytes())
                           for k, pid in enumerate(ids)])
        stats.update(texts[pid][0] for pid in ids)
        done = i + len(ids)
        print(f"  {done:,}/{len(todo):,}  |  {done / encode_seconds:.1f} papers/s  |  "
              f"eta {(len(todo) - done) / (done / encode_seconds) / 3600:.1f} h", end="\r", flush=True)
    print()
    stats["seconds"] = round(encode_seconds)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed every paper into paper_embedding.")
    parser.add_argument("--limit", type=int, help="consider only the first N papers by paper_id (for testing)")
    args = parser.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Paper Embedder                               ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    stats = embed(conn, args.limit)
    conn.close()
    total = stats["abstract"] + stats["title"]
    print("━" * 70)
    print(f"  embedded {total:,}  |  abstract basis {stats['abstract']:,}  |  title basis {stats['title']:,}  |  "
          f"{total / stats['seconds'] if stats['seconds'] else 0:.1f} papers/s")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
