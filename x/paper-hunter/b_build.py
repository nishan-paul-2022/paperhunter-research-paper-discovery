#!/usr/bin/env python3
"""Turn the raw OpenAlex / Semantic Scholar records in paper_source into structured tables.

  paper_detail     one row per paper: ids, the best abstract, TLDR, type, open access, topic, counts
  paper_term       keywords and topics (OpenAlex) and fields of study (Semantic Scholar), one row each
  paper_reference  the works a paper cites, one row per (paper, source, cited work)
  paper_card       view: dblp_paper + paper_detail + terms, what a reviewer reads about one paper

Everything here is derived: `python3 b_build.py --all` rebuilds it from paper_source at any
time without touching the network. a_fetch calls build() after each batch it stores.
"""
import argparse
import html
import json
import re
import sqlite3
import sys
import time
import unicodedata
import zlib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "archive" / "paperhunter.db"
MIN_ABSTRACT = 120          # shorter "abstracts" are mostly boilerplate ("No abstract available.")
BUILD_BATCH = 2000

SCHEMA = """
-- One row per paper that has at least one record in paper_source.
CREATE TABLE IF NOT EXISTS paper_detail (
    paper_id      INTEGER PRIMARY KEY REFERENCES dblp_paper(id),
    openalex_id   TEXT,                  -- W4413176392
    s2_id         TEXT,                  -- Semantic Scholar paperId (40 hex)
    corpus_id     INTEGER,               -- Semantic Scholar CorpusId
    arxiv_id      TEXT,                  -- 2411.05185
    abstract      TEXT,                  -- Semantic Scholar text if usable, else OpenAlex
    abstract_src  TEXT CHECK (abstract_src IN ('s2', 'openalex')),
    tldr          TEXT,                  -- Semantic Scholar one-sentence summary (model-written)
    language      TEXT,                  -- OpenAlex, ISO 639-1
    work_type     TEXT,                  -- OpenAlex type: article, conference-paper, review, ...
    is_retracted  INTEGER CHECK (is_retracted IN (0, 1)),
    is_paratext   INTEGER CHECK (is_paratext IN (0, 1)),   -- front matter, TOC, editorial
    is_oa         INTEGER CHECK (is_oa IN (0, 1)),
    oa_status     TEXT,                  -- gold, green, hybrid, bronze, diamond, closed
    oa_url        TEXT,                  -- best open-access link (landing page or PDF)
    pdf_url       TEXT,                  -- a direct PDF link when one is known
    text_availability TEXT,              -- Semantic Scholar: fulltext, abstract, none
    primary_topic TEXT,                  -- OpenAlex topic hierarchy: topic < subfield < field < domain
    subfield      TEXT,
    field         TEXT,
    domain        TEXT,
    reference_count INTEGER,             -- larger of the two sources
    citation_count  INTEGER,             -- larger of the two sources
    influential_citation_count INTEGER,  -- Semantic Scholar
    built_at      INTEGER NOT NULL
);

-- Multi-valued descriptors. kind: keyword (OpenAlex), topic (OpenAlex), field (Semantic Scholar).
CREATE TABLE IF NOT EXISTS paper_term (
    paper_id INTEGER NOT NULL REFERENCES dblp_paper(id),
    kind     TEXT NOT NULL CHECK (kind IN ('keyword', 'topic', 'field')),
    name     TEXT NOT NULL,
    score    REAL,
    PRIMARY KEY (paper_id, kind, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS paper_term_name ON paper_term (kind, name);

-- Cited works. ref_id is the OpenAlex W-id or the Semantic Scholar paperId of the cited work;
-- Semantic Scholar also gives the cited work's DOI, DBLP key and arXiv id.
CREATE TABLE IF NOT EXISTS paper_reference (
    paper_id INTEGER NOT NULL REFERENCES dblp_paper(id),
    src      TEXT NOT NULL CHECK (src IN ('openalex', 's2')),
    ref_id   TEXT NOT NULL,
    doi      TEXT,
    dblp_key TEXT,
    arxiv_id TEXT,
    PRIMARY KEY (paper_id, src, ref_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS paper_reference_ref ON paper_reference (ref_id);

DROP VIEW IF EXISTS paper_card;
CREATE VIEW paper_card AS
SELECT p.id AS paper_id, p.dblp_key, p.title, p.year, p.venue, p.kind, p.doi,
       d.abstract, d.abstract_src, d.tldr, d.work_type, d.is_paratext, d.language,
       d.primary_topic, d.subfield, d.field,
       (SELECT group_concat(name, '; ') FROM paper_term t WHERE t.paper_id = p.id AND t.kind = 'keyword') AS keywords,
       (SELECT group_concat(name, '; ') FROM paper_term t WHERE t.paper_id = p.id AND t.kind = 'topic') AS topics,
       (SELECT group_concat(name, '; ') FROM paper_term t WHERE t.paper_id = p.id AND t.kind = 'field') AS fields,
       d.arxiv_id, d.oa_url, d.pdf_url, d.openalex_id, d.s2_id
FROM dblp_paper p JOIN paper_detail d ON d.paper_id = p.id;
"""

