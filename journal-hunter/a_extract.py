#!/usr/bin/env python3
import csv
import html
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
SCOPUS_SRC = ARCHIVE_DIR / "publication-scopus-computer-science-journal-2025.csv"
XML_PATH = ARCHIVE_DIR / "paper-dblp.xml"
DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"
VALID_QUARTILES = ("Q1", "Q2", "Q3", "Q4")

ISSN_DOI = re.compile(r"doi\.org/10\.\d{4,9}/(?:\(SICI\)|j\.issn\.|issn\.|j\.)?(\d{4})-(\d{3}[\dX])(?![\dA-Z])", re.I)
ISSN_PII = re.compile(r"(?:pii/|10\.1016/)S(\d{4})-?(\d{3}[\dXx])", re.I)
DOI_PREFIX = re.compile(r"doi\.org/(10\.\d{4,9})/", re.I)
DOI_FULL = re.compile(r"doi\.org/(10\.\d{4,9}/\S+)", re.I)
CROSSREF_API = "https://api.crossref.org/works/"
CROSSREF_WORKERS = 5
CROSSREF_PASSES = 3


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Database not found: {path}  (run circumstance/b_schema.py first)")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def format_issn(raw: str) -> str:
    s = raw.strip().replace("-", "").replace(" ", "").upper()
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 else ""


def issn_valid(issn: str) -> bool:
    digits = issn.replace("-", "")
    if len(digits) != 8 or not digits[:7].isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def link_issns(url: str) -> set[str]:
    found = {f"{a}-{b.upper()}" for a, b in ISSN_DOI.findall(url)}
    found |= {f"{a}-{b.upper()}" for a, b in ISSN_PII.findall(url)}
    return {i for i in found if issn_valid(i)}


def load_scopus(conn: sqlite3.Connection, scopus_path: Path) -> None:
    print("━" * 70)
    print("  Phase 1 – Scopus rankings → journal_scopus")
    print("━" * 70)
    rows, dropped = [], 0
    with open(scopus_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            q = r.get("SJR Best Quartile", "").strip()
            if q not in VALID_QUARTILES:
                dropped += 1
                continue
            issns = [format_issn(p) for p in r.get("Issn", "").split(",") if p.strip()]
            issns = [i for i in issns if i] + ["", ""]
            rows.append((int(r["Sourceid"]), r["Title"].strip(), issns[0], issns[1], r["Publisher"].strip(), q,
                         r.get("Coverage", "").strip() or None))
    with conn:
        conn.executemany(
            "INSERT INTO journal_scopus (id, title, issn_1, issn_2, publisher, quartile, coverage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET title = excluded.title, issn_1 = excluded.issn_1, "
            "issn_2 = excluded.issn_2, publisher = excluded.publisher, quartile = excluded.quartile, "
            "coverage = excluded.coverage",
            rows,
        )
        conn.execute("CREATE TEMP TABLE scopus_ids (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO scopus_ids VALUES (?)", [(r[0],) for r in rows])
        removed = conn.execute("DELETE FROM journal_scopus WHERE id NOT IN (SELECT id FROM scopus_ids)").rowcount
        conn.execute("DROP TABLE scopus_ids")
    print(f"  ✓  {len(rows)} Scopus journals  |  {dropped} rows without Q1–Q4 skipped  |  {removed} removed\n")


def scan_xml(xml_path: Path):
    from lxml import etree

    size = os.path.getsize(xml_path)
    seen = 0
    papers: Counter = Counter()
    names: dict[str, Counter] = defaultdict(Counter)
    issns: dict[str, Counter] = defaultdict(Counter)
    prefixes: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, tuple[int, int]] = {}
    name_years: dict[tuple[str, str], tuple[int, int]] = {}
    dois_by_year: dict[tuple[str, str, int], str] = {}
    f = open(xml_path, "rb")
    ctx = etree.iterparse(f, events=("end",), tag="article", load_dtd=True, resolve_entities=True, huge_tree=True)
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        parts = key.split("/")
        if key.startswith("journals/") and len(parts) >= 3:
            prefix = "/".join(parts[:2])
            papers[prefix] += 1
            name = " ".join((el.findtext("journal") or "").split())
            year_text = el.findtext("year") or ""
            year = int(year_text) if year_text.isdigit() else 0
            if name:
                names[prefix][name] += 1
                widen(name_years, (prefix, name), year)
            widen(years, prefix, year)
            found, dois, doi = set(), set(), None
            for ee in el.findall("ee"):
                url = ee.text or ""
                found |= link_issns(url)
                dois.update(DOI_PREFIX.findall(url))
                m = DOI_FULL.search(url)
                if m and not doi:
                    doi = m.group(1)
            if doi and year:
                dois_by_year.setdefault((prefix, name, year), doi)
            for i in found:
                issns[prefix][i] += 1
            for p in dois:
                prefixes[prefix][p] += 1
        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  |  articles scanned: {seen:,}  |  journal keys: {len(papers):,}", end="\r", flush=True)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    f.close()
    print(f"  100%  |  articles scanned: {seen:,}  |  journal keys: {len(papers):,}")
    journals = [(k, n, *years.get(k, (None, None))) for k, n in papers.items()]
    name_rows = [(k, v, n, *name_years.get((k, v), (None, None))) for k in papers for v, n in names[k].items()]
    issn_rows = [(k, v, n) for k in papers for v, n in issns[k].items()]
    prefix_rows = [(k, v, n) for k in papers for v, n in prefixes[k].items()]
    return journals, name_rows, issn_rows, prefix_rows, doi_samples(dois_by_year)


