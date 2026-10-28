#!/usr/bin/env python3
import html
import json
import os
import re
import shutil
import signal
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
DB_PATH = REPO_ROOT / "b-config" / "paperhunter.db"
ENV_PATH = REPO_ROOT / ".env"
AGENT = "paperhunter/1.0"
TIMEOUT = 60
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
CHUNK_PAPERS = 10_000

ARXIV = "https://export.arxiv.org/api/query"
ARXIV_BATCH = 100
ARXIV_INTERVAL = 3.1

OPENALEX = "https://api.openalex.org"
OPENALEX_RPS = 10
OPENALEX_WORKERS = 8
OPENALEX_SINGLE_CHUNK = 20
OPENALEX_URL_LIMIT = 3_800
OPENALEX_SELECT = "id,doi,ids,display_name,publication_year,abstract_inverted_index,locations,authorships"
OPENALEX_TITLE_RESULTS = 5
OPENALEX_TITLE_CREDITS = 10
OPENALEX_BUDGET_WAIT = 600

S2 = "https://api.semanticscholar.org/graph/v1"
S2_BATCH = 500
S2_INTERVAL = 1.0
S2_FIELDS = "title,year,authors,externalIds,abstract"

TITLE_SIMILARITY = 0.95
VERSION_SIMILARITY = 0.80
YEAR_TOLERANCE = 1
PLACEHOLDER_RE = re.compile(
    r"^(no abstract( is)? available|abstract not available|not available|n/?a|none|"
    r"this article has no abstract|no abstract|abstract|\[no abstract available\])\.?$", re.I)
WITHDRAWN_RE = re.compile(
    r"^(this|the) (paper|article|submission|manuscript|preprint|work) (has been|was|is|have been) withdrawn", re.I)
JUNK_RE = re.compile(
    r"^(?:nema|peer-reviewed|status: ?published|version: ?na|altres ajuts: acords transformatius de la uab"
    r"|no abstract is available(?: for this (?:article|paper|chapter))?"
    r"|publisher copyright: ©[^.]*\.(?: all rights reserved)?"
    r"|the author\(s\) declare that no financial support was received for the research, authorship, and/or publication of this article"
    r"|\d{4} chi conference on human factors in computing systems, [^.]*"
    r"|\d+(?:st|nd|rd|th) edition of the interspeech conference, [^.]*"
    r"|the [a-z]+ language resources and evaluation conference \(lrec \d{4}\), [^.]*)\.?$"
    r"|^(?:zora \(zurich open repository and archive\) provides open and worldwide access"
    r"|test 02 - elsevier's scopus, the largest abstract and citation database"
    r"|views icon views article contents"
    r"|inderscience is a global company"
    r"|click on the doi link to access this article at the publishers website"
    r"|the authors have requested that this preprint be removed from research square"
    r"|aims and scopesoftware: practice and experience is an internationally respected"
    r"|overview jis welcomes research and practice articles"
    r"|where the subscription price includes print issues and delivery is to the recipient's address"
    r"|the art of discrete and applied mathematics \(adam\) is a modern, dynamic, platinum open access"
    r"|s mission of unlocking human potential\.for over two centuries, wiley)", re.I)
