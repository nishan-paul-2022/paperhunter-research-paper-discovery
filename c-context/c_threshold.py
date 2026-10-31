#!/usr/bin/env python3
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "a-database" / "paperhunter.db"
SEMANTIC_PATH = REPO_ROOT / "b-config" / "semantic.json"
MODEL = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MARGIN = 0.05
BATCH_SIZE = 8


def settings() -> dict:
    data = json.loads(SEMANTIC_PATH.read_text())
    for key in ("topic", "queries", "keywords", "seeds"):
        if key not in data:
            sys.exit(f"{SEMANTIC_PATH} has no '{key}'")
    if not data["queries"] or not data["seeds"]:
        sys.exit(f"{SEMANTIC_PATH} needs at least one query and one seed")
    return data


def embed_text(title: str | None, abstract: str | None) -> tuple[str, str]:
    title = " ".join((title or "").split())
    abstract = " ".join((abstract or "").split())
    return ("title_abstract", f"{title}\n{abstract}") if abstract else ("title", title)


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


def load_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL, revision=REVISION, device=pick_device())


def encode(model, texts: list[str]) -> np.ndarray:
    return model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False).astype(np.float32)


def best_similarity(vectors: np.ndarray, queries: np.ndarray) -> np.ndarray:
    return (vectors @ queries.T).max(axis=1)


def thresholds(model, data: dict) -> dict[str, float]:
    queries = encode(model, data["queries"])
    seeds = data["seeds"]
    full = best_similarity(encode(model, [embed_text(s["title"], s.get("abstract"))[1] for s in seeds]), queries)
    title = best_similarity(encode(model, [embed_text(s["title"], None)[1] for s in seeds]), queries)
    with_abstract = [full[i] for i, s in enumerate(seeds) if s.get("abstract")]
    if not with_abstract:
        sys.exit("no seed in semantic.json has an abstract")
    print(f"  {'title_abstract':>14}  {'title':>7}  seed")
    for i, s in enumerate(seeds):
        print(f"  {full[i]:14.4f}  {title[i]:7.4f}  {s['title'][:80]}")
    return {"title_abstract": float(min(with_abstract)) - MARGIN, "title": float(min(title)) - MARGIN}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run c-context/b_schema.py first)")
    data = settings()
    print(f"\n  {len(data['queries'])} queries, {len(data['seeds'])} seeds from {SEMANTIC_PATH.name}")
    print(f"  model {MODEL} @ {REVISION[:7]}  |  margin {MARGIN}\n")
    values = thresholds(load_model(), data)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("DELETE FROM paper_semantic_threshold")
        conn.executemany("INSERT INTO paper_semantic_threshold (basis, threshold) VALUES (?, ?)", values.items())
    conn.close()
    print("\n  " + "  |  ".join(f"{b} {t:.4f}" for b, t in values.items()) + f"  →  {path.name}\n")


if __name__ == "__main__":
    main()
