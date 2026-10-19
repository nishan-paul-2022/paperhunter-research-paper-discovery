#!/usr/bin/env python3
"""Fetch every paper's record from OpenAlex and Semantic Scholar into archive/paperhunter.db.

Both APIs run at the same time, each in its own thread with its own rate limit, and each
works through two phases:
  1. DOI   batches of DOIs (OpenAlex 100 per request, Semantic Scholar 250 per request);
  2. title one title lookup per paper, for papers without a DOI or whose DOI the API did not
           know; a hit is kept only if the title matches (and the year is close).
Every answer is stored verbatim (zlib-compressed JSON) in paper_source, one row per
(paper, source). A miss is stored too, so it is not asked again. After each batch,
b_build turns the raw records into paper_detail, paper_term and paper_reference.
Interrupt and re-run at any time: papers that already have an answer are skipped.

Keys come from the environment or from detail-hunter/.env (KEY=value lines):
  OPENALEX_API_KEY   S2_API_KEY   OPENALEX_MAILTO (optional contact address)
Both APIs work without keys, but Semantic Scholar is then very slow (shared pool, many 429s).

Commands:
  python3 a_fetch.py fetch                       every paper in dblp_paper
  python3 a_fetch.py fetch --slice a-star-2025   papers of paper_inventory slices
  python3 a_fetch.py fetch --key conf/uss/X23    single papers by DBLP key (repeatable)
  python3 a_fetch.py fetch --limit 500           the first N target papers (for a trial)
  python3 a_fetch.py status                      coverage per source and field
  python3 a_fetch.py show conf/uss/X23           what is stored for one paper
"""
import argparse
import difflib
import gzip
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import b_build

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "archive" / "paperhunter.db"

OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
OA_BATCH, S2_BATCH = 100, 250
OA_SELECT = ",".join((
    "id", "doi", "title", "publication_year", "publication_date", "ids", "language", "type", "indexed_in",
    "open_access", "primary_location", "best_oa_location", "locations", "is_retracted", "is_paratext",
    "primary_topic", "topics", "keywords", "referenced_works_count", "referenced_works", "cited_by_count",
    "fwci", "has_fulltext", "abstract_inverted_index", "updated_date"))
S2_FIELDS = ",".join((
    "paperId", "corpusId", "externalIds", "url", "title", "abstract", "venue", "year", "publicationDate",
    "publicationTypes", "referenceCount", "citationCount", "influentialCitationCount", "isOpenAccess",
    "openAccessPdf", "fieldsOfStudy", "s2FieldsOfStudy", "tldr", "textAvailability",
    "references.paperId", "references.externalIds"))
TITLE_MATCH = 0.93          # title similarity needed to accept a title-search hit
YEAR_SLACK = 2              # ... and a publication year at most this far from DBLP's
MAX_FAILED_BATCHES = 3      # consecutive failed requests before a source gives up for this run

SCHEMA = """
-- One row per (paper, source): the API's record as returned, or a miss (found = 0, raw NULL).
CREATE TABLE IF NOT EXISTS paper_source (
    paper_id   INTEGER NOT NULL REFERENCES dblp_paper(id),
    src        TEXT NOT NULL CHECK (src IN ('openalex', 's2')),
    via        TEXT NOT NULL CHECK (via IN ('doi', 'title')),   -- how the record was looked up
    found      INTEGER NOT NULL CHECK (found IN (0, 1)),
    raw        BLOB,                                            -- zlib-compressed JSON
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (paper_id, src),
    CHECK ((found = 1) = (raw IS NOT NULL))
);
"""


class FetchError(Exception):
    pass


FAILED = object()           # Worker.attempt's answer when the request itself failed


# --------------------------------------------------------------------------- config and http

def load_env():
    env = SCRIPT_DIR / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class Throttle:
    """Minimum spacing between the requests of one source."""

    def __init__(self, interval):
        self.interval, self.last = interval, 0.0

    def wait(self):
        delay = self.last + self.interval - time.time()
        if delay > 0:
            time.sleep(delay)
        self.last = time.time()


