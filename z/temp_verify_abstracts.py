#!/usr/bin/env python3
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c_fetch as c

HERE = Path(__file__).resolve().parent
CHECKED_FILE = HERE / "temp_verify_checked.txt"
UNCONFIRMED_FILE = HERE / "temp_verify_unconfirmed.txt"
BACKFILL_FILE = HERE / "temp_backfill_checked.txt"
WORKERS = 8
SAME_TEXT = 0.95
ARXIV_PAUSE = 300
ARXIV_MAX_FAILS = 3
NS = {"a": "http://www.w3.org/2005/Atom"}


def words(text: str | None) -> str:
    return " ".join(re.findall(r"\w+", c.clean_text(text or "").lower()))


def digest(text: str | None) -> bytes:
    return hashlib.blake2b(words(text).encode(), digest_size=16).digest()


class Item:
    __slots__ = ("paper_id", "doi", "arxiv_id", "title", "authors", "year", "digest", "notes", "tried")

    def __init__(self, row, tried: dict[int, list[tuple[str, str]]]):
        self.paper_id, self.doi, self.arxiv_id, self.title, self.authors, self.year = row[:6]
        self.digest = digest(row[6])
        past = tried.get(self.paper_id, [])
        self.notes: list[str] = [note for _, note in past]
        self.tried: set[str] = {source for source, _ in past}

    def paper(self) -> c.Paper:
        return c.Paper(self.paper_id, self.doi, self.arxiv_id, self.title, json.loads(self.authors), self.year)

    def needed(self) -> set[str]:
        return ({"arxiv"} if self.arxiv_id else set()) | ({"s2", "oa-single"} if self.doi else set())


def read_progress() -> tuple[set[int], dict[int, list[tuple[str, str]]]]:
    done, tried = set(), {}
    if CHECKED_FILE.exists():
        for line in CHECKED_FILE.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            if parts[1].startswith("-"):
                tried.setdefault(int(parts[0]), []).append((parts[1][1:], parts[2] if len(parts) > 2 else ""))
            else:
                done.add(int(parts[0]))
    return done, tried


def write_unconfirmed() -> int:
    rows = [line.split("\t") for line in CHECKED_FILE.read_text().splitlines()] if CHECKED_FILE.exists() else []
    why = {int(r[0]): r[2] for r in rows if len(r) > 2 and r[1] == "unconfirmed"}
    conn = sqlite3.connect(f"file:{c.DB_PATH}?mode=ro", uri=True, timeout=120)
    lines = ["paper_id\tdoi\tarxiv_id\twhy\ttitle"]
    for paper_id in sorted(why):
        row = conn.execute("SELECT doi, arxiv_id, title FROM paper_summary WHERE paper_id = ?", (paper_id,)).fetchone()
        if row:
            lines.append(f"{paper_id}\t{row[0] or '-'}\t{row[1] or '-'}\t{why[paper_id]}\t{row[2]}")
    conn.close()
    UNCONFIRMED_FILE.write_text("\n".join(lines) + "\n")
    return len(why)


