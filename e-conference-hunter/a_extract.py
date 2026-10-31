#!/usr/bin/env python3
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
CONFIG_DIR = REPO_ROOT / "b-config"
CORE_SRC = CONFIG_DIR / "venue-core-2026.csv"
XML_PATH = REPO_ROOT / "d-download" / "dblp.xml"
DB_PATH = REPO_ROOT / "a-database" / "paperhunter.db"
ENV_PATH = REPO_ROOT / ".env"
VALID_RANKS = ("A*", "A", "B", "C")
DBLP_SPARQL = "https://sparql.dblp.org/sparql"
AGENT = "paperhunter-conference-hunter/1.0"
DBLP_LINEAGE_QUERY = (
    "PREFIX dblp: <https://dblp.org/rdf/schema#> SELECT ?stream ?relation ?other WHERE { "
    "VALUES ?relation { dblp:predeccessorStream dblp:successorStream } ?stream ?relation ?other }"
)


def load_env() -> None:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run c-context/b_schema.py first)")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_core(conn: sqlite3.Connection, core_path: Path) -> None:
    print("━" * 70)
    print("  Phase 1 – CORE rankings → conf_core")
    print("━" * 70)
    rows, dropped = [], 0
    with open(core_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rank = r.get("Ranking", "").strip()
            if rank not in VALID_RANKS:
                dropped += 1
                continue
            rows.append((r["Title"].strip(), r["Acronym"].strip(), rank))
    duplicates = [t for t, n in Counter(t for t, _, _ in rows).items() if n > 1]
    if duplicates:
        sys.exit(f"Duplicate core_title values: {duplicates}")
    with conn:
        conn.executemany(
            "INSERT INTO conf_core (core_title, core_acronym, rank) VALUES (?, ?, ?) "
            "ON CONFLICT (core_title) DO UPDATE SET core_acronym = excluded.core_acronym, rank = excluded.rank",
            rows,
        )
        conn.execute("CREATE TEMP TABLE core_titles (core_title TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO core_titles VALUES (?)", [(t,) for t, _, _ in rows])
        removed = conn.execute(
            "DELETE FROM conf_core WHERE core_title NOT IN (SELECT core_title FROM core_titles)"
        ).rowcount
        conn.execute("DROP TABLE core_titles")
    print(f"  ✓  {len(rows)} CORE venues  |  {dropped} rows outside A*–C skipped  |  {removed} removed\n")


def issue_label(number: str) -> str:
    label = re.sub(r"[\s_.-]*\d+$", "", " ".join(number.split()))
    return label if re.search(r"[A-Za-z]", label) else ""


def venue_acronym(key: str) -> str:
    return key.split("#", 1)[1] if "#" in key else key.split("/", 1)[1]


def widen(span: dict, key, year: int) -> None:
    if year:
        lo, hi = span.get(key, (year, year))
        span[key] = (min(lo, year), max(hi, year))


def scan_xml(xml_path: Path):
    from lxml import etree

    size = os.path.getsize(xml_path)
    seen = 0
    papers: Counter = Counter()
    years: dict[str, tuple[int, int]] = {}
    book_titles: dict[str, Counter] = defaultdict(Counter)
    bt_years: dict[tuple[str, str], tuple[int, int]] = {}
    crossrefs: Counter = Counter()
    proceedings: dict[str, tuple[str, int, str, str | None]] = {}
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag=("inproceedings", "proceedings", "article"),
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        parts = key.split("/")
        if len(parts) >= 3 and parts[0] in ("conf", "journals"):
            prefix = "/".join(parts[:2])
            year_text = el.findtext("year") or ""
            year = int(year_text) if year_text.isdigit() else 0
            if el.tag == "inproceedings" and parts[0] == "conf":
                streams = [(prefix, (el.findtext("booktitle") or "").strip())]
                crossref = (el.findtext("crossref") or "").strip()
                if crossref:
                    crossrefs[crossref] += 1
            elif el.tag == "article" and parts[0] == "journals":
                journal = " ".join((el.findtext("journal") or "").split())
                streams = [(prefix, journal)]
                label = issue_label(el.findtext("number") or "")
                if label:
                    streams.append((f"{prefix}#{label}", f"{journal} ({label})" if journal else label))
            else:
                streams = []
                if el.tag == "proceedings" and parts[0] == "conf":
                    title_el = el.find("title")
                    proceeding_title = " ".join("".join(title_el.itertext()).split()) if title_el is not None else ""
                    if proceeding_title:
                        publisher = " ".join((el.findtext("publisher") or "").split()) or None
                        proceedings[key] = (prefix, year, proceeding_title, publisher)
            for stream, book_title in streams:
                papers[stream] += 1
                widen(years, stream, year)
                if book_title:
                    book_titles[stream][book_title] += 1
                    widen(bt_years, (stream, book_title), year)
        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  |  records scanned: {seen:,}  |  venue keys: {len(papers):,}", end="\r", flush=True)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    f.close()
    print(f"  100%  |  records scanned: {seen:,}  |  venue keys: {len(papers):,}")
    venues = [(k, venue_acronym(k), n, *years.get(k, (None, None))) for k, n in papers.items()]
    bts = [(k, b, n, *bt_years.get((k, b), (None, None))) for k in papers for b, n in book_titles[k].items()]
    pts = [(k, pk, y, t, p, crossrefs[pk]) for pk, (k, y, t, p) in proceedings.items() if k in papers]
    return venues, bts, pts


def load_dblp(conn: sqlite3.Connection, xml_path: Path) -> None:
    print("━" * 70)
    print("  Phase 2 – DBLP evidence → conf_dblp_venue, conf_dblp_book_title, conf_dblp_proceeding_title")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB, ~20–30 min)\n")
    venues, bts, pts = scan_xml(xml_path)
    with conn:
        conn.executemany(
            "INSERT INTO conf_dblp_venue (dblp_key, dblp_acronym, papers, first_year, last_year) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (dblp_key) DO UPDATE SET dblp_acronym = excluded.dblp_acronym, papers = excluded.papers, "
            "first_year = excluded.first_year, last_year = excluded.last_year",
            venues,
        )
        conn.execute("CREATE TEMP TABLE scanned (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO scanned VALUES (?)", [(v[0],) for v in venues])
        removed = conn.execute(
            "DELETE FROM conf_dblp_venue WHERE dblp_key NOT IN (SELECT dblp_key FROM scanned)"
        ).rowcount
        conn.execute("DROP TABLE scanned")
        conn.execute("DELETE FROM conf_dblp_book_title")
        conn.execute("DELETE FROM conf_dblp_proceeding_title")
        conn.executemany(
            "INSERT INTO conf_dblp_book_title (dblp_key, book_title, papers, first_year, last_year) VALUES (?, ?, ?, ?, ?)",
            bts,
        )
        conn.executemany(
            "INSERT INTO conf_dblp_proceeding_title (dblp_key, proceeding_key, year, proceeding_title, publisher, papers) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            pts,
        )
    kinds = Counter("stream" if "#" in v[0] else v[0].split("/")[0] for v in venues)
    print(f"\n  ✓  {kinds['conf']:,} conf keys  |  {kinds['journals']:,} journals  |  {kinds['stream']:,} journal "
          f"issue streams  |  {len(bts):,} book_titles  |  {len(pts):,} proceeding_titles  |  {removed} keys removed\n")