def request(url, throttle, headers, data=None, tries=6):
    """GET (or POST with data) -> parsed JSON, None on 404. Retries 429/5xx and network errors."""
    hdrs = {"User-Agent": "paperhunter-detail/1.0", "Accept-Encoding": "gzip", **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    problem = ""
    for attempt in range(tries):
        throttle.wait()
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
            problem = f"HTTP {e.code}"
            after = e.headers.get("Retry-After", "") if e.headers else ""
            delay = float(after) if after.replace(".", "").isdigit() else 5 * 2 ** attempt
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            problem = f"{type(e).__name__}: {e}"
            delay = 5 * 2 ** attempt
        time.sleep(min(delay, 120))
    raise FetchError(f"gave up after {tries} tries ({problem})")


def norm_title(t):
    t = unicodedata.normalize("NFKC", t or "").lower()
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def title_ok(dblp_title, dblp_year, title, year):
    a, b = norm_title(dblp_title), norm_title(title)
    if not a or not b or difflib.SequenceMatcher(None, a, b).ratio() < TITLE_MATCH:
        return False
    return not isinstance(year, int) or abs(year - dblp_year) <= YEAR_SLACK


def bare_doi(d):
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d or "", flags=re.IGNORECASE).lower()


# --------------------------------------------------------------------------- sources

class Source:
    """One API. lookup_dois / lookup_title return raw records; nothing here touches the DB."""
    name = ""
    doi_batch = 1

    def lookup_dois(self, dois):
        raise NotImplementedError

    def lookup_title(self, paper):
        raise NotImplementedError


class OpenAlex(Source):
    name, doi_batch = "openalex", OA_BATCH

    def __init__(self):
        key = os.environ.get("OPENALEX_API_KEY")
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        mail = os.environ.get("OPENALEX_MAILTO")
        self.mailto = f"&mailto={urllib.parse.quote(mail)}" if mail else ""
        self.throttle = Throttle(0.25 if key else 1.1)   # OR-filters: 5/s with a key, 1/s without
        self.keyed = bool(key)

    def _works(self, filt, n):
        url = (f"{OPENALEX}/works?filter={urllib.parse.quote(filt, safe=':|')}&per-page={n}"
               f"&select={OA_SELECT}{self.mailto}")
        return (request(url, self.throttle, self.headers) or {}).get("results") or []

    def lookup_dois(self, dois):
        out = {}
        plain = [d for d in dois if not re.search(r"[,|]", d)]
        for w in self._works("doi:" + "|".join(plain), OA_BATCH) if plain else []:
            d = bare_doi(w.get("doi"))
            if d in out and out[d].get("abstract_inverted_index"):
                continue                                  # duplicate work; keep the one with an abstract
            out[d] = w
        for d in dois:                                    # DOIs a filter cannot carry: one by one
            if d not in plain:
                w = request(f"{OPENALEX}/works/doi:{urllib.parse.quote(d)}?select={OA_SELECT}{self.mailto}",
                            self.throttle, self.headers)
                if w:
                    out[d] = w
        return out

    def lookup_title(self, paper):
        q = re.sub(r"[,:;|!?()\[\]\"]", " ", paper["title"].rstrip("."))
        for w in self._works("title.search:" + " ".join(q.split()), 5):
            if title_ok(paper["title"], paper["year"], w.get("title"), w.get("publication_year")):
                return w
        return None


class SemanticScholar(Source):
    name, doi_batch = "s2", S2_BATCH

    def __init__(self):
        key = os.environ.get("S2_API_KEY")
        self.headers = {"x-api-key": key} if key else {}
        self.throttle = Throttle(1.05 if key else 3.0)    # a personal key allows 1 request/s
        self.keyed = bool(key)

    def lookup_dois(self, dois):
        body = json.dumps({"ids": [f"DOI:{d}" for d in dois]}).encode()
        recs = request(f"{S2}/paper/batch?fields={S2_FIELDS}", self.throttle, self.headers, data=body)
        if not isinstance(recs, list) or len(recs) != len(dois):
            raise FetchError("unexpected batch answer")
        return {d: r for d, r in zip(dois, recs) if r}

    def lookup_title(self, paper):
        q = urllib.parse.quote(paper["title"].rstrip("."))
        hit = request(f"{S2}/paper/search/match?query={q}&fields={S2_FIELDS}", self.throttle, self.headers)
        for r in (hit or {}).get("data") or []:
            if (r.get("externalIds") or {}).get("DBLP") == paper["dblp_key"] or \
                    title_ok(paper["title"], paper["year"], r.get("title"), r.get("year")):
                r.pop("matchScore", None)
                return r
        return None


# --------------------------------------------------------------------------- pipeline

def pack(rec):
    return zlib.compress(json.dumps(rec, separators=(",", ":"), ensure_ascii=False).encode(), 6)


