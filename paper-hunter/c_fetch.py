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
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
ENV_PATH = REPO_ROOT / ".env"
AGENT = "paperhunter/1.0"
TIMEOUT = 60
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
ROUND_PAPERS = 2_000

ARXIV = "https://export.arxiv.org/api/query"
ARXIV_BATCH = 100
ARXIV_INTERVAL = 3.1

OPENALEX = "https://api.openalex.org"
OPENALEX_RPS = 10
OPENALEX_WORKERS = 8
OPENALEX_URL_LIMIT = 3_800
OPENALEX_SELECT = "id,doi,ids,display_name,publication_year,abstract_inverted_index,locations,authorships"
OPENALEX_TITLE_RESULTS = 5
OPENALEX_TITLE_CREDITS = 10

S2 = "https://api.semanticscholar.org/graph/v1"
S2_BATCH = 500
S2_INTERVAL = 1.0
S2_FIELDS = "title,year,authors,externalIds,abstract"

TITLE_SIMILARITY = 0.95
YEAR_TOLERANCE = 1
MIN_ABSTRACT_WORDS = 10
PLACEHOLDER_RE = re.compile(
    r"^(no abstract( is)? available|abstract not available|not available|n/?a|none|"
    r"this article has no abstract|no abstract|abstract|\[no abstract available\])\.?$", re.I)
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v\d+)?", re.I)
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v\d+)?", re.I)
ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})", re.I)


def load_env() -> None:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TransientError(Exception):
    pass


class QuotaExhausted(Exception):
    pass


class RateLimiter:
    def __init__(self, interval: float):
        self.interval = interval
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        if start > now:
            time.sleep(start - now)


class Http:
    def __init__(self, interval: float, attempts: int = 6, first_delay: float = 2.0, max_delay: float = 120.0):
        self.limiter = RateLimiter(interval)
        self.session = requests.Session()
        mailto = os.environ.get("CONTACT_MAILTO")
        self.session.headers["User-Agent"] = AGENT + (f" (mailto:{mailto})" if mailto else "")
        self.attempts, self.first_delay, self.max_delay = attempts, first_delay, max_delay
        self.calls = 0

    def request(self, method: str, url: str, on_429=None, **kwargs) -> requests.Response:
        delay = self.first_delay
        for attempt in range(1, self.attempts + 1):
            self.limiter.wait()
            self.calls += 1
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
            time.sleep(min(delay, self.max_delay))
            delay *= 2
        raise TransientError("no answer")


@dataclass
class Paper:
    paper_id: int
    doi: str | None
    arxiv_id: str | None
    title: str | None
    authors: list[str]
    year: int
    abstract: str | None = None
    new_arxiv: str | None = None
    pending_arxiv: str | None = None
    failed: bool = False


def clean_text(text: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = " ".join(re.sub(r"<[^>]+>", " ", text).split())
    return re.sub(r"^(abstract|summary)\s*[:.\-–—]?\s+", "", text, flags=re.I)


def normalize_title(title: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower()).split())


def valid_abstract(text: str, title: str | None) -> str | None:
    text = clean_text(text)
    if not text or PLACEHOLDER_RE.match(text) or normalize_title(text) == normalize_title(title):
        return None
    return text if len(text.split()) >= MIN_ABSTRACT_WORDS else None


def surname(name: str) -> str:
    name = re.sub(r"\s+\d{4}$", "", name or "").strip()
    if "," in name:
        name = name.split(",", 1)[0]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = re.findall(r"[a-z]+", name)
    return tokens[-1] if tokens else ""


def title_matches(paper: Paper, title: str | None, year, authors: list[str]) -> bool:
    if SequenceMatcher(None, normalize_title(paper.title), normalize_title(title)).ratio() < TITLE_SIMILARITY:
        return False
    if not isinstance(year, int) or abs(year - paper.year) > YEAR_TOLERANCE:
        return False
    ours = {surname(a) for a in paper.authors} - {""}
    return bool(ours & ({surname(a) for a in authors} - {""}))


def canonical_arxiv(value: str | None) -> str | None:
    m = ARXIV_ID_RE.fullmatch((value or "").strip().removeprefix("arXiv:").removeprefix("arxiv:"))
    return m.group(1).lower() if m else None