def widen(span: dict, key, year: int) -> None:
    if year:
        lo, hi = span.get(key, (year, year))
        span[key] = (min(lo, year), max(hi, year))


def doi_samples(dois_by_year: dict[tuple[str, str, int], str]) -> list[tuple[str, str, str, int]]:
    by_key: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for (key, name, year), doi in dois_by_year.items():
        by_key[key].append((year, name, doi))
    picked: dict[str, tuple[str, str, int]] = {}
    for key, rows in by_key.items():
        by_name: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        for row in rows:
            by_name[row[1]].append(row)
        chosen = [r for lst in by_name.values() for r in (min(lst), max(lst))] + sorted(rows, reverse=True)[:2]
        for year, name, doi in chosen:
            picked[doi] = (key, name, year)
    return [(doi, key, name, year) for doi, (key, name, year) in picked.items()]


def load_dblp(conn: sqlite3.Connection, xml_path: Path) -> None:
    print("━" * 70)
    print("  Phase 2 – DBLP evidence → journal_dblp_venue, journal_dblp_name, journal_dblp_issn, journal_dblp_doi")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB, ~15–20 min)\n")
    journals, name_rows, issn_rows, prefix_rows, samples = scan_xml(xml_path)
    with conn:
        conn.executemany(
            "INSERT INTO journal_dblp_venue (dblp_key, papers, first_year, last_year) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (dblp_key) DO UPDATE SET papers = excluded.papers, "
            "first_year = excluded.first_year, last_year = excluded.last_year",
            journals,
        )
        conn.execute("CREATE TEMP TABLE scanned (dblp_key TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO scanned VALUES (?)", [(j[0],) for j in journals])
        removed = conn.execute(
            "DELETE FROM journal_dblp_venue WHERE dblp_key NOT IN (SELECT dblp_key FROM scanned)"
        ).rowcount
        conn.execute("DROP TABLE scanned")
        for table in ("journal_dblp_name", "journal_dblp_issn", "journal_dblp_doi"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO journal_dblp_name (dblp_key, name, papers, first_year, last_year) "
                         "VALUES (?, ?, ?, ?, ?)", name_rows)
        conn.executemany("INSERT INTO journal_dblp_issn VALUES (?, ?, ?)", issn_rows)
        conn.executemany("INSERT INTO journal_dblp_doi VALUES (?, ?, ?)", prefix_rows)
        conn.execute("CREATE TEMP TABLE sampled (doi TEXT PRIMARY KEY, dblp_key TEXT, name TEXT, year INTEGER)")
        conn.executemany("INSERT INTO sampled VALUES (?, ?, ?, ?)", samples)
        conn.execute("DELETE FROM journal_dblp_crossref WHERE doi NOT IN (SELECT doi FROM sampled)")
        conn.execute("INSERT INTO journal_dblp_crossref (doi, dblp_key, name, year) SELECT doi, dblp_key, name, year "
                     "FROM sampled WHERE true ON CONFLICT (doi) DO UPDATE SET dblp_key = excluded.dblp_key, "
                     "name = excluded.name, year = excluded.year")
        conn.execute("DROP TABLE sampled")
    with_issn = len({r[0] for r in issn_rows})
    print(
        f"\n  ✓  {len(journals):,} keys  |  {len(name_rows):,} names  |  {with_issn:,} keys with link ISSNs"
        f"  |  {len(prefix_rows):,} DOI prefixes  |  {len(samples):,} DOIs sampled  |  {removed} keys removed\n"
    )