def store(conn, src, via, results):
    """results: [(paper_id, record or None)]. Stores them and rebuilds their derived rows."""
    now = int(time.time())
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO paper_source (paper_id, src, via, found, raw, fetched_at) VALUES (?,?,?,?,?,?)",
            [(pid, src, via, int(rec is not None), pack(rec) if rec is not None else None, now)
             for pid, rec in results])
        b_build.build(conn, [pid for pid, rec in results if rec is not None])


class Worker(threading.Thread):
    """Runs both phases for one source over the target papers, with its own DB connection."""

    def __init__(self, source, targets, retry_missing):
        super().__init__(name=source.name)
        self.source, self.targets, self.retry_missing = source, targets, retry_missing
        self.stats = {"doi_asked": 0, "doi_found": 0, "title_asked": 0, "title_found": 0}
        self.error = None
        self.failed = 0

    def log(self, msg):
        print(f"[{self.source.name:8}] {msg}", flush=True)

    def state(self, conn):
        return {pid: (via, found) for pid, via, found in conn.execute(
            "SELECT paper_id, via, found FROM paper_source WHERE src = ?", (self.source.name,))}

    def attempt(self, call, *args):
        """Run one API call; FAILED after a failure (the papers stay unasked and are retried next run)."""
        try:
            out = call(*args)
            self.failed = 0
            return out
        except FetchError as e:
            self.failed += 1
            self.log(f"request failed: {e}")
            if self.failed >= MAX_FAILED_BATCHES:
                raise
            return FAILED

    def run(self):
        conn = sqlite3.connect(DB_PATH, timeout=300)
        try:
            self.doi_phase(conn)
            self.title_phase(conn)
        except FetchError as e:
            self.error = f"stopped after {MAX_FAILED_BATCHES} failed requests in a row: {e}"
            self.log(self.error)
        finally:
            conn.close()

    def doi_phase(self, conn):
        done = self.state(conn)
        todo = [p for p in self.targets if p["doi"] and (
            p["id"] not in done or (self.retry_missing and not done[p["id"]][1]))]
        by_doi = {}
        for p in todo:
            by_doi.setdefault(p["doi"], []).append(p["id"])
        dois = list(by_doi)
        self.log(f"DOI phase: {len(todo):,} papers, {len(dois):,} DOIs")
        step = self.source.doi_batch
        for i in range(0, len(dois), step):
            batch = dois[i:i + step]
            found = self.attempt(self.source.lookup_dois, batch)
            if found is FAILED:
                continue
            store(conn, self.source.name, "doi", [(pid, found.get(d)) for d in batch for pid in by_doi[d]])
            self.stats["doi_asked"] += len(batch)
            self.stats["doi_found"] += sum(d in found for d in batch)
            if (i // step) % 20 == 0 or i + step >= len(dois):
                self.log(f"DOI {min(i + step, len(dois)):,}/{len(dois):,}  found {self.stats['doi_found']:,}")

    def title_phase(self, conn):
        done = self.state(conn)
        # no DOI, or the DOI was asked and unknown; a DOI whose request failed waits for the next run
        todo = [p for p in self.targets if (not p["doi"] and p["id"] not in done)
                or done.get(p["id"]) == ("doi", 0)
                or (self.retry_missing and done[p["id"]] == ("title", 0))]
        self.log(f"title phase: {len(todo):,} papers")
        pending = []
        for n, p in enumerate(todo, 1):
            rec = self.attempt(self.source.lookup_title, p)
            if rec is FAILED:
                continue
            pending.append((p["id"], rec))
            self.stats["title_asked"] += 1
            self.stats["title_found"] += rec is not None
            if len(pending) >= 50 or n == len(todo):
                store(conn, self.source.name, "title", pending)
                pending = []
            if n % 500 == 0 or n == len(todo):
                self.log(f"title {n:,}/{len(todo):,}  found {self.stats['title_found']:,}")
        if pending:
            store(conn, self.source.name, "title", pending)


def select_targets(conn, slices, keys, limit):
    sql = "SELECT id, dblp_key, doi, title, year FROM dblp_paper"
    where, args = [], []
    if slices:
        where.append(f"id IN (SELECT id FROM paper_inventory WHERE slice IN ({','.join('?' * len(slices))}))")
        args += slices
    if keys:
        where.append(f"dblp_key IN ({','.join('?' * len(keys))})")
        args += keys
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    cols = ("id", "dblp_key", "doi", "title", "year")
    return [dict(zip(cols, r)) for r in conn.execute(sql, args)]


def cmd_fetch(a):
    conn = open_db()
    targets = select_targets(conn, a.slice, a.key, a.limit)
    conn.close()
    if not targets:
        sys.exit("ERROR: no papers match (check --slice / --key)")
    sources = [s() for s in (OpenAlex, SemanticScholar) if not a.source or s.name == a.source]
    print(f"{len(targets):,} target papers  ({sum(1 for p in targets if p['doi']):,} with DOI)")
    for s in sources:
        if not s.keyed:
            print(f"WARNING: no API key for {s.name}; running at the keyless rate limit")
    workers = [Worker(s, targets, a.retry_missing) for s in sources]
    t0 = time.time()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    print(f"\nDone in {time.time() - t0:,.0f} s")
    for w in workers:
        print(f"  {w.name:8} {json.dumps(w.stats)}" + (f"  ERROR: {w.error}" if w.error else ""))
    if any(w.error for w in workers):
        sys.exit(1)


# --------------------------------------------------------------------------- reporting

def cmd_status(a):
    conn = open_db()
    total = conn.execute("SELECT count(*) FROM dblp_paper").fetchone()[0]
    print(f"dblp_paper: {total:,} papers\n")
    print(f"{'source':9} {'via':6} {'found':>10} {'missing':>10}")
    for src, via, found, missing in conn.execute(
            "SELECT src, via, sum(found), sum(1 - found) FROM paper_source GROUP BY src, via ORDER BY src, via"):
        print(f"{src:9} {via:6} {found:>10,} {missing:>10,}")
    n = conn.execute("SELECT count(*) FROM paper_detail").fetchone()[0]
    print(f"\npaper_detail: {n:,} papers")
    if n:
        for label, cond in (("abstract", "abstract IS NOT NULL"), ("  from s2", "abstract_src = 's2'"),
                            ("  from openalex", "abstract_src = 'openalex'"), ("tldr", "tldr IS NOT NULL"),
                            ("primary_topic", "primary_topic IS NOT NULL"), ("arxiv_id", "arxiv_id IS NOT NULL"),
                            ("pdf_url", "pdf_url IS NOT NULL"), ("is_paratext", "is_paratext = 1")):
            k = conn.execute(f"SELECT count(*) FROM paper_detail WHERE {cond}").fetchone()[0]
            print(f"  {label:16} {k:>10,}  {100 * k / n:5.1f}%")
        for table in ("paper_term", "paper_reference"):
            print(f"{table}: {conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]:,} rows")
    conn.close()


def cmd_show(a):
    conn = open_db()
    conn.row_factory = sqlite3.Row
    card = conn.execute("SELECT * FROM paper_card WHERE dblp_key = ?", (a.dblp_key,)).fetchone()
    if card is None:
        sys.exit(f"no details stored for {a.dblp_key}")
    for k in card.keys():
        if card[k] is not None:
            print(f"{k:14} {card[k]}")
    for r in conn.execute("SELECT src, via, found, length(raw) AS bytes FROM paper_source WHERE paper_id = ?",
                          (card["paper_id"],)):
        print(f"source         {r['src']} via {r['via']}: {'found' if r['found'] else 'missing'}"
              + (f" ({r['bytes']:,} bytes compressed)" if r["found"] else ""))
    refs = conn.execute("SELECT src, count(*) FROM paper_reference WHERE paper_id = ? GROUP BY src",
                        (card["paper_id"],)).fetchall()
    print("references     " + ", ".join(f"{s}: {n}" for s, n in refs))
    conn.close()


def open_db():
    if not DB_PATH.exists():
        sys.exit(f"ERROR: database not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.executescript(SCHEMA)
    b_build.create_schema(conn)
    return conn


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="fetch records from OpenAlex and Semantic Scholar")
    f.add_argument("--slice", action="append", help="paper_inventory slice, e.g. a-star-2025 (repeatable)")
    f.add_argument("--key", action="append", help="DBLP key of one paper (repeatable)")
    f.add_argument("--limit", type=int, help="only the first N target papers")
    f.add_argument("--source", choices=("openalex", "s2"), help="only this API")
    f.add_argument("--retry-missing", action="store_true", help="ask again for papers an API did not know")
    sub.add_parser("status", help="coverage per source and field")
    s = sub.add_parser("show", help="what is stored for one paper")
    s.add_argument("dblp_key")
    a = ap.parse_args()
    {"fetch": cmd_fetch, "status": cmd_status, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
