#!/usr/bin/env python3
import os
import signal
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c_fetch as c

DONE_FILE = c.REPO_ROOT / "b-config" / "c_backfill_checked.txt"
WORKERS = 8
CHUNK = 1_000


def lookup(http: c.Http, doi: str) -> dict | None:
    url = f"{c.OPENALEX}/works/https://doi.org/{urllib.parse.quote(doi, safe='/')}"
    r = http.request("GET", url, params={"select": c.OPENALEX_SELECT})
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise c.TransientError(f"OpenAlex HTTP {r.status_code}")
    return r.json()


def main() -> None:
    c.load_env()
    conn = c.connect()
    checked = set(int(x) for x in DONE_FILE.read_text().split()) if DONE_FILE.exists() else set()
    rows = conn.execute("SELECT paper_id, doi, title FROM paper_summary "
                        "WHERE status = 'C' AND abstract IS NULL AND doi IS NOT NULL ORDER BY paper_id").fetchall()
    todo = [r for r in rows if r[0] not in checked]
    print(f"\n  C papers with a DOI and no abstract: {len(rows):,}  ·  already checked: {len(rows) - len(todo):,}"
          f"  ·  to check: {len(todo):,}")
    print("  Ctrl+C once to stop safely, twice to quit at once\n")
    http = c.Http(1.0 / c.OPENALEX_RPS)
    lock, stop = threading.Lock(), threading.Event()
    counts = {"done": 0, "found": 0, "failed": 0}
    started = time.time()
    done_file = DONE_FILE.open("a")

    def on_sigint(signum, frame):
        if stop.is_set():
            print("\n  quit at once: everything found so far is already saved", flush=True)
            done_file.flush()
            os._exit(130)
        stop.set()
        print("\n  stopping: finishing requests in flight (Ctrl+C again to quit at once)", flush=True)
    signal.signal(signal.SIGINT, on_sigint)

    def run(row) -> None:
        paper_id, doi, title = row
        if stop.is_set():
            return
        try:
            work = lookup(http, doi)
        except c.TransientError:
            with lock:
                counts["failed"] += 1
                counts["done"] += 1
            return
        abstract = c.valid_abstract(c.OpenAlex.abstract(work), title) if work else None
        with lock:
            if abstract:
                with conn:
                    conn.execute("UPDATE paper_field_abstract SET abstract = ? WHERE paper_id = ? AND abstract IS NULL",
                                 (abstract, paper_id))
                counts["found"] += 1
            done_file.write(f"{paper_id}\n")
            counts["done"] += 1

    with ThreadPoolExecutor(WORKERS) as pool:
        for i in range(0, len(todo), CHUNK):
            if stop.is_set():
                break
            list(pool.map(run, todo[i:i + CHUNK]))
            done_file.flush()
            rate = counts["done"] / max(time.time() - started, 1e-9)
            left = (len(todo) - counts["done"]) / rate if rate else 0
            print(f"  {counts['done']:,}/{len(todo):,}  ·  ✓ {counts['found']:,} abstracts  ·  "
                  f"⚠ {counts['failed']:,} no answer  ·  {rate:.1f}/s  ·  {c.clock(time.time() - started)}"
                  f"  ·  ETA {c.clock(left)}", flush=True)
    done_file.close()
    conn.close()
    print(f"\n  Done: ✓ {counts['found']:,} abstracts added  ·  ⚠ {counts['failed']:,} no answer "
          f"(run again to retry them)  ·  {c.clock(time.time() - started)}\n")


if __name__ == "__main__":
    main()
