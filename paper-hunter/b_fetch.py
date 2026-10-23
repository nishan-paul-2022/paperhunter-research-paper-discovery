#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
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

MAX_ATTEMPTS = 6
BACKOFF_CAP = 60.0
TIMEOUT = 60
SECRET_PARAMS = {"api_key", "apikey", "key", "token"}
TRANSIENT_STATUS = {429, 500, 502, 503, 504}

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
    conn = sqlite3.connect(path)
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
        self.next_at = max(now, self.next_at) + self.interval


class Http:

    def __init__(self, limiter: RateLimiter, session: requests.Session | None = None):
        self.limiter = limiter
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = AGENT + (
            f" (mailto:{os.environ['CONTACT_MAILTO']})" if os.environ.get("CONTACT_MAILTO") else "")
        self.calls = 0
        self.retries = 0

    def request(self, method: str, url: str, on_429=None, **kwargs) -> requests.Response:
        delay = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
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
            if attempt == MAX_ATTEMPTS:
                raise TransientError(f"{reason} after {attempt} attempts")
            self.retries += 1
            time.sleep(min(delay, BACKOFF_CAP))
            delay *= 2
        raise TransientError(f"no answer after {MAX_ATTEMPTS} attempts")


def rebuild_inverted_index(inverted: dict | None) -> str:
    if not inverted:
        return ""
    positions = [(i, word) for word, idx in inverted.items() for i in idx]
    return " ".join(word for _, word in sorted(positions))


def clean_text(text: str) -> str:
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


@dataclass
class Outcome:
    paper_id: int
    source: str
    lookup_by: str
    request: str
    response: str
    abstract: str | None
    found: bool


def pending_papers(conn: sqlite3.Connection, source: str, lookup_by: str, column: str, limit: int | None):
    sql = f"""
        SELECT p.paper_id, p.{column}, p.title
        FROM paper_dblp p
        JOIN paper_abstract_status s ON s.paper_id = p.paper_id
        WHERE s.has_abstract = 0 AND p.{column} IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM paper_abstract_hit h
                          WHERE h.paper_id = p.paper_id AND h.source = ? AND h.lookup_by = ?)
          AND NOT EXISTS (SELECT 1 FROM paper_abstract_miss m
                          WHERE m.paper_id = p.paper_id AND m.source = ? AND m.lookup_by = ?)
        ORDER BY p.paper_id
    """ + (" LIMIT ?" if limit is not None else "")
    args = (source, lookup_by, source, lookup_by) + ((limit,) if limit is not None else ())
    return conn.execute(sql, args).fetchall()


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


