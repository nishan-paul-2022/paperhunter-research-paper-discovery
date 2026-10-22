#!/usr/bin/env python3
import difflib
import fcntl
import gzip
import html
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
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "archive" / "paperhunter.db"
LOCK_PATH = SCRIPT_DIR / ".details.lock"

OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
OA_BATCH, S2_BATCH = 100, 500
S2_INTERVAL = 1.1
S2_TRIES = 10
OA_SELECT = ",".join((
    "id", "doi", "title", "publication_year", "language", "type", "is_retracted", "is_paratext",
    "keywords", "topics", "abstract_inverted_index", "locations"))
S2_FIELDS = ",".join((
    "paperId", "externalIds", "title", "abstract", "year", "tldr", "fieldsOfStudy", "s2FieldsOfStudy"))
TITLE_MATCH = 0.93
YEAR_SLACK = 2
MAX_FAILED_BATCHES = 3

_TAG = re.compile(r"</?(?:[\w-]+:)?([\w-]+)[^>]*>")
_BLOCK = {"p", "title", "sec", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#v]+(?:v\d+)?)")


class FetchError(Exception):
    pass


class BudgetExhausted(FetchError):
    pass


FAILED = object()


def load_env():
    env = SCRIPT_DIR.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class Throttle:

    def __init__(self, interval):
        self.interval, self.last = interval, 0.0

    def wait(self):
        delay = self.last + self.interval - time.time()
        if delay > 0:
            time.sleep(delay)
        self.last = time.time()


def request(url, throttle, headers, data=None, tries=6, meta=None):
    hdrs = {"User-Agent": "paperhunter-detail/1.0", "Accept-Encoding": "gzip", **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    problem = ""
    for attempt in range(tries):
        throttle.wait()
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=90) as r:
                if meta is not None:
                    meta.update(r.headers)
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
            left = e.headers.get("X-RateLimit-Remaining") if e.headers else None
            if e.code == 429 and left is not None and left.strip() in ("0", "-1"):
                raise BudgetExhausted("daily budget used up")
            after = e.headers.get("Retry-After", "") if e.headers else ""
            delay = float(after) if after.replace(".", "").isdigit() else 5 * 2 ** attempt
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            problem = f"{type(e).__name__}: {e}"
            delay = 5 * 2 ** attempt
        time.sleep(min(delay, 60))
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


class Source:
    name = ""
    table = ""
    cols = ()
    doi_batch = 1

    def lookup_dois(self, dois):
        raise NotImplementedError

    def lookup_title(self, paper):
        raise NotImplementedError

    def row(self, rec):
        raise NotImplementedError

    def note(self):
        return ""


class OpenAlex(Source):
    name, table, doi_batch = "openalex", "paper_openalex", OA_BATCH
    cols = ("openalex_id", "doi", "title", "year", "abstract", "keywords", "topics", "work_type",
            "is_paratext", "is_retracted", "language", "arxiv_id")

    def __init__(self):
        numbered = sorted((int(m.group(1)), v.strip()) for k, v in os.environ.items()
                          if (m := re.fullmatch(r"OPENALEX_API_KEY_(\d+)", k)) and v.strip())
        plain = os.environ.get("OPENALEX_API_KEY", "").strip()
        self.keys = ([plain] if plain else []) + [v for _, v in numbered]
        self.key_no = 0
        mail = os.environ.get("OPENALEX_MAILTO")
        self.mailto = f"&mailto={urllib.parse.quote(mail)}" if mail else ""
        self.throttle = Throttle(0.25 if self.keys else 1.1)
        self.keyed = bool(self.keys)
        self.meta = {}

    def _get(self, url):
        while True:
            headers = {"Authorization": f"Bearer {self.keys[self.key_no]}"} if self.keys else {}
            try:
                return request(url, self.throttle, headers, meta=self.meta)
            except BudgetExhausted:
                if self.key_no + 1 >= len(self.keys):
                    raise BudgetExhausted(f"daily budget used up on all {len(self.keys) or 'keyless'} key(s); "
                                          "re-run tomorrow to continue")
                self.key_no += 1
                print(f"[openalex] key {self.key_no} used up its daily budget; switching to key {self.key_no + 1}",
                      flush=True)

    def note(self):
        left = self.meta.get("X-RateLimit-Remaining") or self.meta.get("x-ratelimit-remaining")
        if left is None:
            return ""
        return f"  | credits left {int(left):,}" + (f" (key {self.key_no + 1}/{len(self.keys)})" if self.keys else "")

    def _works(self, filt, n):
        url = (f"{OPENALEX}/works?filter={urllib.parse.quote(filt, safe=':|')}&per-page={n}"
               f"&select={OA_SELECT}{self.mailto}")
        return (self._get(url) or {}).get("results") or []

    def lookup_dois(self, dois):
        out = {}
        plain = [d for d in dois if not re.search(r"[,|]", d)]
        for w in self._works("doi:" + "|".join(plain), OA_BATCH) if plain else []:
            d = bare_doi(w.get("doi"))
            if d in out and out[d].get("abstract_inverted_index"):
                continue
            out[d] = w
        for d in dois:
            if d not in plain:
                w = self._get(f"{OPENALEX}/works/doi:{urllib.parse.quote(d)}?select={OA_SELECT}{self.mailto}")
                if w:
                    out[d] = w
        return out

    def lookup_title(self, paper):
        q = re.sub(r"[,:;|!?()\[\]\"]", " ", paper["title"].rstrip("."))
        for w in self._works("title.search:" + " ".join(q.split()), 5):
            if title_ok(paper["title"], paper["year"], w.get("title"), w.get("publication_year")):
                return w
        return None

    def row(self, w):
        arxiv = None
        for loc in w.get("locations") or []:
            m = _ARXIV_URL.search((loc or {}).get("landing_page_url") or "")
            if m:
                arxiv = m.group(1)
                break
        return (
            _short_id(w.get("id")),
            bare_doi(w.get("doi")) or None,
            w.get("title"),
            w.get("publication_year"),
            clean_text(openalex_abstract(w.get("abstract_inverted_index"))) or None,
            _names(w.get("keywords")),
            _names(w.get("topics")),
            w.get("type"),
            _flag(w.get("is_paratext")),
            _flag(w.get("is_retracted")),
            w.get("language"),
            arxiv,
        )


class SemanticScholar(Source):
    name, table, doi_batch = "s2", "paper_s2", S2_BATCH
    cols = ("s2_id", "doi", "title", "year", "abstract", "tldr", "fields", "arxiv_id")

    def __init__(self):
        key = os.environ.get("S2_API_KEY")
        self.headers = {"x-api-key": key} if key else {}
        self.throttle = Throttle(S2_INTERVAL if key else 3.0)
        self.keyed = bool(key)

    def lookup_dois(self, dois):
        body = json.dumps({"ids": [f"DOI:{d}" for d in dois]}).encode()
        recs = request(f"{S2}/paper/batch?fields={S2_FIELDS}", self.throttle, self.headers, data=body,
                       tries=S2_TRIES)
        if not isinstance(recs, list) or len(recs) != len(dois):
            raise FetchError("unexpected batch answer")
        return {d: r for d, r in zip(dois, recs) if r}

    def lookup_title(self, paper):
        q = urllib.parse.quote(paper["title"].rstrip("."))
        hit = request(f"{S2}/paper/search/match?query={q}&fields={S2_FIELDS}", self.throttle, self.headers,
                      tries=S2_TRIES)
        for r in (hit or {}).get("data") or []:
            if (r.get("externalIds") or {}).get("DBLP") == paper["dblp_key"] or \
                    title_ok(paper["title"], paper["year"], r.get("title"), r.get("year")):
                r.pop("matchScore", None)
                return r
        return None

    def row(self, r):
        ids = r.get("externalIds") or {}
        fields = [f.get("category") if isinstance(f, dict) else f
                  for f in (r.get("fieldsOfStudy") or []) + (r.get("s2FieldsOfStudy") or [])]
        return (
            r.get("paperId"),
            bare_doi(ids.get("DOI")) or None,
            r.get("title"),
            r.get("year"),
            clean_text(r.get("abstract")) or None,
            clean_text((r.get("tldr") or {}).get("text")) or None,
            "; ".join(dict.fromkeys(f for f in fields if isinstance(f, str) and f)) or None,
            ids.get("ArXiv"),
        )


def clean_text(s):
    s = _TAG.sub(lambda m: " " if m.group(1).lower() in _BLOCK else "", s or "")
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(s))).strip()
    s = re.sub(r"^abstract\b[\s:.–—-]*", "", s, flags=re.IGNORECASE)
    return s.lstrip(" :.–—-")


