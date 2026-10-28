#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c_fetch as c

DONE_FILE = Path(__file__).resolve().with_name("temp_c_recheck_done.txt")
SOURCE_BY_NO = {s.no: s.source for s in c.STAGES}
ORDER = ["arxiv", "oa-batch", "s2-doi", "oa-single", "oa-title"]
STAGES = [c.Stage(i, s.name, s.source, "C", "C", s.needs)
          for i, s in enumerate(sorted(c.STAGES, key=lambda s: ORDER.index(s.source)), start=1)]
BASE = "FROM paper_summary WHERE status = 'C' AND abstract IS NULL"


class RecheckStore(c.Store):
    def __init__(self, conn):
        super().__init__(conn)
        self.done: set[tuple[int, str]] = set()
        if DONE_FILE.exists():
            for line in DONE_FILE.read_text().split():
                pid, _, key = line.partition(":")
                self.done.add((int(pid), SOURCE_BY_NO[int(key)] if key.isdigit() else key))
        self.out = DONE_FILE.open("a", buffering=1)
        self.source = ""
        self.cursor = 0

    def begin(self, stage: c.Stage) -> None:
        self.source, self.cursor = stage.source, 0

    def save(self, papers: list[c.Paper], takes: str, sets: str) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "UPDATE paper_field_arxiv SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL",
                [(p.new_arxiv, p.paper_id) for p in papers if p.new_arxiv])
            self.conn.executemany(
                "UPDATE paper_field_abstract SET abstract = ? WHERE paper_id = ? AND abstract IS NULL",
                [(p.abstract, p.paper_id) for p in papers if p.abstract])
        with self.lock:
            for p in papers:
                if p.abstract or not p.failed:
                    self.done.add((p.paper_id, self.source))
                    self.out.write(f"{p.paper_id}:{self.source}\n")

    def pass_on(self, stage: c.Stage) -> int:
        self.begin(stage)
        return 0

    def count(self, stage: c.Stage) -> int:
        return sum((pid, stage.source) not in self.done
                   for (pid,) in self.conn.execute(f"SELECT paper_id {BASE} AND {stage.needs}"))

    def load(self, stage: c.Stage, after: int) -> list[c.Paper]:
        while True:
            rows = self.conn.execute(
                f"SELECT paper_id, doi, arxiv_id, title, authors, year {BASE} AND paper_id > ? AND {stage.needs} "
                "ORDER BY paper_id LIMIT ?", (self.cursor, c.CHUNK_PAPERS)).fetchall()
            if not rows:
                return []
            self.cursor = rows[-1][0]
            papers = [c.Paper(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in rows
                      if (r[0], stage.source) not in self.done]
            if papers:
                return papers

    def remaining(self) -> int:
        return self.conn.execute(f"SELECT COUNT(*) {BASE}").fetchone()[0]

    def close(self) -> None:
        self.out.close()


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  C · Recheck C Papers Without Abstract        ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {c.DB_PATH}\n")
    c.load_env()
    conn = c.connect()
    store = RecheckStore(conn)
    before = store.remaining()
    print(f"  Papers at C without an abstract: {before:,}  ·  stage results already saved: {len(store.done):,}")
    print("  Same 5 stages and checks as c_fetch.py  ·  status is never changed, only empty fields are filled")
    print("  Ctrl+C once to stop safely, twice to quit at once")
    progress = c.Progress()
    c.STAGES[:] = STAGES
    fetcher = c.Fetcher(progress, store)

    def on_sigint(signum, frame):
        if fetcher.stopping.is_set():
            progress.close()
            store.out.flush()
            print("\n  quit at once: everything found so far is already saved", flush=True)
            os._exit(130)
        fetcher.stopping.set()
        progress.log("    stopping: finishing requests in flight (Ctrl+C again to quit at once)")
    signal.signal(signal.SIGINT, on_sigint)
    started, stopped, budget = time.time(), None, None
    try:
        for stage in STAGES:
            try:
                fetcher.run_stage(stage)
            except c.QuotaExhausted as e:
                budget = e
                progress.log(f"    stage {stage.no} paused: {e}; its papers wait for the next run")
    except c.Stopped as e:
        stopped = e
    finally:
        progress.close()
        after = store.remaining()
        left = sum(store.count(stage) for stage in STAGES)
        store.close()
        conn.close()
    if stopped:
        print(f"\n  stopped: {stopped}")
    if budget:
        print(f"\n  {budget}")
    print("\n" + "━" * 70)
    for stage in STAGES:
        if stage.no in fetcher.summary:
            found, missed, failed, _ = fetcher.summary[stage.no]
            print(f"  stage {stage.no} {stage.name:<20} ✓ {found:,}  ✗ {missed:,}  ⚠ {failed:,}")
    print(f"  arXiv fallback ✓ {fetcher.stats['arxiv-fb']:,}  ·  arXiv ids recovered {fetcher.stats['arxiv ids found']:,}"
          f"  ·  other paper rejected {fetcher.stats['other paper']:,}")
    print(f"  calls: arXiv {fetcher.arxiv.http.calls:,}  OpenAlex {fetcher.openalex.calls:,}  "
          f"Semantic Scholar {fetcher.s2.http.calls:,}  |  {c.clock(time.time() - started)}")
    print(f"  abstracts added: {before - after:,}  ·  C papers still without an abstract: {after:,}")
    if left:
        print(f"  Not finished: {left:,} stage lookups left.  → Run temp_c_recheck.py again to continue")
    else:
        print("  Done.  Every C paper without an abstract went through all stages.")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