class OpenAlex:
    name = "openalex"

    def __init__(self, session: requests.Session | None = None):
        numbered = sorted((int(m.group(1)), v.strip()) for k, v in os.environ.items()
                          if (m := re.fullmatch(r"OPENALEX_API_KEY_(\d+)", k)) and v.strip())
        self.keys = [v for _, v in numbered]
        if not self.keys:
            sys.exit("No OPENALEX_API_KEY_<n> in .env")
        self.key_no = 0
        self.http = Http(RateLimiter(OPENALEX_RPS), session)
        self.remaining: str | None = None

    def secrets(self) -> list[str]:
        return self.keys

    def _on_429(self, r: requests.Response) -> bool:
        if r.headers.get("X-RateLimit-Remaining", "").strip() not in ("0", "-1"):
            return False
        self.key_no += 1
        if self.key_no >= len(self.keys):
            raise QuotaExhausted("every OpenAlex key has used up its daily budget")
        print(f"\n  [openalex] key {self.key_no} used up its daily budget; switching to key {self.key_no + 1}")
        return True

    def get(self, path: str, params: dict) -> tuple[requests.Response, str]:
        url = f"{OPENALEX}{path}?{urllib.parse.urlencode(params, safe=':|/,')}"
        r = self.http.request(
            "GET", url, on_429=self._on_429,
            auth=_Bearer(lambda: self.keys[self.key_no]),
        )
        if r.status_code in (401, 403):
            sys.exit(f"OpenAlex rejected key {self.key_no + 1} (HTTP {r.status_code}): {r.text[:200]}")
        self.remaining = r.headers.get("X-RateLimit-Remaining", self.remaining)
        return r, redact(url, self.secrets())

    def lookup_doi_batch(self, papers: list[tuple[int, str, str | None]]) -> list[Outcome]:
        by_doi: dict[str, list[tuple[int, str | None]]] = {}
        for paper_id, doi, title in papers:
            by_doi.setdefault(doi, []).append((paper_id, title))
        works: dict[str, list[dict]] = {}
        page, seen, request_url = 1, 0, ""
        while True:
            r, url = self.get("/works", {"filter": "doi:" + "|".join(by_doi), "per_page": 100,
                                         "page": page, "select": OPENALEX_SELECT})
            request_url = request_url or url
            if r.status_code != 200:
                return [o for doi, group in by_doi.items()
                        for o in self.lookup_doi_single(doi, group)]
            data = r.json()
            for work in data.get("results", []):
                doi = (work.get("doi") or "").removeprefix("https://doi.org/").lower()
                works.setdefault(doi, []).append(work)
            seen += len(data.get("results", []))
            if seen >= data.get("meta", {}).get("count", 0) or not data.get("results"):
                break
            page += 1
        outcomes = []
        for doi, group in by_doi.items():
            outcomes += self._outcomes(group, works.get(doi, []), "doi", request_url)
        return outcomes

    def lookup_doi_single(self, doi: str, group: list[tuple[int, str | None]]) -> list[Outcome]:
        r, url = self.get(f"/works/doi:{urllib.parse.quote(doi, safe='/')}", {"select": OPENALEX_SELECT})
        works = [r.json()] if r.status_code == 200 else []
        return self._outcomes(group, works, "doi", url)

    def _outcomes(self, group, works: list[dict], lookup_by: str, url: str) -> list[Outcome]:
        out = []
        for paper_id, title in group:
            chosen, abstract = None, None
            for work in works:
                text = clean_text(rebuild_inverted_index(work.get("abstract_inverted_index")))
                if valid_abstract(text, title):
                    chosen, abstract = work, text
                    break
                chosen = chosen or work
            response = json.dumps(chosen, ensure_ascii=False) if chosen is not None else ""
            out.append(Outcome(paper_id, self.name, lookup_by, url, response, abstract, chosen is not None))
        return out


class _Bearer(requests.auth.AuthBase):
    def __init__(self, key):
        self.key = key

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self.key()}"
        return r


def run_openalex(conn: sqlite3.Connection, limit: int | None, session: requests.Session | None = None) -> Counter:
    oa = OpenAlex(session)
    papers = pending_papers(conn, oa.name, "doi", "doi", limit)
    print(f"  [openalex] {len(papers):,} papers pending a DOI lookup")
    stats: Counter = Counter()
    started = time.time()
    for i in range(0, len(papers), OPENALEX_BATCH):
        batch = papers[i:i + OPENALEX_BATCH]
        try:
            outcomes = oa.lookup_doi_batch(batch)
        except TransientError as e:
            stats["transient"] += len(batch)
            print(f"\n  [openalex] batch of {len(batch)} skipped, transient error: {e}")
            continue
        except QuotaExhausted as e:
            print(f"\n  [openalex] stopping: {e}")
            break
        record(conn, outcomes)
        for o in outcomes:
            stats["hit" if o.abstract else ("miss_no_abstract" if o.found else "miss_not_found")] += 1
        done = min(i + OPENALEX_BATCH, len(papers))
        print(f"  [openalex] {done:,}/{len(papers):,}  |  hits {stats['hit']:,}  |  "
              f"misses {stats['miss_no_abstract'] + stats['miss_not_found']:,}  |  calls {oa.http.calls:,}  |  "
              f"budget left {oa.remaining}  |  {time.time() - started:,.0f}s", end="\r", flush=True)
    stats["calls"] = oa.http.calls
    stats["retries"] = oa.http.retries
    print()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch abstracts from OpenAlex into paper_abstract.")
    parser.add_argument("--limit", type=int, help="process only the first N pending papers (for testing)")
    args = parser.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Abstract Fetcher                             ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    load_env()
    conn = connect()
    stats = run_openalex(conn, args.limit)
    conn.close()
    misses = stats["miss_no_abstract"] + stats["miss_not_found"]
    print("━" * 70)
    print(f"  hits {stats['hit']:,}  |  misses {misses:,} ({stats['miss_not_found']:,} not found, "
          f"{stats['miss_no_abstract']:,} found without abstract)  |  transient errors {stats['transient']:,}")
    print(f"  OpenAlex calls {stats['calls']:,} (retries {stats['retries']:,})")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