def dblp_sparql(query: str) -> list[dict] | None:
    mailto = os.environ.get("CONTACT_MAILTO", "").strip()
    agent = AGENT + (f" (mailto:{mailto})" if mailto else "")
    data = urllib.parse.urlencode({"query": query}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(DBLP_SPARQL, data=data, headers={
                "User-Agent": agent, "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)["results"]["bindings"]
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
            time.sleep(2 ** attempt * 5)
    return None


def load_dblp_lineage(conn: sqlite3.Connection) -> None:
    print("━" * 70)
    print("  Phase 3 – DBLP predecessor/successor links (sparql.dblp.org) → conf_dblp_lineage")
    print("━" * 70)
    rows = dblp_sparql(DBLP_LINEAGE_QUERY)
    if rows is None:
        kept = conn.execute("SELECT count(*) FROM conf_dblp_lineage").fetchone()[0]
        print(f"  ✗ sparql.dblp.org did not answer: keeping the {kept:,} links stored by the last run\n")
        return
    keys = {k for (k,) in conn.execute("SELECT dblp_key FROM conf_dblp_venue")}
    stream = lambda r, f: r[f]["value"].replace("https://dblp.org/streams/", "")
    links = set()
    for r in rows:
        a, b = stream(r, "stream"), stream(r, "other")
        pair = (a, b) if r["relation"]["value"].endswith("successorStream") else (b, a)
        if pair[0] in keys and pair[1] in keys and pair[0] != pair[1]:
            links.add(pair)
    with conn:
        conn.execute("DELETE FROM conf_dblp_lineage")
        conn.executemany("INSERT INTO conf_dblp_lineage VALUES (?, ?)", sorted(links))
    print(f"  ✓  {len(links):,} links between {len({k for p in links for k in p}):,} series\n")


def main() -> None:
    xml_path, core_path = XML_PATH, CORE_SRC
    if not core_path.exists():
        sys.exit(f"CORE CSV not found: {core_path}")
    if not xml_path.exists():
        sys.exit(f"dblp.xml not found: {xml_path}\nRun the downloader first.")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Conference Evidence Loader                   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    load_env()
    conn = connect()
    load_core(conn, core_path)
    load_dblp(conn, xml_path)
    load_dblp_lineage(conn)
    conn.close()
    print("━" * 70)
    print("  Done.  → Run b_match.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