TAG_RE = re.compile(r"\s*[(\[][^)\]]*[)\]]")
LETTER_RE = re.compile(r"[^\W\d_]")
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
        sys.exit(f"Database not found: {path}  (run c-context/b_schema.py first)")
    conn = sqlite3.connect(path, timeout=120, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


class TransientError(Exception):
    pass


class QuotaExhausted(Exception):
    pass


class Stopped(Exception):
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
        self.on_wait = None

    def request(self, method: str, url: str, on_429=None, **kwargs) -> requests.Response:
        delay, attempt = self.first_delay, 0
        while True:
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
            attempt += 1
            if attempt == self.attempts:
                raise TransientError(f"{reason} after {attempt} attempts")
            if self.on_wait is not None:
                self.on_wait(reason, min(delay, self.max_delay), attempt, self.attempts)
            time.sleep(min(delay, self.max_delay))
            delay *= 2


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
    if not LETTER_RE.search(text) or PLACEHOLDER_RE.match(text) or WITHDRAWN_RE.match(text) or JUNK_RE.search(text):
        return None
    return None if normalize_title(text) == normalize_title(title) else text


def surname(name: str) -> str:
    name = re.sub(r"\s+\d{4}$", "", name or "").strip()
    if "," in name:
        name = name.split(",", 1)[0]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = re.findall(r"[a-z]+", name)
    return tokens[-1] if tokens else ""


def title_similarity(a: str | None, b: str | None) -> float:
    plain = SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()
    untagged = SequenceMatcher(None, normalize_title(TAG_RE.sub(" ", a or "")),
                               normalize_title(TAG_RE.sub(" ", b or ""))).ratio()
    return max(plain, untagged)


def name_tokens(names: list[str]) -> set[str]:
    out = set()
    for name in names:
        name = unicodedata.normalize("NFKD", re.sub(r"\s+\d{4}$", "", name or "")).encode("ascii", "ignore").decode()
        out |= set(re.findall(r"[a-z]{3,}", name.lower()))
    return out


def title_matches(paper: Paper, title: str | None, year, authors: list[str]) -> bool:
    if title_similarity(paper.title, title) < TITLE_SIMILARITY:
        return False
    if not isinstance(year, int) or abs(year - paper.year) > YEAR_TOLERANCE:
        return False
    ours = {surname(a) for a in paper.authors} - {""}
    return bool(ours & ({surname(a) for a in authors} - {""}))


def same_work(paper: Paper, title: str | None, year, authors: list[str]) -> bool:
    if title_matches(paper, title, year, authors):
        return True
    return title_similarity(paper.title, title) >= VERSION_SIMILARITY and bool(
        name_tokens(paper.authors) & name_tokens(authors))


def canonical_arxiv(value: str | None) -> str | None:
    m = ARXIV_ID_RE.fullmatch((value or "").strip().removeprefix("arXiv:").removeprefix("arxiv:"))
    return m.group(1).lower() if m else None


class ArXiv:
    def __init__(self):
        self.http = Http(ARXIV_INTERVAL)

    def fetch(self, ids: list[str], on_chunk=None) -> dict[str, str]:
        out = {}
        for i in range(0, len(ids), ARXIV_BATCH):
            part = self.fetch_chunk(ids[i:i + ARXIV_BATCH])
            out.update(part)
            if on_chunk is not None:
                on_chunk(ids[i:i + ARXIV_BATCH], part)
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


class OpenAlex:
    def __init__(self):
        numbered = sorted((int(m.group(1)), v.strip()) for k, v in os.environ.items()
                          if (m := re.fullmatch(r"OPENALEX_API_KEY_(\d+)", k)) and v.strip())
        if not numbered:
            sys.exit("No OPENALEX_API_KEY_<n> in .env")
        self.key = numbered[0][1]
        self.http = Http(1.0 / OPENALEX_RPS)

    @staticmethod
    def budget_spent(r: requests.Response, needed: int) -> bool:
        if "insufficient budget" in r.text.lower():
            return True
        left = r.headers.get("X-RateLimit-Remaining", "").strip()
        if left.lstrip("-").isdigit():
            return int(left) < needed
        after = r.headers.get("Retry-After", "").strip()
        return after.isdigit() and int(after) > OPENALEX_BUDGET_WAIT

    @staticmethod
    def reset_in(r: requests.Response) -> str:
        for name in ("Retry-After", "X-RateLimit-Reset"):
            value = r.headers.get(name, "").strip()
            if value.isdigit():
                return f" (resets at 00:00 UTC, in {clock(int(value))})"
        return " (resets at 00:00 UTC)"

    def _on_429(self, needed: int):
        def handler(r: requests.Response) -> bool:
            if self.budget_spent(r, needed):
                raise QuotaExhausted("OpenAlex free daily budget is used up" + self.reset_in(r))
            return False
        return handler

    def get(self, params: dict, needed: int = 1) -> dict:
        url = f"{OPENALEX}/works?{urllib.parse.urlencode(params, safe=':|/,')}"
        r = self.http.request("GET", url, on_429=self._on_429(needed),
                              headers={"Authorization": f"Bearer {self.key}"})
        if r.status_code in (401, 403):
            raise SystemExit(f"OpenAlex rejected the key (HTTP {r.status_code}): {r.text[:200]}")
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

    @classmethod
    def same_paper(cls, work: dict, paper: Paper) -> bool:
        doi = (work.get("doi") or "").removeprefix("https://doi.org/").lower()
        return bool(paper.doi and doi == paper.doi.lower()) or same_work(paper, *cls.match_fields(work))

    def doi_chunks(self, papers: list[Paper]) -> list[list[Paper]]:
        chunks, current, length = [], [], 0
        for p in papers:
            size = len(urllib.parse.quote(p.doi, safe="/:")) + 1
            if current and (len(current) >= 100 or length + size > OPENALEX_URL_LIMIT):
                chunks.append(current)
                current, length = [], 0
            current.append(p)
            length += size
        return chunks + [current] if current else chunks

    def by_doi(self, papers: list[Paper]) -> dict[int, dict | None]:
        data = self.get({"filter": "doi:" + "|".join(p.doi for p in papers), "per_page": 100,
                         "select": OPENALEX_SELECT})
        works = {(w.get("doi") or "").removeprefix("https://doi.org/").lower(): w for w in data.get("results", [])}
        return {p.paper_id: works.get(p.doi.lower()) for p in papers}

    def by_doi_one(self, paper: Paper) -> dict | None:
        url = f"{OPENALEX}/works/https://doi.org/{urllib.parse.quote(paper.doi, safe='/')}"
        r = self.http.request("GET", url, params={"select": OPENALEX_SELECT})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise TransientError(f"OpenAlex HTTP {r.status_code}")
        return r.json()

    def by_title(self, paper: Paper) -> dict | None:
        data = self.get({"search.title": normalize_title(paper.title), "per_page": OPENALEX_TITLE_RESULTS,
                         "select": OPENALEX_SELECT,
                         "filter": f"publication_year:{paper.year - YEAR_TOLERANCE}-{paper.year + YEAR_TOLERANCE}"},
                        needed=OPENALEX_TITLE_CREDITS)
        return next((w for w in data.get("results", []) if title_matches(paper, *self.match_fields(w))), None)


class SemanticScholar:
    def __init__(self):
        self.http = Http(S2_INTERVAL, attempts=8, first_delay=5.0)
        key = os.environ.get("S2_API_KEY", "").strip()
        if key:
            self.http.session.headers["x-api-key"] = key

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

    @staticmethod
    def same_paper(work: dict, paper: Paper) -> bool:
        doi = ((work.get("externalIds") or {}).get("DOI") or "").lower()
        if paper.doi and doi == paper.doi.lower():
            return True
        return same_work(paper, work.get("title"), work.get("year"),
                         [a.get("name") or "" for a in work.get("authors") or []])

    def by_doi(self, papers: list[Paper]) -> dict[int, dict | None]:
        r = self.request("POST", f"{S2}/paper/batch", params={"fields": S2_FIELDS},
                         json={"ids": [f"DOI:{p.doi}" for p in papers]})
        if r.status_code != 200:
            raise TransientError(f"Semantic Scholar HTTP {r.status_code}")
        return {p.paper_id: w for p, w in zip(papers, r.json())}



@dataclass
class Stage:
    no: int
    name: str
    source: str
    takes: str
    sets: str
    needs: str


STAGES = [
    Stage(1, "arXiv ID", "arxiv", "B", "B-1", "arxiv_id IS NOT NULL"),
    Stage(2, "OpenAlex DOI batch", "oa-batch", "B-1", "B-2", "doi IS NOT NULL"),
    Stage(3, "OpenAlex DOI single", "oa-single", "B-2", "B-3", "doi IS NOT NULL"),
    Stage(4, "S2 DOI", "s2-doi", "B-3", "B-4", "doi IS NOT NULL"),
    Stage(5, "OpenAlex title", "oa-title", "B-4", "C",
          "doi IS NULL AND arxiv_id IS NULL AND trim(coalesce(title, '')) <> ''"),
]
OPEN_STATUSES = [stage.takes for stage in STAGES]


def clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def describe(p: Paper) -> str:
    return f"doi {p.doi or '–'} · arXiv {p.arxiv_id or p.new_arxiv or '–'} · \"{p.title or ''}\""


class Progress:
    def __init__(self):
        self.live = sys.stdout.isatty()
        self.lock = threading.RLock()
        self.stage: Stage | None = None
        self.total = self.done = self.found = self.missed = self.failed = 0
        self.stage_started = 0.0
        self.current, self.waiting, self.failures = "", None, {}
        self.drawn = False
        self.stop = threading.Event()
        self.ticker = threading.Thread(target=self.tick, daemon=True)
        if self.live:
            self.ticker.start()

    def tick(self) -> None:
        while not self.stop.wait(0.5):
            with self.lock:
                self.draw()

    def erase(self) -> None:
        if self.drawn:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")
            self.drawn = False

    def eta(self) -> str:
        if not self.done:
            return "–"
        return clock((time.time() - self.stage_started) / self.done * max(0, self.total - self.done))

    def counts(self) -> str:
        return f"✓ {self.found:,}  ✗ {self.missed:,}  ⚠ {self.failed:,}"

    def draw(self) -> None:
        if not self.live or self.stage is None:
            return
        width = shutil.get_terminal_size().columns - 1
        top = (f"  stage {self.stage.no}/{len(STAGES)} {self.stage.name} · {self.done:,}/{self.total:,} · {self.counts()} · "
               f"{clock(time.time() - self.stage_started)} · ETA {self.eta()}")
        bottom = f"    → {self.current}"
        if self.waiting and self.waiting[0] > time.time():
            until, api, reason, attempt, attempts = self.waiting
            bottom = f"    ⏳ {api} waiting {until - time.time():.0f}s ({reason}, attempt {attempt}/{attempts})"
        self.erase()
        sys.stdout.write(top[:width] + "\n" + bottom[:width])
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

    def start_stage(self, stage: Stage, total: int, passed: int) -> None:
        with self.lock:
            self.erase()
            print(f"\n  stage {stage.no}/{len(STAGES)} · {stage.name} · {stage.takes} → {stage.sets} · "
                  f"{total:,} to look up · {passed:,} passed on with no call", flush=True)
            self.stage, self.total, self.stage_started = stage, total, time.time()
            self.done = self.found = self.missed = self.failed = 0
            self.current, self.waiting, self.failures = "", None, {}

    def step(self, papers: list[Paper], current: str) -> None:
        with self.lock:
            self.done += len(papers)
            self.found += sum(bool(p.abstract) for p in papers)
            self.failed += sum(p.failed for p in papers)
            self.missed += sum(not p.abstract and not p.failed for p in papers)
            self.current, self.waiting = current, None

    def fail(self, lookup: str, n: int, error: Exception) -> None:
        with self.lock:
            entry = self.failures.setdefault(lookup, [0, ""])
            entry[0] += n
            entry[1] = str(error)

    def end_chunk(self) -> None:
        with self.lock:
            self.erase()
            for lookup, (n, error) in self.failures.items():
                print(f"    ⚠ [{lookup}] {n:,} papers kept at {self.stage.takes}: {error}")
            print(f"    {self.done:,}/{self.total:,} · {self.counts()} · "
                  f"{clock(time.time() - self.stage_started)}", flush=True)
            self.failures = {}
            self.draw()

    def end_stage(self) -> tuple[int, int, int]:
        with self.lock:
            self.erase()
            self.stage = None
            return self.found, self.missed, self.failed

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            self.erase()
            self.stage = None
        sys.stdout.flush()


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.Lock()

    def save(self, papers: list[Paper], takes: str, sets: str) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "UPDATE paper_field_arxiv SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL",
                [(p.new_arxiv, p.paper_id) for p in papers if p.new_arxiv])
            self.conn.executemany(
                "UPDATE paper_field_abstract SET abstract = ? WHERE paper_id = ? AND abstract IS NULL",
                [(p.abstract, p.paper_id) for p in papers if p.abstract])
            self.conn.executemany(
                "UPDATE paper_status SET status = ? WHERE paper_id = ? AND status = ?",
                [("C" if p.abstract else sets, p.paper_id, takes) for p in papers if p.abstract or not p.failed])

    def pass_on(self, stage: Stage) -> int:
        with self.lock, self.conn:
            return self.conn.execute(
                "UPDATE paper_status SET status = ? WHERE status = ? AND paper_id IN "
                f"(SELECT paper_id FROM paper_summary WHERE status = ? AND NOT ({stage.needs}))",
                (stage.sets, stage.takes, stage.takes)).rowcount

    def count(self, stage: Stage) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM paper_summary WHERE status = ? AND {stage.needs}",
                                 (stage.takes,)).fetchone()[0]

    def load(self, stage: Stage, after: int) -> list[Paper]:
        rows = self.conn.execute(
            "SELECT paper_id, doi, arxiv_id, title, authors, year FROM paper_summary "
            f"WHERE status = ? AND paper_id > ? AND {stage.needs} ORDER BY paper_id LIMIT ?",
            (stage.takes, after, CHUNK_PAPERS)).fetchall()
        return [Paper(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in rows]

    def statuses(self) -> dict[str, int]:
        return dict(self.conn.execute(
            f"SELECT status, COUNT(*) FROM paper_status WHERE status IN ({', '.join('?' * len(OPEN_STATUSES))}, 'C') "
            "GROUP BY status", OPEN_STATUSES).fetchall())


class Fetcher:
    def __init__(self, progress: Progress, store: Store):
        self.arxiv = ArXiv()
        self.openalex = OpenAlex()
        self.s2 = SemanticScholar()
        self.stats: Counter = Counter()
        self.summary: dict[int, tuple[int, int, int, int]] = {}
        self.progress = progress
        self.store = store
        self.stopping = threading.Event()
        self.arxiv.http.on_wait = progress.waiter("arXiv")
        self.openalex.http.on_wait = progress.waiter("OpenAlex")
        self.s2.http.on_wait = progress.waiter("Semantic Scholar")

    def check(self) -> None:
        if self.stopping.is_set():
            raise Stopped("Ctrl+C")

    def fail(self, papers: list[Paper], lookup: str, error: Exception) -> None:
        for p in papers:
            p.failed = True
        self.progress.fail(lookup, len(papers), error)

    def fallback(self, papers: list[Paper]) -> None:
        todo = [p for p in papers if not p.abstract and not p.failed and p.pending_arxiv]
        if not todo:
            return
        try:
            found = self.arxiv.fetch(sorted({p.pending_arxiv for p in todo}))
        except TransientError as e:
            self.fail(todo, "arxiv fallback", e)
            return
        for p in todo:
            p.abstract = valid_abstract(found.get(p.pending_arxiv, ""), p.title)
            self.stats["arxiv-fb"] += bool(p.abstract)

    def accept(self, papers: list[Paper], stage: Stage, results: dict[int, dict | None], source) -> None:
        for p in papers:
            work = results[p.paper_id]
            if work is not None and not source.same_paper(work, p):
                work = None
                self.stats["other paper"] += 1
            if work is not None:
                p.abstract = valid_abstract(source.abstract(work), p.title)
            if p.abstract:
                self.stats[stage.source] += 1
                continue
            found = source.arxiv_id(work) if work is not None and not p.arxiv_id else None
            if found:
                p.new_arxiv = found
                self.stats["arxiv ids found"] += 1
            p.pending_arxiv = p.arxiv_id or found
        self.fallback(papers)
        self.store.save(papers, stage.takes, stage.sets)

    def arxiv_chunk(self, papers: list[Paper], stage: Stage) -> None:
        by_id: dict[str, list[Paper]] = {}
        for p in papers:
            by_id.setdefault(p.arxiv_id, []).append(p)
        done = set()

        def on_chunk(ids: list[str], found: dict[str, str]) -> None:
            chunk = [p for i in ids for p in by_id[i]]
            for p in chunk:
                p.abstract = valid_abstract(found.get(p.arxiv_id, ""), p.title)
                self.stats[stage.source] += bool(p.abstract)
            self.store.save(chunk, stage.takes, stage.sets)
            done.update(ids)
            self.progress.step(chunk, f"arXiv {ids[-1]} (batch of {len(ids)})")
            self.check()
        try:
            self.arxiv.fetch(sorted(by_id), on_chunk)
        except TransientError as e:
            rest = [p for i, ps in by_id.items() if i not in done for p in ps]
            self.fail(rest, stage.name, e)
            self.progress.step(rest, str(e))

    def openalex_single(self, papers: list[Paper], stage: Stage) -> dict[int, dict | None]:
        results = {}
        for p in papers:
            if self.stopping.is_set():
                break
            try:
                results[p.paper_id] = self.openalex.by_doi_one(p)
            except TransientError as e:
                self.fail([p], stage.name, e)
        return results

    def doi_chunk(self, papers: list[Paper], stage: Stage) -> None:
        lookup, source = {"oa-batch": (self.openalex.by_doi, self.openalex),
                          "oa-single": (lambda chunk: self.openalex_single(chunk, stage), self.openalex),
                          "s2-doi": (self.s2.by_doi, self.s2)}[stage.source]

        def run(chunk: list[Paper]) -> None:
            self.check()
            results = {}
            try:
                results = lookup(chunk)
            except TransientError as e:
                self.fail(chunk, stage.name, e)
            else:
                self.accept([p for p in chunk if p.paper_id in results], stage, results, source)
            self.progress.step([p for p in chunk if p.failed or p.paper_id in results], describe(chunk[-1]))
        if stage.source == "oa-batch":
            unsafe = [p for p in papers if any(c in p.doi for c in ",|")]
            if unsafe:
                self.accept(unsafe, stage, {p.paper_id: None for p in unsafe}, source)
                self.progress.step(unsafe, describe(unsafe[-1]))
            skip = {p.paper_id for p in unsafe}
            chunks = self.openalex.doi_chunks([p for p in papers if p.paper_id not in skip])
            workers = OPENALEX_WORKERS
        elif stage.source == "oa-single":
            chunks = [papers[i:i + OPENALEX_SINGLE_CHUNK] for i in range(0, len(papers), OPENALEX_SINGLE_CHUNK)]
            workers = OPENALEX_WORKERS
        else:
            chunks, workers = [papers[i:i + S2_BATCH] for i in range(0, len(papers), S2_BATCH)], 1
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(run, chunks))

    def title_chunk(self, papers: list[Paper], stage: Stage) -> None:
        for p in papers:
            self.check()
            try:
                self.accept([p], stage, {p.paper_id: self.openalex.by_title(p)}, self.openalex)
            except TransientError as e:
                self.fail([p], stage.name, e)
            self.progress.step([p], describe(p))

    def run_stage(self, stage: Stage) -> None:
        self.check()
        passed = self.store.pass_on(stage)
        self.progress.start_stage(stage, self.store.count(stage), passed)
        work = {"arxiv": self.arxiv_chunk, "oa-title": self.title_chunk}.get(stage.source, self.doi_chunk)
        after = 0
        try:
            while papers := self.store.load(stage, after):
                after = papers[-1].paper_id
                try:
                    work(papers, stage)
                finally:
                    self.progress.end_chunk()
        finally:
            self.summary[stage.no] = (*self.progress.end_stage(), passed)


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  C · Abstract Fetcher                         ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    load_env()
    conn = connect()
    store = Store(conn)
    before = store.statuses()
    print("  Papers waiting: " + " · ".join(f"{s} {before.get(s, 0):,}" for s in OPEN_STATUSES))
    print("  Ctrl+C once to stop safely, twice to quit at once")
    progress = Progress()
    fetcher = Fetcher(progress, store)

    def on_sigint(signum, frame):
        if fetcher.stopping.is_set():
            progress.close()
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
            except QuotaExhausted as e:
                budget = e
                progress.log(f"    stage {stage.no} paused: {e}; its papers wait for the next run")
    except Stopped as e:
        stopped = e
    finally:
        progress.close()
        after = store.statuses()
        conn.close()
    if stopped:
        print(f"\n  stopped: {stopped}")
    if budget:
        print(f"\n  {budget}")
    print("\n" + "━" * 70)
    for stage in STAGES:
        if stage.no in fetcher.summary:
            found, missed, failed, passed = fetcher.summary[stage.no]
            print(f"  stage {stage.no} {stage.name:<15} ✓ {found:,}  ✗ {missed:,}  ⚠ {failed:,}  · passed on {passed:,}")
    print(f"  arXiv fallback ✓ {fetcher.stats['arxiv-fb']:,}  ·  arXiv ids recovered {fetcher.stats['arxiv ids found']:,}"
          f"  ·  other paper rejected {fetcher.stats['other paper']:,}")
    print(f"  calls: arXiv {fetcher.arxiv.http.calls:,}  OpenAlex {fetcher.openalex.http.calls:,}  "
          f"Semantic Scholar {fetcher.s2.http.calls:,}  |  {clock(time.time() - started)}")
    print("  status now: " + " · ".join(f"{s} {after.get(s, 0):,}" for s in OPEN_STATUSES + ["C"]))
    if any(after.get(s, 0) for s in OPEN_STATUSES):
        print("  Not finished.  → Run c_fetch.py again to continue")
    else:
        print("  Done.  → Give d_abstract.md to the AI next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