def openalex_abstract(inverted):
    if not isinstance(inverted, dict):
        return ""
    return " ".join(w for _, w in sorted((i, w) for w, idxs in inverted.items() for i in idxs or []))


def _short_id(url):
    return url.rsplit("/", 1)[-1] if isinstance(url, str) and url else None


def _flag(v):
    return None if v is None else int(bool(v))


def _names(items):
    ranked = sorted((it for it in items or [] if isinstance(it, dict) and it.get("display_name")),
                    key=lambda it: -(it.get("score") or 0))
    return "; ".join(dict.fromkeys(it["display_name"] for it in ranked)) or None


def store(conn, source, via, results):
    cols = ("paper_id", "via", "found", *source.cols)
    missing = (None,) * len(source.cols)
    with conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO {source.table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [(pid, via, int(rec is not None), *(source.row(rec) if rec is not None else missing))
             for pid, rec in results])


class Worker(threading.Thread):

    def __init__(self, source, targets, gaps_only=False, after=None):
        super().__init__(name=source.name)
        self.source, self.targets = source, targets
        self.gaps_only, self.after = gaps_only, after
        self.stats = {"doi_asked": 0, "doi_found": 0, "title_asked": 0, "title_found": 0}
        self.error = None
        self.failed = 0

    def log(self, msg):
        print(f"[{self.source.name:8}] {msg}", flush=True)

    def state(self, conn):
        return {pid: (via, found) for pid, via, found in conn.execute(
            f"SELECT paper_id, via, found FROM {self.source.table}")}

    def attempt(self, call, *args):
        try:
            out = call(*args)
            self.failed = 0
            return out
        except BudgetExhausted:
            raise
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
            self.error = str(e) if isinstance(e, BudgetExhausted) else \
                f"stopped after {MAX_FAILED_BATCHES} failed requests in a row: {e}"
            self.log(self.error)
        finally:
            conn.close()

    def doi_phase(self, conn):
        done = self.state(conn)
        todo = [p for p in self.targets if p["doi"] and p["id"] not in done]
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
            store(conn, self.source, "doi", [(pid, found.get(d)) for d in batch for pid in by_doi[d]])
            self.stats["doi_asked"] += len(batch)
            self.stats["doi_found"] += sum(d in found for d in batch)
            if (i // step) % 20 == 0 or i + step >= len(dois):
                self.log(f"DOI {min(i + step, len(dois)):,}/{len(dois):,}  found {self.stats['doi_found']:,}"
                         + self.source.note())

    def title_phase(self, conn):
        if self.after is not None:
            self.log(f"title phase waits for {self.after.name} to finish")
            self.after.join()
        done = self.state(conn)
        todo = [p for p in self.targets if (not p["doi"] and p["id"] not in done) or done.get(p["id"]) == ("doi", 0)]
        if self.gaps_only:
            have = {r[0] for r in conn.execute("SELECT paper_id FROM paper_summary "
                                               "WHERE abstract_s2 IS NOT NULL OR abstract_openalex IS NOT NULL")}
            todo = [p for p in todo if p["id"] not in have]
        self.log(f"title phase: {len(todo):,} papers" + (" (still without an abstract)" if self.gaps_only else ""))
        pending = []
        for n, p in enumerate(todo, 1):
            rec = self.attempt(self.source.lookup_title, p)
            if rec is FAILED:
                continue
            pending.append((p["id"], rec))
            self.stats["title_asked"] += 1
            self.stats["title_found"] += rec is not None
            if len(pending) >= 50 or n == len(todo):
                store(conn, self.source, "title", pending)
                pending = []
            if n % 500 == 0 or n == len(todo):
                self.log(f"title {n:,}/{len(todo):,}  found {self.stats['title_found']:,}" + self.source.note())
        if pending:
            store(conn, self.source, "title", pending)


def take_lock():
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("ERROR: another b_details.py is already running (the S2 key allows one process)")
    return f


def status(conn):
    total = conn.execute("SELECT count(*) FROM paper_dblp").fetchone()[0]
    print(f"\npaper_dblp: {total:,} papers\n")
    print(f"{'table':14} {'via':6} {'found':>10} {'missing':>10}")
    for table in ("paper_openalex", "paper_s2"):
        for via, found, missing in conn.execute(
                f"SELECT via, sum(found), sum(1 - found) FROM {table} GROUP BY via ORDER BY via"):
            print(f"{table:14} {via:6} {found:>10,} {missing:>10,}")
    checks = (("abstract", "d.abstract_s2 IS NOT NULL OR d.abstract_openalex IS NOT NULL"),
              ("  from s2", "d.abstract_s2 IS NOT NULL"), ("  from openalex", "d.abstract_openalex IS NOT NULL"),
              ("tldr", "d.tldr IS NOT NULL"), ("keywords", "d.keywords IS NOT NULL"),
              ("topics", "d.topics IS NOT NULL"), ("fields", "d.fields IS NOT NULL"),
              ("arxiv_id", "s.arxiv_id IS NOT NULL OR o.arxiv_id IS NOT NULL"),
              ("is_paratext", "d.is_paratext = 1"), ("is_retracted", "o.is_retracted = 1"))
    n, *counts = conn.execute(
        "SELECT count(*), " + ", ".join(f"coalesce(sum({cond}), 0)" for _, cond in checks)
        + " FROM paper_summary d LEFT JOIN paper_openalex o ON o.paper_id = d.paper_id"
          " LEFT JOIN paper_s2 s ON s.paper_id = d.paper_id WHERE o.found = 1 OR s.found = 1").fetchone()
    print(f"\npaper_summary: {n:,} papers found by either API")
    if n:
        for (label, _), k in zip(checks, counts):
            print(f"  {label:16} {k:>10,}  {100 * k / n:5.1f}%")
    print("\npaper_review:")
    for issue, k in conn.execute("SELECT issue, count(*) FROM paper_review GROUP BY issue ORDER BY issue"):
        print(f"  {issue:16} {k:>10,}")


def main():
    load_env()
    lock = take_lock()
    if not DB_PATH.exists():
        sys.exit(f"ERROR: database not found at {DB_PATH} (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(DB_PATH, timeout=300)
    cols = ("id", "dblp_key", "doi", "title", "year")
    targets = [dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM paper_dblp ORDER BY id")]
    if not targets:
        sys.exit("ERROR: paper_dblp is empty (run a_extract.py first)")
    print(f"{len(targets):,} papers  ({sum(1 for p in targets if p['doi']):,} with DOI)")
    s2, oa = SemanticScholar(), OpenAlex()
    for s in (s2, oa):
        if not s.keyed:
            print(f"WARNING: no API key for {s.name}; running at the keyless rate limit")
    s2_worker = Worker(s2, targets)
    oa_worker = Worker(oa, targets, gaps_only=True, after=s2_worker)
    workers = [oa_worker, s2_worker]
    t0 = time.time()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    print(f"\nFetched in {time.time() - t0:,.0f} s")
    for w in workers:
        print(f"  {w.name:8} {json.dumps(w.stats)}" + (f"  ERROR: {w.error}" if w.error else ""))
    status(conn)
    conn.close()
    lock.close()
    if any(w.error for w in workers):
        sys.exit(1)


if __name__ == "__main__":
    main()