class Verifier:
    def __init__(self, items: list[Item]):
        self.items = items
        self.db = sqlite3.connect(f"file:{c.DB_PATH}?mode=ro", uri=True, check_same_thread=False, timeout=120)
        self.db_lock = threading.Lock()
        self.lock = threading.RLock()
        self.confirmed: dict[int, str] = {}
        self.retry: set[int] = set()
        self.by_source: Counter = Counter()
        self.phase: dict[str, list[int]] = {}
        self.stop = threading.Event()
        self.out = CHECKED_FILE.open("a", buffering=1)
        self.arxiv = c.ArXiv()
        self.openalex = c.OpenAlex()
        self.s2 = c.SemanticScholar()
        self.budget = ""

    def stored(self, paper_id: int) -> str:
        with self.db_lock:
            return self.db.execute("SELECT abstract FROM paper_field_abstract WHERE paper_id = ?",
                                   (paper_id,)).fetchone()[0]

    def same_text(self, item: Item, text: str | None) -> bool:
        if not text:
            return False
        if digest(text) == item.digest:
            return True
        a, b = words(self.stored(item.paper_id)), words(text)
        return SequenceMatcher(None, a[:2000], b[:2000]).ratio() >= SAME_TEXT

    def open(self, item: Item, source: str | None = None) -> bool:
        return item.paper_id not in self.confirmed and (source is None or source not in item.tried)

    def confirm(self, item: Item, source: str) -> None:
        with self.lock:
            if item.paper_id in self.confirmed:
                return
            self.confirmed[item.paper_id] = source
            self.by_source[source] += 1
            self.out.write(f"{item.paper_id}\t{source}\n")

    def note(self, item: Item, source: str, text: str) -> None:
        with self.lock:
            item.notes.append(text)
            item.tried.add(source)
            self.out.write(f"{item.paper_id}\t-{source}\t{text}\n")

    def step(self, name: str, n: int) -> None:
        with self.lock:
            self.phase[name][0] += n

    def check_arxiv(self, items: list[Item], live) -> None:
        by_id: dict[str, list[Item]] = {}
        for it in items:
            if self.open(it, "arxiv"):
                by_id.setdefault(it.arxiv_id, []).append(it)
        ids = sorted(by_id)
        self.phase["arXiv"] = [0, sum(len(v) for v in by_id.values())]
        fails = 0
        for i in range(0, len(ids), c.ARXIV_BATCH):
            if self.stop.is_set():
                return
            chunk = ids[i:i + c.ARXIV_BATCH]
            group = [it for a in chunk for it in by_id[a]]
            try:
                entries = self.arxiv_entries(chunk)
            except c.TransientError as e:
                fails += 1
                with self.lock:
                    self.retry.update(it.paper_id for it in group)
                self.step("arXiv", len(group))
                if fails >= ARXIV_MAX_FAILS:
                    live.log(f"  [arXiv] {e}; {fails} failed calls in a row, arXiv check stops for this run")
                    with self.lock:
                        self.retry.update(it.paper_id for a in ids[i + c.ARXIV_BATCH:] for it in by_id[a])
                    return
                live.log(f"  [arXiv] {e}; pausing {ARXIV_PAUSE // 60} min")
                self.stop.wait(ARXIV_PAUSE)
                continue
            fails = 0
            for it in group:
                entry = entries.get(it.arxiv_id)
                if entry is None:
                    self.note(it, "arxiv", "arXiv: id not found")
                    continue
                summary, names = entry
                ours = {c.surname(a) for a in json.loads(it.authors)} - {""}
                if not ours & ({c.surname(a) for a in names} - {""}):
                    self.note(it, "arxiv", "arXiv: no shared author")
                elif self.same_text(it, summary):
                    self.confirm(it, "arxiv")
                else:
                    self.note(it, "arxiv", "arXiv: different text")
            self.step("arXiv", len(group))
            live.current = f"arXiv {chunk[-1]}"

    def arxiv_entries(self, ids: list[str]) -> dict[str, tuple[str, list[str]]]:
        r = self.arxiv.http.request("GET", c.ARXIV, params={"id_list": ",".join(ids), "max_results": len(ids)})
        if r.status_code == 400:
            out = {}
            if len(ids) > 1:
                for one in ids:
                    out.update(self.arxiv_entries([one]))
            return out
        if r.status_code != 200:
            raise c.TransientError(f"arXiv HTTP {r.status_code}")
        out = {}
        for entry in ET.fromstring(r.text).findall("a:entry", NS):
            m = c.ARXIV_URL_RE.search(entry.findtext("a:id", "", NS))
            if m:
                names = [a.findtext("a:name", "", NS) for a in entry.findall("a:author", NS)]
                out[m.group(1).lower()] = (entry.findtext("a:summary", "", NS), names)
        return out

    def judge(self, items: list[Item], results: dict[int, dict | None], source, name: str) -> None:
        for it in items:
            if it.paper_id not in results:
                continue
            work = results[it.paper_id]
            if work is None:
                self.note(it, name, f"{name}: not found")
            elif not source.same_paper(work, it.paper()):
                self.note(it, name, f"{name}: other paper")
            elif self.same_text(it, source.abstract(work)):
                self.confirm(it, name)
            else:
                self.note(it, name, f"{name}: different text")

    def check_openalex_batch(self, items: list[Item], live) -> None:
        safe = [it for it in items if not any(ch in it.doi for ch in ",|") and self.open(it, "oa-batch")
                and self.open(it, "oa-single")]
        self.phase["OA batch"] = [0, len(safe)]
        chunks = self.openalex.doi_chunks(safe)

        def run(chunk: list[Item]) -> None:
            if self.stop.is_set() or self.budget:
                return
            todo = [it for it in chunk if self.open(it, "oa-batch")]
            if todo:
                try:
                    results = self.openalex.by_doi(todo)
                except c.QuotaExhausted as e:
                    with self.lock:
                        if not self.budget:
                            self.budget = str(e)
                            live.log(f"  [OpenAlex batch] {e}; the other sources carry on")
                    return
                except c.TransientError:
                    results = {}
                self.judge(todo, results, self.openalex, "oa-batch")
                live.current = f"OpenAlex batch {todo[-1].doi}"
            self.step("OA batch", len(chunk))
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(run, chunks))

    def check_s2(self, items: list[Item], live) -> None:
        items = [it for it in items if self.open(it, "s2")]
        self.phase["S2"] = [0, len(items)]
        for i in range(0, len(items), c.S2_BATCH):
            if self.stop.is_set():
                return
            chunk = items[i:i + c.S2_BATCH]
            todo = [it for it in chunk if self.open(it, "s2")]
            if todo:
                try:
                    results = self.s2.by_doi(todo)
                except c.TransientError as e:
                    with self.lock:
                        self.retry.update(it.paper_id for it in todo)
                    live.log(f"  [S2] {e}; {len(todo):,} papers retried next run")
                    results = {}
                self.judge(todo, results, self.s2, "s2")
                live.current = f"S2 {todo[-1].doi}"
            self.step("S2", len(chunk))

    def check_doi(self, items: list[Item], live) -> None:
        self.check_openalex_batch(items, live)
        if not self.stop.is_set():
            self.check_s2(items, live)

    def check_openalex_single(self, items: list[Item], live) -> None:
        todo = [it for it in items if self.open(it, "oa-single")]
        self.phase["OA single"] = [0, len(todo)]

        def run(it: Item) -> None:
            if self.stop.is_set():
                return
            try:
                work = self.openalex.by_doi_one(it.paper())
            except c.TransientError:
                with self.lock:
                    self.retry.add(it.paper_id)
                self.step("OA single", 1)
                return
            self.judge([it], {it.paper_id: work}, self.openalex, "oa-single")
            live.current = f"OpenAlex single {it.doi}"
            self.step("OA single", 1)
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(run, todo))

    def finish(self) -> list[Item]:
        unconfirmed = [it for it in self.items if it.paper_id not in self.confirmed
                       and it.paper_id not in self.retry and it.needed() <= it.tried]
        for it in unconfirmed:
            self.out.write(f"{it.paper_id}\tunconfirmed\t{'; '.join(it.notes) or 'no source to check'}\n")
        self.out.close()
        self.db.close()
        return unconfirmed