_TAG = re.compile(r"</?(?:[\w-]+:)?([\w-]+)[^>]*>")
_BLOCK = {"p", "title", "sec", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#v]+(?:v\d+)?)")


def create_schema(conn):
    conn.executescript(SCHEMA)


def unpack(blob):
    return json.loads(zlib.decompress(blob)) if blob else None


def clean_abstract(s):
    """Strip JATS/HTML markup, unescape entities, unfold ligatures (NFKC), collapse whitespace,
    and drop a leading 'Abstract' and the stray leading dashes PDF extraction leaves."""
    s = _TAG.sub(lambda m: " " if m.group(1).lower() in _BLOCK else "", s or "")
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(s))).strip()
    s = re.sub(r"^abstract\b[\s:.–—-]*", "", s, flags=re.IGNORECASE)
    return s.lstrip(" :.–—-")


def openalex_abstract(inverted):
    """Rebuild OpenAlex abstract text from its inverted index {word: [positions]}."""
    if not inverted:
        return ""
    return " ".join(w for _, w in sorted((i, w) for w, idxs in inverted.items() for i in idxs))


def _short_id(url):
    return url.rsplit("/", 1)[-1] if url else None


def _bare_doi(d):
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d or "", flags=re.IGNORECASE).lower() or None


def _flag(v):
    return None if v is None else int(bool(v))


def derive(paper_id, oa, s2):
    """Raw records (either may be None) -> (detail row dict, term rows, reference rows)."""
    oa, s2 = oa or {}, s2 or {}
    s2_ids = s2.get("externalIds") or {}

    abstract, abstract_src = None, None
    for src, text in (("s2", clean_abstract(s2.get("abstract"))),
                      ("openalex", clean_abstract(openalex_abstract(oa.get("abstract_inverted_index"))))):
        if len(text) >= MIN_ABSTRACT:
            abstract, abstract_src = text, src
            break

    arxiv = s2_ids.get("ArXiv")
    pdf = None
    for loc in [oa.get("best_oa_location") or {}] + (oa.get("locations") or []):
        pdf = pdf or loc.get("pdf_url")
        m = _ARXIV_URL.search(loc.get("landing_page_url") or "")
        if m and not arxiv:
            arxiv = m.group(1)
    s2_pdf = (s2.get("openAccessPdf") or {}).get("url") or None
    if not pdf and s2_pdf and (s2_pdf.lower().endswith(".pdf") or "/pdf/" in s2_pdf):
        pdf = s2_pdf
    oa_access = oa.get("open_access") or {}
    s2_status = (s2.get("openAccessPdf") or {}).get("status")
    topic = oa.get("primary_topic") or {}

    def biggest(*xs):
        xs = [x for x in xs if isinstance(x, int)]
        return max(xs) if xs else None

    detail = {
        "paper_id": paper_id,
        "openalex_id": _short_id(oa.get("id")),
        "s2_id": s2.get("paperId"),
        "corpus_id": s2.get("corpusId"),
        "arxiv_id": arxiv,
        "abstract": abstract,
        "abstract_src": abstract_src,
        "tldr": (s2.get("tldr") or {}).get("text") or None,
        "language": oa.get("language"),
        "work_type": oa.get("type"),
        "is_retracted": _flag(oa.get("is_retracted")),
        "is_paratext": _flag(oa.get("is_paratext")),
        "is_oa": _flag(oa_access["is_oa"] if "is_oa" in oa_access else s2.get("isOpenAccess")),
        "oa_status": oa_access.get("oa_status") or (s2_status.lower() if s2_status else None),
        "oa_url": oa_access.get("oa_url") or s2_pdf,
        "pdf_url": pdf,
        "text_availability": s2.get("textAvailability"),
        "primary_topic": topic.get("display_name"),
        "subfield": (topic.get("subfield") or {}).get("display_name"),
        "field": (topic.get("field") or {}).get("display_name"),
        "domain": (topic.get("domain") or {}).get("display_name"),
        "reference_count": biggest(oa.get("referenced_works_count"), s2.get("referenceCount")),
        "citation_count": biggest(oa.get("cited_by_count"), s2.get("citationCount")),
        "influential_citation_count": s2.get("influentialCitationCount"),
        "built_at": int(time.time()),
    }

    terms = {}
    for kind, items in (("keyword", oa.get("keywords")), ("topic", oa.get("topics"))):
        for it in items or []:
            if it.get("display_name"):
                terms[(kind, it["display_name"])] = it.get("score")
    for name in s2.get("fieldsOfStudy") or []:
        terms[("field", name)] = None
    for it in s2.get("s2FieldsOfStudy") or []:
        terms.setdefault(("field", it.get("category")), None)
    term_rows = [(paper_id, k, n, s) for (k, n), s in terms.items() if n]

    refs = {}
    for w in oa.get("referenced_works") or []:
        refs[("openalex", _short_id(w))] = (None, None, None)
    for r in s2.get("references") or []:
        if r and r.get("paperId"):
            ids = r.get("externalIds") or {}
            refs[("s2", r["paperId"])] = (_bare_doi(ids.get("DOI")), ids.get("DBLP"), ids.get("ArXiv"))
    ref_rows = [(paper_id, src, rid, *ids) for (src, rid), ids in refs.items() if rid]
    return detail, term_rows, ref_rows


