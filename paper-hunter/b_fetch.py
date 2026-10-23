#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import requests

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
ENV_PATH = REPO_ROOT / ".env"
AGENT = "paperhunter-paper-hunter/1.0"

OPENALEX = "https://api.openalex.org"
OPENALEX_BATCH = 50
OPENALEX_RPS = 10
OPENALEX_SELECT = "id,doi,display_name,publication_year,type,keywords,primary_topic,abstract_inverted_index"
OPENALEX_TITLE_SELECT = OPENALEX_SELECT + ",authorships"
OPENALEX_TITLE_RESULTS = 5
OPENALEX_SEARCH_CREDITS = 10

S2 = "https://api.semanticscholar.org/graph/v1"
S2_BATCH = 500
S2_RPS = 0.9
S2_FIELDS = "paperId,externalIds,title,year,authors,venue,publicationTypes,s2FieldsOfStudy,abstract,tldr"

TIMEOUT = 60
SECRET_PARAMS = {"api_key", "apikey", "key", "token"}
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
STEPS = ("doi", "arxiv", "title")
ROUND_ROWS = 5_000
POLL_SECONDS = 15

TITLE_SIMILARITY = 0.95
YEAR_TOLERANCE = 1
PLACEHOLDER_RE = re.compile(
    r"^(no abstract( is)? available|abstract not available|not available|n/?a|none|"
    r"this article has no abstract|no abstract|abstract|\[no abstract available\])\.?$", re.I)
MIN_ABSTRACT_WORDS = 10


def load_env() -> None:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run paper-hunter/a_extract.py first)")
    conn = sqlite3.connect(path, timeout=120, isolation_level="IMMEDIATE", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def redact(url: str, secrets: list[str]) -> str:
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in SECRET_PARAMS]
    clean = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, safe=":|/,")))
    for secret in secrets:
        if secret and secret in clean:
            clean = clean.replace(secret, "REDACTED")
    return clean


class TransientError(Exception):
    pass


class QuotaExhausted(Exception):
    pass


class RateLimiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self.next_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self.next_at:
            time.sleep(self.next_at - now)
        self.next_at = max(time.monotonic(), self.next_at) + self.interval