def crossref_lookup(doi: str) -> tuple | None:
    url = CROSSREF_API + urllib.parse.quote(doi, safe="/")
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paperhunter-journal-hunter/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                m = json.load(resp)["message"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return (404, None, None, None, None)
            time.sleep(2 ** attempt * 2)
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            time.sleep(2 ** attempt * 2)
            continue
        typed = {t.get("type"): format_issn(t.get("value", "")) for t in m.get("issn-type") or []}
        issns = [typed.get("print"), typed.get("electronic")]
        issns += [format_issn(i) for i in m.get("ISSN") or []]
        issns = list(dict.fromkeys(i for i in issns if i)) + [None, None]
        titles = [html.unescape(" ".join(t.split())) for t in m.get("container-title") or []] or [None]
        return (200, issns[0], issns[1], titles[0], m.get("publisher"))
    return None


def load_crossref(conn: sqlite3.Connection) -> None:
    print("━" * 70)
    print("  Phase 3 – Crossref ISSN and title per sampled DOI → journal_dblp_crossref")
    print("━" * 70)
    for attempt in range(1, CROSSREF_PASSES + 1):
        pending = [r[0] for r in conn.execute("SELECT doi FROM journal_dblp_crossref WHERE status IS NULL")]
        if not pending:
            break
        if attempt > 1:
            time.sleep(60)
        print(f"  Pass {attempt}: {len(pending):,} DOIs to look up (answers already in the database are kept)\n")
        done = failed = 0
        with ThreadPoolExecutor(CROSSREF_WORKERS) as pool:
            for doi, row in zip(pending, pool.map(crossref_lookup, pending)):
                if row is None:
                    failed += 1
                else:
                    conn.execute("UPDATE journal_dblp_crossref SET status = ?, issn_1 = ?, issn_2 = ?, title = ?, "
                                 "publisher = ? WHERE doi = ?", (*row, doi))
                done += 1
                if done % 100 == 0:
                    conn.commit()
                    print(f"  {done:,}/{len(pending):,}  |  failed: {failed}", end="\r", flush=True)
        conn.commit()
        print()
    stats = dict(conn.execute("SELECT coalesce(status, 0), count(*) FROM journal_dblp_crossref GROUP BY 1"))
    keys = conn.execute("SELECT count(DISTINCT dblp_key) FROM journal_dblp_crossref WHERE status = 200").fetchone()[0]
    print(f"\n  ✓  {stats.get(200, 0):,} found  |  {stats.get(404, 0):,} not in Crossref  |  "
          f"{stats.get(0, 0):,} still pending  |  {keys:,} keys with Crossref evidence\n")
    if stats.get(0):
        print("  ✗ Some lookups still failed: run a_extract.py again to retry them\n")


def main() -> None:
    xml_path, scopus_path = XML_PATH, SCOPUS_SRC
    if not scopus_path.exists():
        sys.exit(f"Scopus CSV not found: {scopus_path}")
    if not xml_path.exists():
        sys.exit(f"paper-dblp.xml not found: {xml_path}\nRun the downloader first.")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Journal Evidence Loader                      ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Database: {DB_PATH}\n")
    conn = connect()
    load_scopus(conn, scopus_path)
    load_dblp(conn, xml_path)
    load_crossref(conn)
    conn.close()
    print("━" * 70)
    print("  Done.  → Run b_match.py next")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
