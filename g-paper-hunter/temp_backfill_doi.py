#!/usr/bin/env python3
import json
import os
import shutil
import signal
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c_fetch as c

DONE_FILE = Path(__file__).resolve().with_name("temp_backfill_checked.txt")
WORKERS = 8
CHUNK = 1_000
LOG_EVERY = 10_000


def lookup(http: c.Http, doi: str) -> dict | None:
    url = f"{c.OPENALEX}/works/https://doi.org/{urllib.parse.quote(doi, safe='/')}"
    r = http.request("GET", url, params={"select": c.OPENALEX_SELECT})
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise c.TransientError(f"OpenAlex HTTP {r.status_code}")
    return r.json()


class Live:
    def __init__(self, total: int):
        self.total = total
        self.live = sys.stdout.isatty()
        self.lock = threading.RLock()
        self.started = time.time()
        self.done = self.found = self.missing = self.empty = self.other = self.failed = 0
        self.current, self.waiting, self.last_error = "", None, ""
        self.drawn = False
        self.stop = threading.Event()
        if self.live:
            threading.Thread(target=self.tick, daemon=True).start()

    def tick(self) -> None:
        while not self.stop.wait(0.5):
            with self.lock:
                self.draw()

    def erase(self) -> None:
        if self.drawn:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K\033[1A\r\033[2K")
            self.drawn = False

    def progress(self) -> str:
        elapsed = time.time() - self.started
        rate = self.done / elapsed if elapsed > 0 else 0
        eta = c.clock((self.total - self.done) / rate) if rate else "–"
        pct = self.done / self.total * 100 if self.total else 100
        return f"{self.done:,}/{self.total:,} ({pct:.1f}%) · {rate:.1f}/s · {c.clock(elapsed)} · ETA {eta}"

    def counts(self) -> str:
        return (f"✓ {self.found:,} abstracts · ✗ {self.empty:,} no abstract · "
                f"✗ {self.missing:,} not in OpenAlex · ✗ {self.other:,} other paper · ⚠ {self.failed:,} no answer")

    def draw(self) -> None:
        if not self.live:
            return
        width = shutil.get_terminal_size().columns - 1
        bottom = f"    → {self.current}"
        if self.waiting and self.waiting[0] > time.time():
            until, reason, attempt, attempts = self.waiting
            bottom = f"    ⏳ OpenAlex waiting {until - time.time():.0f}s ({reason}, attempt {attempt}/{attempts})"
        self.erase()
        sys.stdout.write(("  " + self.progress())[:width] + "\n" + ("    " + self.counts())[:width] + "\n"
                         + bottom[:width])
        sys.stdout.flush()
        self.drawn = True

    def log(self, message: str) -> None:
        with self.lock:
            self.erase()
            print(message, flush=True)
            self.draw()

    def on_wait(self, reason: str, seconds: float, attempt: int, attempts: int) -> None:
        with self.lock:
            self.waiting = (time.time() + seconds, reason, attempt, attempts)

    def record(self, kind: str, doi: str, note: str) -> None:
        with self.lock:
            setattr(self, kind, getattr(self, kind) + 1)
            self.done += 1
            self.current, self.waiting = f"{doi} · {note}", None
            if self.done % LOG_EVERY == 0:
                self.log(f"  {self.progress()}\n    {self.counts()}")

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            self.erase()
        sys.stdout.flush()


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  C · OpenAlex DOI Backfill                    ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {c.DB_PATH}\n")
    c.load_env()
    conn = c.connect()
    checked = set(int(x) for x in DONE_FILE.read_text().split()) if DONE_FILE.exists() else set()
    rows = conn.execute("SELECT paper_id, doi, title, authors, year FROM paper_summary "
                        "WHERE status = 'C' AND abstract IS NULL AND doi IS NOT NULL ORDER BY paper_id").fetchall()
    todo = [r for r in rows if r[0] not in checked]
    print(f"  C papers with a DOI and no abstract: {len(rows):,}  ·  already checked: {len(rows) - len(todo):,}"
          f"  ·  to check: {len(todo):,}")
    print("  Free OpenAlex single-DOI lookups, no key  ·  status stays C")
    print("  Ctrl+C once to stop safely, twice to quit at once\n")
    live = Live(len(todo))
    http = c.Http(1.0 / c.OPENALEX_RPS)
    http.on_wait = live.on_wait
    lock, stop = threading.Lock(), threading.Event()
    done_file = DONE_FILE.open("a")

    def on_sigint(signum, frame):
        if stop.is_set():
            live.close()
            print("\n  quit at once: everything found so far is already saved", flush=True)
            done_file.flush()
            os._exit(130)
        stop.set()
        live.log("  stopping: finishing requests in flight (Ctrl+C again to quit at once)")
    signal.signal(signal.SIGINT, on_sigint)

    def run(row) -> None:
        paper = c.Paper(row[0], row[1], None, row[2], json.loads(row[3]), row[4])
        paper_id, doi, title = paper.paper_id, paper.doi, paper.title
        if stop.is_set():
            return
        try:
            work = lookup(http, doi)
        except c.TransientError as e:
            live.last_error = str(e)
            live.record("failed", doi, f"no answer: {e}")
            return
        other = work is not None and not c.OpenAlex.same_paper(work, paper)
        abstract = c.valid_abstract(c.OpenAlex.abstract(work), title) if work and not other else None
        with lock:
            if abstract:
                with conn:
                    conn.execute("UPDATE paper_field_abstract SET abstract = ? WHERE paper_id = ? AND abstract IS NULL",
                                 (abstract, paper_id))
            done_file.write(f"{paper_id}\n")
        if abstract:
            live.record("found", doi, f"✓ abstract saved ({len(abstract.split())} words)")
        elif other:
            live.record("other", doi, f"✗ DOI points to another paper: {work.get('display_name') or ''}")
        elif work:
            live.record("empty", doi, "✗ in OpenAlex, no abstract")
        else:
            live.record("missing", doi, "✗ not in OpenAlex")

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            for i in range(0, len(todo), CHUNK):
                if stop.is_set():
                    break
                list(pool.map(run, todo[i:i + CHUNK]))
                done_file.flush()
    finally:
        live.close()
        done_file.close()
        conn.close()
    print("\n" + "━" * 70)
    print(f"  ✓ {live.found:,} abstracts added  ·  ✗ {live.empty:,} no abstract in OpenAlex  ·  "
          f"✗ {live.missing:,} not in OpenAlex  ·  ✗ {live.other:,} DOI points to another paper")
    print(f"  ⚠ {live.failed:,} no answer" + (f" (last: {live.last_error})" if live.failed else ""))
    print(f"  checked {live.done:,} of {live.total:,}  ·  calls {http.calls:,}  ·  {c.clock(time.time() - live.started)}")
    if live.failed or live.done < live.total:
        print("  Not finished.  → Run temp_backfill_doi.py again to continue")
    else:
        print("  Done.")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