class Http:
    def __init__(self, limiter: RateLimiter, session: requests.Session | None = None,
                 attempts: int = 6, first_delay: float = 1.0, max_delay: float = 60.0):
        self.limiter = limiter
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = AGENT + (
            f" (mailto:{os.environ['CONTACT_MAILTO']})" if os.environ.get("CONTACT_MAILTO") else "")
        self.attempts, self.first_delay, self.max_delay = attempts, first_delay, max_delay
        self.calls = 0
        self.retries = 0
        self.sent_at: list[float] = []

    def request(self, method: str, url: str, on_429=None, **kwargs) -> requests.Response:
        delay = self.first_delay
        for attempt in range(1, self.attempts + 1):
            self.limiter.wait()
            self.calls += 1
            self.sent_at.append(time.monotonic())
            try:
                r = self.session.request(method, url, timeout=TIMEOUT, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as e:
                reason = type(e).__name__
            else:
                if r.status_code not in TRANSIENT_STATUS:
                    return r
                if r.status_code == 429 and on_429 is not None and on_429(r):
                    continue
                reason = f"HTTP {r.status_code}"
                after = r.headers.get("Retry-After", "")
                if after.isdigit():
                    delay = max(delay, float(after))
            if attempt == self.attempts:
                raise TransientError(f"{reason} after {attempt} attempts")
            self.retries += 1
            time.sleep(min(delay, self.max_delay))
            delay *= 2
        raise TransientError(f"no answer after {self.attempts} attempts")

    def observed_rate(self) -> tuple[float, int]:
        if len(self.sent_at) < 2:
            return 0.0, len(self.sent_at)
        span = self.sent_at[-1] - self.sent_at[0]
        peak, j = 0, 0
        for i, t in enumerate(self.sent_at):
            while self.sent_at[j] <= t - 1.0:
                j += 1
            peak = max(peak, i - j + 1)
        return (len(self.sent_at) - 1) / span if span else 0.0, peak


def rebuild_inverted_index(inverted: dict | None) -> str:
    if not inverted:
        return ""
    positions = [(i, word) for word, idx in inverted.items() for i in idx]
    return " ".join(word for _, word in sorted(positions))


def clean_text(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return re.sub(r"^(abstract|summary)\s*[:.\-–—]?\s+", "", text, flags=re.I)


def normalize_title(title: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower()).split())


def valid_abstract(text: str, title: str | None) -> bool:
    if not text or PLACEHOLDER_RE.match(text):
        return False
    if normalize_title(text) == normalize_title(title):
        return False
    return len(text.split()) >= MIN_ABSTRACT_WORDS


def surname(name: str) -> str:
    name = re.sub(r"\s+\d{4}$", "", name or "").strip()
    if "," in name:
        name = name.split(",", 1)[0]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = re.findall(r"[a-z]+", name)
    return tokens[-1] if tokens else ""


def title_matches(paper: "Paper", title: str | None, year, authors: list[str]) -> bool:
    if SequenceMatcher(None, normalize_title(paper.title), normalize_title(title)).ratio() < TITLE_SIMILARITY:
        return False
    if not isinstance(year, int) or abs(year - paper.year) > YEAR_TOLERANCE:
        return False
    ours = {surname(a) for a in paper.authors} - {""}
    return bool(ours & ({surname(a) for a in authors} - {""}))


@dataclass
class Paper:
    paper_id: int
    doi: str | None
    arxiv_id: str | None
    title: str | None
    authors: list[str]
    year: int


@dataclass
class Outcome:
    paper_id: int
    source: str
    lookup_by: str
    request: str
    response: str
    abstract: str | None


def logged(source: str, by: str) -> str:
    return (f"(EXISTS (SELECT 1 FROM paper_abstract_hit h WHERE h.paper_id = p.paper_id "
            f"AND h.source = '{source}' AND h.lookup_by = '{by}') "
            f"OR EXISTS (SELECT 1 FROM paper_abstract_miss m WHERE m.paper_id = p.paper_id "
            f"AND m.source = '{source}' AND m.lookup_by = '{by}'))")


def found(source: str, by: str) -> str:
    return (f"EXISTS (SELECT 1 FROM paper_abstract_miss f WHERE f.paper_id = p.paper_id "
            f"AND f.source = '{source}' AND f.lookup_by = '{by}' AND f.response <> '')")


def todo(source: str, step: str) -> str:
    doi_done = f"(p.doi IS NULL OR {logged(source, 'doi')})"
    if step == "doi":
        return f"(p.doi IS NOT NULL AND NOT {logged(source, 'doi')})"
    if step == "arxiv":
        return (f"(p.arxiv_id IS NOT NULL AND NOT {logged(source, 'arxiv')} AND {doi_done} "
                f"AND NOT {found(source, 'doi')})")
    return (f"(p.title IS NOT NULL AND NOT {logged(source, 'title')} AND {doi_done} "
            f"AND (p.arxiv_id IS NULL OR {logged(source, 'arxiv')} OR {found(source, 'doi')}) "
            f"AND NOT {found(source, 'doi')} AND NOT {found(source, 'arxiv')})")


def finished(source: str) -> str:
    return " AND ".join(f"NOT {todo(source, step)}" for step in STEPS)


def pending(conn: sqlite3.Connection, source: str, step: str, scope: list[int] | None,
            after: str | None, exclude: set[int]) -> list[Paper]:
    sql = (f"SELECT p.paper_id, p.doi, p.arxiv_id, p.title, p.authors, p.year FROM paper_dblp p "
           f"JOIN paper_abstract_status s ON s.paper_id = p.paper_id "
           f"WHERE s.has_abstract = 0 AND {todo(source, step)}")
    args: list = []
    if after:
        sql += f" AND {finished(after)}"
    if scope is not None:
        sql += " AND p.paper_id IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(scope))
    if exclude:
        sql += " AND p.paper_id NOT IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(sorted(exclude)))
    sql += f" ORDER BY p.paper_id LIMIT {ROUND_ROWS}"
    return [Paper(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in conn.execute(sql, args)]


def record(conn: sqlite3.Connection, outcomes: list[Outcome]) -> None:
    with conn:
        for o in outcomes:
            table = "paper_abstract_hit" if o.abstract else "paper_abstract_miss"
            conn.execute(
                f"INSERT INTO {table} (paper_id, source, lookup_by, request, response) VALUES (?, ?, ?, ?, ?)",
                (o.paper_id, o.source, o.lookup_by, o.request, o.response),
            )
            if o.abstract:
                stored = conn.execute(
                    "INSERT OR IGNORE INTO paper_abstract (paper_id, abstract, source) VALUES (?, ?, ?)",
                    (o.paper_id, o.abstract, o.source),
                ).rowcount
                conn.execute(
                    "UPDATE paper_abstract_status SET hit_count = hit_count + 1, "
                    "has_abstract = CASE WHEN ? THEN 1 ELSE has_abstract END WHERE paper_id = ?",
                    (stored, o.paper_id),
                )
            else:
                conn.execute("UPDATE paper_abstract_status SET miss_count = miss_count + 1 WHERE paper_id = ?",
                             (o.paper_id,))


class Source:
    name = ""
    batch = 1

    def abstract_of(self, work: dict) -> str:
        raise NotImplementedError

    def match_fields(self, work: dict) -> tuple[str | None, object, list[str]]:
        raise NotImplementedError

    def outcomes(self, papers: list[Paper], works: list[dict], step: str, request: str) -> list[Outcome]:
        out = []
        for paper in papers:
            candidates = works
            if step == "title":
                candidates = [w for w in works if title_matches(paper, *self.match_fields(w))]
            chosen, abstract = None, None
            for work in candidates:
                text = clean_text(self.abstract_of(work))
                if valid_abstract(text, paper.title):
                    chosen, abstract = work, text
                    break
                chosen = chosen or work
            chosen = chosen if chosen is not None else (works[0] if works else None)
            response = json.dumps(chosen, ensure_ascii=False) if chosen is not None else ""
            out.append(Outcome(paper.paper_id, self.name, step, request, response, abstract))
        return out

    def lookup(self, step: str, papers: list[Paper]) -> list[Outcome]:
        if step == "title":
            return [o for paper in papers for o in self.lookup_title(paper)]
        key = (lambda p: p.doi) if step == "doi" else (lambda p: p.arxiv_id)
        groups: dict[str, list[Paper]] = {}
        for paper in papers:
            groups.setdefault(key(paper), []).append(paper)
        return self.lookup_ids(step, groups)

    def lookup_ids(self, step: str, groups: dict[str, list[Paper]]) -> list[Outcome]:
        raise NotImplementedError

    def lookup_title(self, paper: Paper) -> list[Outcome]:
        raise NotImplementedError


class _Bearer(requests.auth.AuthBase):
    def __init__(self, key):
        self.key = key

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self.key()}"
        return r


class OpenAlex(Source):
    name = "openalex"
    batch = OPENALEX_BATCH

    def __init__(self, session: requests.Session | None = None):
        numbered = sorted((int(m.group(1)), v.strip()) for k, v in os.environ.items()
                          if (m := re.fullmatch(r"OPENALEX_API_KEY_(\d+)", k)) and v.strip())
        self.keys = [v for _, v in numbered]
        if not self.keys:
            sys.exit("No OPENALEX_API_KEY_<n> in .env")
        self.key_no = 0
        self.http = Http(RateLimiter(OPENALEX_RPS), session)
        self.remaining: str | None = None
        self.needed = 1

    def _on_429(self, r: requests.Response) -> bool:
        left = r.headers.get("X-RateLimit-Remaining", "").strip()
        if not left.lstrip("-").isdigit() or int(left) >= self.needed:
            return False
        self.key_no += 1
        if self.key_no >= len(self.keys):
            raise QuotaExhausted("every OpenAlex key has used up its daily budget")
        print(f"\n  [openalex] key {self.key_no} used up its daily budget; switching to key {self.key_no + 1}")
        return True

    def get(self, path: str, params: dict, needed: int = 1) -> tuple[requests.Response, str]:
        url = f"{OPENALEX}{path}?{urllib.parse.urlencode(params, safe=':|/,')}"
        self.needed = needed
        r = self.http.request("GET", url, on_429=self._on_429, auth=_Bearer(lambda: self.keys[self.key_no]))
        if r.status_code in (401, 403):
            raise SystemExit(f"OpenAlex rejected key {self.key_no + 1} (HTTP {r.status_code}): {r.text[:200]}")
        self.remaining = r.headers.get("X-RateLimit-Remaining", self.remaining)
        return r, redact(url, self.keys)

    def abstract_of(self, work: dict) -> str:
        return rebuild_inverted_index(work.get("abstract_inverted_index"))

    def match_fields(self, work: dict):
        names = [(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
                 for a in work.get("authorships") or []]
        return work.get("display_name"), work.get("publication_year"), names

    def lookup_ids(self, step: str, groups: dict[str, list[Paper]]) -> list[Outcome]:
        doi_of = (lambda k: k) if step == "doi" else (lambda k: f"10.48550/arxiv.{k}")
        wanted = {doi_of(k).lower(): k for k in groups}
        works: dict[str, list[dict]] = {}
        page, seen, request_url = 1, 0, ""
        while True:
            r, url = self.get("/works", {"filter": "doi:" + "|".join(wanted), "per_page": 100,
                                         "page": page, "select": OPENALEX_SELECT})
            request_url = request_url or url
            if r.status_code != 200:
                return [o for k, group in groups.items() for o in self.lookup_single(step, doi_of(k), group)]
            data = r.json()
            for work in data.get("results", []):
                doi = (work.get("doi") or "").removeprefix("https://doi.org/").lower()
                if doi in wanted:
                    works.setdefault(wanted[doi], []).append(work)
            seen += len(data.get("results", []))
            if seen >= data.get("meta", {}).get("count", 0) or not data.get("results"):
                break
            page += 1
        return [o for k, group in groups.items() for o in self.outcomes(group, works.get(k, []), step, request_url)]

    def lookup_single(self, step: str, doi: str, group: list[Paper]) -> list[Outcome]:
        r, url = self.get(f"/works/doi:{urllib.parse.quote(doi, safe='/')}", {"select": OPENALEX_SELECT})
        return self.outcomes(group, [r.json()] if r.status_code == 200 else [], step, url)

    def lookup_title(self, paper: Paper) -> list[Outcome]:
        params = {"search.title": normalize_title(paper.title), "per_page": OPENALEX_TITLE_RESULTS,
                  "select": OPENALEX_TITLE_SELECT,
                  "filter": f"publication_year:{paper.year - YEAR_TOLERANCE}-{paper.year + YEAR_TOLERANCE}"}
        r, url = self.get("/works", params, needed=OPENALEX_SEARCH_CREDITS)
        works = r.json().get("results", []) if r.status_code == 200 else []
        return self.outcomes([paper], works, "title", url)


class SemanticScholar(Source):
    name = "s2"
    batch = S2_BATCH

    def __init__(self, session: requests.Session | None = None):
        self.key = os.environ.get("S2_API_KEY", "").strip()
        if not self.key:
            sys.exit("No S2_API_KEY in .env")
        self.http = Http(RateLimiter(S2_RPS), session, attempts=8, first_delay=5.0, max_delay=120.0)
        self.http.session.headers["x-api-key"] = self.key

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        r = self.http.request(method, url, **kwargs)
        if r.status_code in (401, 403):
            raise SystemExit(f"Semantic Scholar rejected the API key (HTTP {r.status_code}): {r.text[:200]}")
        return r

    def abstract_of(self, work: dict) -> str:
        return work.get("abstract") or ""

    def match_fields(self, work: dict):
        return work.get("title"), work.get("year"), [a.get("name") or "" for a in work.get("authors") or []]

    def lookup_ids(self, step: str, groups: dict[str, list[Paper]]) -> list[Outcome]:
        prefix = "DOI:" if step == "doi" else "ARXIV:"
        keys = list(groups)
        url = f"{S2}/paper/batch?{urllib.parse.urlencode({'fields': S2_FIELDS}, safe=',')}"
        r = self.request("POST", url, json={"ids": [prefix + k for k in keys]})
        if r.status_code != 200:
            return [o for k in keys for o in self.lookup_single(step, prefix + k, groups[k])]
        out = []
        for k, work in zip(keys, r.json()):
            request = f"POST {url} " + json.dumps({"ids": [prefix + k]})
            out += self.outcomes(groups[k], [work] if work else [], step, request)
        return out

    def lookup_single(self, step: str, paper_key: str, group: list[Paper]) -> list[Outcome]:
        url = f"{S2}/paper/{urllib.parse.quote(paper_key, safe=':/')}?" + urllib.parse.urlencode(
            {"fields": S2_FIELDS}, safe=",")
        r = self.request("GET", url)
        return self.outcomes(group, [r.json()] if r.status_code == 200 else [], step, url)

    def lookup_title(self, paper: Paper) -> list[Outcome]:
        url = f"{S2}/paper/search/match?" + urllib.parse.urlencode(
            {"query": paper.title, "fields": S2_FIELDS}, safe=",")
        r = self.request("GET", url)
        works = r.json().get("data", []) if r.status_code == 200 else []
        return self.outcomes([paper], works, "title", url)


class Worker(threading.Thread):
    def __init__(self, source: Source, scope: list[int] | None, after: str | None = None,
                 upstream_done: threading.Event | None = None):
        super().__init__(name=source.name, daemon=True)
        self.source, self.scope, self.after, self.upstream_done = source, scope, after, upstream_done
        self.done = threading.Event()
        self.stats: Counter = Counter()
        self.failed: set[int] = set()
        self.error: BaseException | None = None

    def run(self) -> None:
        conn = connect()
        try:
            while True:
                upstream_finished = self.upstream_done is None or self.upstream_done.is_set()
                progressed = False
                for step in STEPS:
                    progressed |= self.run_step(conn, step)
                if not progressed:
                    if upstream_finished:
                        break
                    time.sleep(POLL_SECONDS)
        except QuotaExhausted as e:
            print(f"\n  [{self.source.name}] stopping: {e}")
        except BaseException as e:
            self.error = e
        finally:
            conn.close()
            self.done.set()

    def run_step(self, conn: sqlite3.Connection, step: str) -> bool:
        papers = pending(conn, self.source.name, step, self.scope, self.after, self.failed)
        size = self.source.batch if step != "title" else 1
        for i in range(0, len(papers), size):
            batch = papers[i:i + size]
            try:
                outcomes = self.source.lookup(step, batch)
            except TransientError as e:
                self.stats[step, "error"] += len(batch)
                self.failed.update(p.paper_id for p in batch)
                print(f"\n  [{self.source.name}] {step} batch of {len(batch)} skipped: {e}")
                continue
            record(conn, outcomes)
            for o in outcomes:
                self.stats[step, "hit" if o.abstract else "miss"] += 1
            self.progress()
        return bool(papers)

    def progress(self) -> None:
        hits = sum(n for (_, kind), n in self.stats.items() if kind == "hit")
        misses = sum(n for (_, kind), n in self.stats.items() if kind == "miss")
        print(f"  [{self.source.name:8s}] hits {hits:,}  misses {misses:,}  calls {self.source.http.calls:,}",
              flush=True)


def scope_papers(conn: sqlite3.Connection, limit: int | None) -> list[int] | None:
    if limit is None:
        return None
    sql = (f"SELECT p.paper_id FROM paper_dblp p JOIN paper_abstract_status s ON s.paper_id = p.paper_id "
           f"WHERE s.has_abstract = 0 AND NOT ({finished('openalex')} AND {finished('s2')}) "
           f"ORDER BY p.paper_id LIMIT ?")
    return [r[0] for r in conn.execute(sql, (limit,))]


def run(limit: int | None, oa_session=None, s2_session=None) -> tuple[Worker, Worker, float]:
    conn = connect()
    scope = scope_papers(conn, limit)
    conn.close()
    print(f"  Scope: {'all pending papers' if scope is None else f'{len(scope):,} papers'}\n")
    started = time.time()
    oa = Worker(OpenAlex(oa_session), scope)
    s2 = Worker(SemanticScholar(s2_session), scope, after="openalex", upstream_done=oa.done)
    oa.start()
    s2.start()
    for w in (oa, s2):
        while w.is_alive():
            w.join(1)
    for w in (oa, s2):
        if w.error is not None:
            raise w.error
    return oa, s2, time.time() - started


def report(workers: tuple[Worker, ...], elapsed: float) -> None:
    print("━" * 70)
    print(f"  {'source':10s}{'lookup_by':>10s}{'hits':>9s}{'misses':>9s}{'errors':>9s}")
    for w in workers:
        for step in STEPS:
            h, m, e = (w.stats[step, k] for k in ("hit", "miss", "error"))
            print(f"  {w.source.name:10s}{step:>10s}{h:>9,}{m:>9,}{e:>9,}")
    for w in workers:
        avg, peak = w.source.http.observed_rate()
        print(f"  {w.source.name}: {w.source.http.calls:,} calls ({w.source.http.retries:,} retries), "
              f"average {avg:.2f}/s, peak {peak} in any 1 s window")
    print(f"  elapsed {elapsed:,.0f}s")
    print("━" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch abstracts from OpenAlex and Semantic Scholar.")
    parser.add_argument("--limit", type=int, help="process only the first N pending papers (for testing)")
    args = parser.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Abstract Fetcher                             ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}")
    print(f"  Limits: OpenAlex {OPENALEX_RPS}/s (documented 30/s), Semantic Scholar {S2_RPS}/s (documented 1/s)")
    load_env()
    oa, s2, elapsed = run(args.limit)
    report((oa, s2), elapsed)
    print()


if __name__ == "__main__":
    main()