class ArXiv:
    def __init__(self):
        self.http = Http(ARXIV_INTERVAL)

    def fetch(self, ids: list[str]) -> dict[str, str]:
        out = {}
        for i in range(0, len(ids), ARXIV_BATCH):
            out.update(self.fetch_chunk(ids[i:i + ARXIV_BATCH]))
        return out

    def fetch_chunk(self, ids: list[str]) -> dict[str, str]:
        r = self.http.request("GET", ARXIV, params={"id_list": ",".join(ids), "max_results": len(ids)})
        if r.status_code == 400:
            if len(ids) == 1:
                return {}
            out = {}
            for one in ids:
                out.update(self.fetch_chunk([one]))
            return out
        if r.status_code != 200:
            raise TransientError(f"arXiv HTTP {r.status_code}")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = {}
        for entry in ET.fromstring(r.text).findall("a:entry", ns):
            m = ARXIV_URL_RE.search(entry.findtext("a:id", "", ns))
            summary = entry.findtext("a:summary", "", ns)
            if m and summary:
                out[m.group(1).lower()] = summary
        return out


class _Bearer(requests.auth.AuthBase):
    def __init__(self, key):
        self.key = key

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self.key()}"
        return r


class OpenAlex:
    def __init__(self):
        numbered = sorted((int(m.group(1)), v.strip()) for k, v in os.environ.items()
                          if (m := re.fullmatch(r"OPENALEX_API_KEY_(\d+)", k)) and v.strip())
        self.keys = [v for _, v in numbered]
        if not self.keys:
            sys.exit("No OPENALEX_API_KEY_<n> in .env")
        self.key_no = 0
        self.lock = threading.Lock()
        self.http = Http(1.0 / OPENALEX_RPS)

    def _on_429(self, needed: int):
        def handler(r: requests.Response) -> bool:
            left = r.headers.get("X-RateLimit-Remaining", "").strip()
            if not left.lstrip("-").isdigit() or int(left) >= needed:
                return False
            with self.lock:
                self.key_no += 1
                if self.key_no >= len(self.keys):
                    raise QuotaExhausted("every OpenAlex key has used up its daily budget")
                print(f"\n  [openalex] switching to key {self.key_no + 1}", flush=True)
            return True
        return handler

    def get(self, params: dict, needed: int = 1) -> dict:
        url = f"{OPENALEX}/works?{urllib.parse.urlencode(params, safe=':|/,')}"
        r = self.http.request("GET", url, on_429=self._on_429(needed),
                              auth=_Bearer(lambda: self.keys[min(self.key_no, len(self.keys) - 1)]))
        if r.status_code in (401, 403):
            raise SystemExit(f"OpenAlex rejected key {self.key_no + 1} (HTTP {r.status_code}): {r.text[:200]}")
        if r.status_code != 200:
            raise TransientError(f"OpenAlex HTTP {r.status_code}")
        return r.json()

    @staticmethod
    def abstract(work: dict) -> str:
        inverted = work.get("abstract_inverted_index") or {}
        return " ".join(w for _, w in sorted((i, w) for w, idx in inverted.items() for i in idx))

    @staticmethod
    def arxiv_id(work: dict) -> str | None:
        for doi in ((work.get("ids") or {}).get("doi"), work.get("doi")):
            m = ARXIV_DOI_RE.search(doi or "")
            if m:
                return m.group(1).lower()
        for loc in work.get("locations") or []:
            for url in (loc.get("landing_page_url"), loc.get("pdf_url")):
                m = ARXIV_URL_RE.search(url or "")
                if m:
                    return m.group(1).lower()
        return None

    @staticmethod
    def match_fields(work: dict):
        names = [(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
                 for a in work.get("authorships") or []]
        return work.get("display_name"), work.get("publication_year"), names

    def doi_chunks(self, papers: list[Paper]) -> list[list[Paper]]:
        chunks, current, length = [], [], 0
        for p in papers:
            if any(c in p.doi for c in ",|"):
                chunks.append([p])
                continue
            size = len(urllib.parse.quote(p.doi, safe="/:")) + 1
            if current and (len(current) >= 100 or length + size > OPENALEX_URL_LIMIT):
                chunks.append(current)
                current, length = [], 0
            current.append(p)
            length += size
        return chunks + [current] if current else chunks

    def by_doi(self, papers: list[Paper]) -> dict[int, dict | None]:
        if len(papers) == 1 and any(c in papers[0].doi for c in ",|"):
            url = f"{OPENALEX}/works/https://doi.org/{urllib.parse.quote(papers[0].doi, safe='/')}"
            r = self.http.request("GET", url, params={"select": OPENALEX_SELECT}, on_429=self._on_429(1),
                                  auth=_Bearer(lambda: self.keys[min(self.key_no, len(self.keys) - 1)]))
            if r.status_code == 404:
                return {papers[0].paper_id: None}
            if r.status_code != 200:
                raise TransientError(f"OpenAlex HTTP {r.status_code}")
            return {papers[0].paper_id: r.json()}
        data = self.get({"filter": "doi:" + "|".join(p.doi for p in papers), "per_page": 100,
                         "select": OPENALEX_SELECT})
        works = {(w.get("doi") or "").removeprefix("https://doi.org/").lower(): w for w in data.get("results", [])}
        return {p.paper_id: works.get(p.doi.lower()) for p in papers}

    def by_title(self, paper: Paper) -> dict | None:
        data = self.get({"search.title": normalize_title(paper.title), "per_page": OPENALEX_TITLE_RESULTS,
                         "select": OPENALEX_SELECT,
                         "filter": f"publication_year:{paper.year - YEAR_TOLERANCE}-{paper.year + YEAR_TOLERANCE}"},
                        needed=OPENALEX_TITLE_CREDITS)
        return next((w for w in data.get("results", []) if title_matches(paper, *self.match_fields(w))), None)


class SemanticScholar:
    def __init__(self):
        self.http = Http(S2_INTERVAL, attempts=8, first_delay=5.0)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        r = self.http.request(method, url, **kwargs)
        if r.status_code in (401, 403):
            raise SystemExit(f"Semantic Scholar refused the request (HTTP {r.status_code}): {r.text[:200]}")
        return r

    @staticmethod
    def abstract(work: dict) -> str:
        return work.get("abstract") or ""

    @staticmethod
    def arxiv_id(work: dict) -> str | None:
        return canonical_arxiv((work.get("externalIds") or {}).get("ArXiv"))

    def by_doi(self, papers: list[Paper]) -> dict[int, dict | None]:
        r = self.request("POST", f"{S2}/paper/batch", params={"fields": S2_FIELDS},
                         json={"ids": [f"DOI:{p.doi}" for p in papers]})
        if r.status_code != 200:
            raise TransientError(f"Semantic Scholar HTTP {r.status_code}")
        return {p.paper_id: w for p, w in zip(papers, r.json())}

    def by_title(self, paper: Paper) -> dict | None:
        r = self.request("GET", f"{S2}/paper/search/match", params={"query": paper.title, "fields": S2_FIELDS})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise TransientError(f"Semantic Scholar HTTP {r.status_code}")
        work = next(iter(r.json().get("data") or []), None)
        if work is None:
            return None
        names = [a.get("name") or "" for a in work.get("authors") or []]
        return work if title_matches(paper, work.get("title"), work.get("year"), names) else None


class Fetcher:
    def __init__(self):
        self.arxiv = ArXiv()
        self.openalex = OpenAlex()
        self.s2 = SemanticScholar()
        self.stats: Counter = Counter()

    def open_papers(self, papers: list[Paper], need_doi: bool = False) -> list[Paper]:
        return [p for p in papers if not p.abstract and not p.failed and (p.doi or not need_doi)]

    def accept(self, papers: list[Paper], lookup: str, results: dict[int, dict | None], source) -> None:
        for p in papers:
            work = results.get(p.paper_id)
            if work is None:
                continue
            p.abstract = valid_abstract(source.abstract(work), p.title)
            if p.abstract:
                self.stats[lookup] += 1
            elif not p.arxiv_id and not p.new_arxiv:
                found = source.arxiv_id(work)
                if found:
                    p.new_arxiv = p.pending_arxiv = found
                    self.stats["arxiv ids found"] += 1

    def fail(self, papers: list[Paper], lookup: str, error: Exception) -> None:
        for p in papers:
            p.failed = True
        self.stats["failed"] += len(papers)
        print(f"\n  [{lookup}] {len(papers)} papers left at B for the next run: {error}", flush=True)

    def arxiv_stage(self, papers: list[Paper], lookup: str, attr: str) -> None:
        todo = [p for p in self.open_papers(papers) if getattr(p, attr)]
        if not todo:
            return
        try:
            found = self.arxiv.fetch(sorted({getattr(p, attr) for p in todo}))
        except TransientError as e:
            self.fail(todo, lookup, e)
            return
        for p in todo:
            p.abstract = valid_abstract(found.get(getattr(p, attr), ""), p.title)
            self.stats[lookup] += bool(p.abstract)
            p.pending_arxiv = None

    def doi_stage(self, papers: list[Paper], source, lookup: str, chunks: list[list[Paper]], workers: int) -> None:
        def run(chunk):
            try:
                self.accept(chunk, lookup, source.by_doi(chunk), source)
            except TransientError as e:
                self.fail(chunk, lookup, e)
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(run, chunks))

    def title_stage(self, papers: list[Paper], source, lookup: str, workers: int) -> None:
        def run(p):
            try:
                self.accept([p], lookup, {p.paper_id: source.by_title(p)}, source)
            except TransientError as e:
                self.fail([p], lookup, e)
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(run, [p for p in self.open_papers(papers) if p.title]))

    def chain(self, papers: list[Paper]) -> None:
        self.arxiv_stage(papers, "1 arxiv id", "arxiv_id")
        todo = self.open_papers(papers, need_doi=True)
        self.doi_stage(papers, self.openalex, "2 openalex doi", self.openalex.doi_chunks(todo), OPENALEX_WORKERS)
        self.arxiv_stage(papers, "arxiv fallback", "pending_arxiv")
        self.title_stage(papers, self.openalex, "3 openalex title", OPENALEX_WORKERS)
        self.arxiv_stage(papers, "arxiv fallback", "pending_arxiv")
        todo = self.open_papers(papers, need_doi=True)
        self.doi_stage(papers, self.s2, "4 s2 doi", [todo[i:i + S2_BATCH] for i in range(0, len(todo), S2_BATCH)], 1)
        self.arxiv_stage(papers, "arxiv fallback", "pending_arxiv")
        self.title_stage(papers, self.s2, "5 s2 title", 1)
        self.arxiv_stage(papers, "arxiv fallback", "pending_arxiv")