class Live:
    def __init__(self, verifier: Verifier, total: int):
        self.v, self.total = verifier, total
        self.live = sys.stdout.isatty()
        self.lock = threading.RLock()
        self.started = time.time()
        self.current, self.waiting = "", None
        self.drawn = False
        self.halt = threading.Event()
        if self.live:
            threading.Thread(target=self.tick, daemon=True).start()

    def tick(self) -> None:
        while not self.halt.wait(0.5):
            with self.lock:
                self.draw()

    def erase(self) -> None:
        if self.drawn:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K\033[1A\r\033[2K")
            self.drawn = False

    def summary(self) -> str:
        n = len(self.v.confirmed)
        by = " · ".join(f"{k} {v:,}" for k, v in self.v.by_source.most_common())
        return (f"confirmed {n:,}/{self.total:,} ({n / max(self.total, 1) * 100:.1f}%) · {by or '–'} · "
                f"⚠ retry {len(self.v.retry):,} · {c.clock(time.time() - self.started)}")

    def phases(self) -> str:
        return " · ".join(f"{k} {d:,}/{t:,}" for k, (d, t) in self.v.phase.items()) or "starting"

    def draw(self) -> None:
        if not self.live:
            return
        width = shutil.get_terminal_size().columns - 1
        bottom = f"    → {self.current}"
        if self.waiting and self.waiting[0] > time.time():
            until, api, reason, attempt, attempts = self.waiting
            bottom = f"    ⏳ {api} waiting {until - time.time():.0f}s ({reason}, attempt {attempt}/{attempts})"
        self.erase()
        sys.stdout.write(("  " + self.summary())[:width] + "\n" + ("    " + self.phases())[:width] + "\n"
                         + bottom[:width])
        sys.stdout.flush()
        self.drawn = True

    def log(self, message: str) -> None:
        with self.lock:
            self.erase()
            print(message, flush=True)
            self.draw()

    def waiter(self, api: str):
        def on_wait(reason: str, seconds: float, attempt: int, attempts: int) -> None:
            with self.lock:
                self.waiting = (time.time() + seconds, api, reason, attempt, attempts)
        return on_wait

    def close(self) -> None:
        self.halt.set()
        with self.lock:
            self.erase()
        sys.stdout.flush()


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  C · Verify Existing Abstracts (read-only)    ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {c.DB_PATH}  (opened read-only)\n")
    c.load_env()
    done, tried = read_progress()
    backfilled = set()
    if BACKFILL_FILE.exists():
        backfilled = {int(x) for x in BACKFILL_FILE.read_text().split()}
    conn = sqlite3.connect(f"file:{c.DB_PATH}?mode=ro", uri=True, timeout=120)
    items = [Item(r, tried) for r in conn.execute(
        "SELECT paper_id, doi, arxiv_id, title, authors, year, abstract FROM paper_summary "
        "WHERE status = 'C' AND abstract IS NOT NULL ORDER BY paper_id")
        if r[0] not in done and r[0] not in backfilled]
    conn.close()
    with_arxiv = [it for it in items if it.arxiv_id]
    with_doi = [it for it in items if it.doi]
    print(f"  Papers at C with an abstract left to verify: {len(items):,}  (finished earlier: {len(done):,} · "
          f"skipped, from the backfill: {len(backfilled):,} checked there)")
    print(f"  arXiv ID {len(with_arxiv):,} · DOI {len(with_doi):,} · neither "
          f"{sum(not it.arxiv_id and not it.doi for it in items):,}")
    print("  arXiv runs side by side with OpenAlex batch → S2; OpenAlex single runs last on what is left")
    print("  Ctrl+C once to stop safely, twice to quit at once\n")
    v = Verifier(items)
    live = Live(v, len(items))
    v.arxiv.http.on_wait = live.waiter("arXiv")
    v.openalex.http.on_wait = live.waiter("OpenAlex")
    v.s2.http.on_wait = live.waiter("Semantic Scholar")

    def on_sigint(signum, frame):
        if v.stop.is_set():
            live.close()
            v.out.flush()
            print("\n  quit at once: progress so far is saved", flush=True)
            os._exit(130)
        v.stop.set()
        live.log("  stopping: finishing requests in flight (Ctrl+C again to quit at once)")
    signal.signal(signal.SIGINT, on_sigint)

    threads = [threading.Thread(target=v.check_arxiv, args=(with_arxiv, live)),
               threading.Thread(target=v.check_doi, args=(with_doi, live))]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        live.log(f"  side-by-side checks done · {live.summary()}")
        if not v.stop.is_set():
            v.check_openalex_single(with_doi, live)
    finally:
        live.close()
        unconfirmed = v.finish()
    total_unconfirmed = write_unconfirmed()
    print("\n" + "━" * 70)
    print(f"  {live.summary()}")
    print(f"  unconfirmed this run {len(unconfirmed):,} · all runs {total_unconfirmed:,}  → listed in {UNCONFIRMED_FILE.name}")
    if v.budget:
        print(f"  OpenAlex batch stopped early: {v.budget}")
    print(f"  calls: arXiv {v.arxiv.http.calls:,}  OpenAlex {v.openalex.http.calls:,}  "
          f"Semantic Scholar {v.s2.http.calls:,}")
    left = sum(it.paper_id not in v.confirmed and not it.needed() <= it.tried for it in items)
    if v.stop.is_set() or v.retry or left:
        print("  Not finished.  → Run temp_verify_abstracts.py again to continue")
    else:
        print("  Done.  The database was not changed.")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