DETAIL_COLS = ("paper_id", "openalex_id", "s2_id", "corpus_id", "arxiv_id", "abstract", "abstract_src", "tldr",
               "language", "work_type", "is_retracted", "is_paratext", "is_oa", "oa_status", "oa_url", "pdf_url",
               "text_availability", "primary_topic", "subfield", "field", "domain", "reference_count",
               "citation_count", "influential_citation_count", "built_at")


def build(conn, paper_ids):
    """Rebuild the derived rows of these papers from paper_source. The caller owns the transaction."""
    ids = sorted(set(paper_ids))
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        raw = {}
        for pid, src, blob in conn.execute(
                f"SELECT paper_id, src, raw FROM paper_source WHERE found = 1 AND paper_id IN ({marks})", chunk):
            raw.setdefault(pid, {})[src] = unpack(blob)
        for table in ("paper_detail", "paper_term", "paper_reference"):
            conn.execute(f"DELETE FROM {table} WHERE paper_id IN ({marks})", chunk)
        details, terms, refs = [], [], []
        for pid, recs in raw.items():
            d, t, r = derive(pid, recs.get("openalex"), recs.get("s2"))
            details.append(tuple(d[c] for c in DETAIL_COLS))
            terms += t
            refs += r
        conn.executemany(f"INSERT INTO paper_detail ({','.join(DETAIL_COLS)}) "
                         f"VALUES ({','.join('?' * len(DETAIL_COLS))})", details)
        conn.executemany("INSERT INTO paper_term VALUES (?, ?, ?, ?)", terms)
        conn.executemany("INSERT INTO paper_reference VALUES (?, ?, ?, ?, ?, ?)", refs)


def build_all(conn):
    ids = [r[0] for r in conn.execute("SELECT DISTINCT paper_id FROM paper_source WHERE found = 1")]
    for i in range(0, len(ids), BUILD_BATCH):
        with conn:
            build(conn, ids[i:i + BUILD_BATCH])
        print(f"\r  built {min(i + BUILD_BATCH, len(ids)):,}/{len(ids):,}", end="", flush=True)
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="rebuild every paper from paper_source")
    a = ap.parse_args()
    if not a.all:
        ap.print_help()
        sys.exit(2)
    conn = sqlite3.connect(DB_PATH, timeout=120)
    create_schema(conn)
    build_all(conn)
    conn.close()


if __name__ == "__main__":
    main()