def load_round(conn: sqlite3.Connection, size: int, skip: set[int]) -> list[Paper]:
    rows = conn.execute(
        "SELECT paper_id, doi, arxiv_id, title, authors, year FROM paper_summary WHERE status = 'B' "
        "AND paper_id NOT IN (SELECT value FROM json_each(?)) ORDER BY paper_id LIMIT ?",
        (json.dumps(sorted(skip)), size)).fetchall()
    return [Paper(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in rows]


def save(conn: sqlite3.Connection, papers: list[Paper]) -> None:
    with conn:
        conn.executemany("UPDATE paper_field_arxiv SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL",
                         [(p.new_arxiv, p.paper_id) for p in papers if p.new_arxiv])
        conn.executemany("UPDATE paper_field_abstract SET abstract = ? WHERE paper_id = ? AND abstract IS NULL",
                         [(p.abstract, p.paper_id) for p in papers if p.abstract])
        conn.executemany("UPDATE paper_status SET status = 'C' WHERE paper_id = ? AND status = 'B'",
                         [(p.paper_id,) for p in papers if not p.failed])


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch abstracts for papers at status B.")
    parser.add_argument("--limit", type=int, help="process at most N papers (for testing)")
    args = parser.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  C · Abstract Fetcher                         ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    load_env()
    conn = connect()
    fetcher = Fetcher()
    started, done, skip = time.time(), 0, set()
    try:
        while args.limit is None or done < args.limit:
            size = ROUND_PAPERS if args.limit is None else min(ROUND_PAPERS, args.limit - done)
            papers = load_round(conn, size, skip)
            if not papers:
                break
            try:
                fetcher.chain(papers)
            except QuotaExhausted as e:
                for p in fetcher.open_papers(papers):
                    p.failed = True
                save(conn, papers)
                print(f"\n  stopping: {e}")
                break
            save(conn, papers)
            skip.update(p.paper_id for p in papers if p.failed)
            done += len(papers)
            found = sum(bool(p.abstract) for p in papers)
            print(f"  round of {len(papers):,}: {found:,} abstracts  |  total {done:,} papers  |  "
                  f"{time.time() - started:,.0f}s", flush=True)
    finally:
        conn.close()
    print("\n" + "━" * 70)
    print("  abstracts by lookup:  " + "  |  ".join(f"{k} {v:,}" for k, v in sorted(fetcher.stats.items())))
    print(f"  calls: arXiv {fetcher.arxiv.http.calls:,}  OpenAlex {fetcher.openalex.http.calls:,}  "
          f"Semantic Scholar {fetcher.s2.http.calls:,}  |  {time.time() - started:,.0f}s")
    print("  Done.  → Give d_abstract.md to the AI next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
