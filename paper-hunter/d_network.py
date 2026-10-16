"""d_network.py: HTTP plumbing and abstract providers for PAPER-HUNTER v3.

Content-only: every provider returns `{"abstract": str, "pdf": str|None, "arxiv": str|None}`
(abstract + two retrieval handles) or None when the source has nothing for the paper.
A network-level failure (no HTTP answer after retries) raises `NetworkError`, so callers can
tell "the source has nothing" (None, safe to cache) from "we could not ask" (retry later).
Throttles are per process and shared by all threads.
"""

import difflib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from html.parser import HTMLParser

from b_db import MIN_ABSTRACT, norm_title

MAILTO = os.environ.get("PH_MAILTO", "")
UA = "paper-hunter/3.0 (literature screening; mailto:%s)" % (MAILTO or "unknown@example.org")
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

URL_CROSSREF = os.environ.get("PH_CROSSREF", "https://api.crossref.org").rstrip("/")
URL_OPENALEX = os.environ.get("PH_OPENALEX", "https://api.openalex.org").rstrip("/")
URL_S2 = os.environ.get("PH_S2", "https://api.semanticscholar.org").rstrip("/")
URL_ARXIV = os.environ.get("PH_ARXIV", "http://export.arxiv.org/api/query")

TITLE_MATCH = 0.93
OA_BATCH, S2_BATCH = 50, 100
OA_SELECT = "doi,title,abstract_inverted_index,best_oa_location,locations"
S2_FIELDS = "title,abstract,externalIds,openAccessPdf"


class NetworkError(Exception):
    """The remote host could not be reached (or kept failing); nothing is known about the paper."""


class Throttle:
    """Minimum spacing between calls, shared by all threads."""

    def __init__(self, interval):
        self.interval, self.last, self.lock = interval, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            wait = self.last + self.interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()


THROTTLES = {
    "crossref": Throttle(0.05),
    "openalex": Throttle(0.11),
    "arxiv": Throttle(3.1),
    "s2": Throttle(1.1),
}
_host_throttles, _host_lock = {}, threading.Lock()


def throttle_for(key):
    """Named provider throttle, or a 0.6 s per-host throttle for any other key."""
    if key in THROTTLES:
        return THROTTLES[key]
    with _host_lock:
        return _host_throttles.setdefault(key, Throttle(0.6))


_fail_count, _fail_time, _fail_lock = defaultdict(int), defaultdict(float), threading.Lock()
DEAD_AFTER = 6
RESET_AFTER = 60.0


def http(url, key, method="GET", data=None, headers=None, timeout=30, retries=4,
         browser=False, max_bytes=40_000_000):
    """One throttled request with retry/backoff -> (status, body, final_url, headers).

    Status 0 means no usable answer (network error, or 429/5xx after all retries). A host that
    failed DEAD_AFTER times in a row is skipped (status 0) until RESET_AFTER seconds pass.
    """
    host = urllib.parse.urlparse(url).netloc
    with _fail_lock:
        if _fail_count[host] >= DEAD_AFTER:
            if time.time() - _fail_time[host] <= RESET_AFTER:
                return 0, b"", url, {"error": "host skipped after repeated network failures: " + host}
            _fail_count[host] = 0
    hdrs = {"User-Agent": BROWSER_UA if browser else UA, "Accept": "*/*"}
    hdrs.update(headers or {})
    last = ""
    for attempt in range(retries):
        throttle_for(key).wait()
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                with _fail_lock:
                    _fail_count[host] = 0
                return r.status, r.read(max_bytes), r.geturl(), dict(r.headers)
        except urllib.error.HTTPError as e:
            with _fail_lock:
                _fail_count[host] = 0
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if ra and ra.replace(".", "").isdigit() else 2 ** attempt * 3
                last = "HTTP %d" % e.code
                time.sleep(min(delay, 45))
                continue
            try:
                body = e.read()
            except Exception:
                body = b""
            return e.code, body, url, {}
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            with _fail_lock:
                _fail_count[host] += 1
                _fail_time[host] = time.time()
                dead = _fail_count[host] >= DEAD_AFTER
            if dead:
                break
            time.sleep(min(2 ** attempt, 4))
    return 0, b"", url, {"error": last}


def jget(url, key, strict=False, **kw):
    """GET -> parsed JSON, or None on a non-200 answer or unparsable body.

    Raises NetworkError when there was no answer at all; with strict=True also on any non-200.
    """
    st, body, _, meta = http(url, key, **kw)
    if st == 0 or (strict and st != 200):
        raise NetworkError(meta.get("error") or "HTTP %d from %s" % (st, url))
    if st != 200:
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def qdoi(doi):
    return urllib.parse.quote(doi, safe="/()")


def title_sim(a, b):
    """Similarity (0-1) of two titles after normalisation."""
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


_TAG = re.compile(r"</?(?:[\w-]+:)?([\w-]+)[^>]*>")
_BLOCK = {"p", "title", "sec", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}


def clean_abstract(s):
    """Strip markup, unescape entities, collapse whitespace, drop a leading 'Abstract'."""
    s = _TAG.sub(lambda m: " " if m.group(1).lower() in _BLOCK else "", s or "")
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return re.sub(r"^abstract\b[\s:.\u2013\u2014-]*", "", s, flags=re.IGNORECASE)


def oa_abstract(inv):
    """Rebuild abstract text from an OpenAlex inverted index {word: [positions]}."""
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos))


def _result(abstract="", pdf=None, arxiv_id=None):
    """Provider result dict, or None when there is nothing at all."""
    if not (abstract or pdf or arxiv_id):
        return None
    return {"abstract": abstract, "pdf": pdf or None, "arxiv": arxiv_id or None}


def _bare_doi(d):
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d or "", flags=re.IGNORECASE).lower()


_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?$")


def _oa_record(w):
    """OpenAlex work -> provider result."""
    pdf = None
    for loc in [w.get("best_oa_location") or {}] + (w.get("locations") or []):
        if loc.get("pdf_url"):
            pdf = loc["pdf_url"]
            break
    arx = None
    for loc in w.get("locations") or []:
        m = _ARXIV_URL.search(loc.get("landing_page_url") or "")
        if m:
            arx = m.group(1)
            break
    return _result(clean_abstract(oa_abstract(w.get("abstract_inverted_index"))), pdf, arx)


def _mailto():
    return "&mailto=" + urllib.parse.quote(MAILTO) if MAILTO else ""


def openalex_batch(dois):
    """OpenAlex lookup, 50 DOIs per request -> {lower-case doi: result}. Raises NetworkError."""
    out = {}
    dois = [d for d in dois if "," not in d and "|" not in d]
    for i in range(0, len(dois), OA_BATCH):
        filt = "doi:" + "|".join(dois[i:i + OA_BATCH])
        j = jget("%s/works?filter=%s&per-page=%d&select=%s%s" % (
            URL_OPENALEX, urllib.parse.quote(filt), OA_BATCH, OA_SELECT, _mailto()),
            "openalex", strict=True)
        for w in (j or {}).get("results") or []:
            rec, d = _oa_record(w), _bare_doi(w.get("doi"))
            if rec and d:
                out[d] = rec
    return out


def s2_batch(dois):
    """Semantic Scholar lookup, 100 DOIs per request -> {lower-case doi: result}. Raises NetworkError."""
    out = {}
    hdr = {"Content-Type": "application/json"}
    if os.environ.get("S2_API_KEY"):
        hdr["x-api-key"] = os.environ["S2_API_KEY"]
    for i in range(0, len(dois), S2_BATCH):
        chunk = dois[i:i + S2_BATCH]
        st, resp, _, meta = http(
            "%s/graph/v1/paper/batch?fields=%s" % (URL_S2, S2_FIELDS), "s2", method="POST",
            data=json.dumps({"ids": ["DOI:" + d for d in chunk]}).encode(), headers=hdr, retries=6)
        try:
            arr = json.loads(resp.decode("utf-8", errors="replace")) if st == 200 else None
        except ValueError:
            arr = None
        if not isinstance(arr, list):
            raise NetworkError(meta.get("error") or "S2 batch failed (HTTP %d)" % st)
        for d, rec in zip(chunk, arr):
            if rec:
                r = _result(clean_abstract(rec.get("abstract")),
                            (rec.get("openAccessPdf") or {}).get("url"),
                            (rec.get("externalIds") or {}).get("ArXiv"))
                if r:
                    out[d.lower()] = r
    return out


def openalex_title(title):
    """OpenAlex title search; accepts only a hit with title similarity >= 0.93. Raises NetworkError."""
    j = jget("%s/works?filter=title.search:%s&per-page=3&select=%s%s" % (
        URL_OPENALEX, urllib.parse.quote(re.sub(r"[,:;|]", " ", title)), OA_SELECT, _mailto()),
        "openalex")
    for w in (j or {}).get("results") or []:
        if title_sim(w.get("title"), title) >= TITLE_MATCH:
            return _oa_record(w)
    return None


def crossref(doi=None, title=None):
    """Crossref lookup by DOI, else by title (similarity >= 0.93). Raises NetworkError."""
    mail = "mailto=" + urllib.parse.quote(MAILTO) if MAILTO else ""
    msg = None
    if doi:
        j = jget("%s/works/%s%s" % (URL_CROSSREF, qdoi(doi), "?" + mail if mail else ""), "crossref")
        msg = (j or {}).get("message")
    elif title:
        j = jget("%s/works?query.bibliographic=%s&rows=3%s" % (
            URL_CROSSREF, urllib.parse.quote(title), "&" + mail if mail else ""), "crossref")
        for it in (j or {}).get("message", {}).get("items") or []:
            if title_sim((it.get("title") or [""])[0], title) >= TITLE_MATCH:
                msg = it
                break
    if not msg:
        return None
    pdf = next((link["URL"] for link in msg.get("link") or []
                if "pdf" in (link.get("content-type") or "") and link.get("URL")), None)
    return _result(clean_abstract(msg.get("abstract")), pdf)


class _MetaParser(HTMLParser):
    """Collects <meta name|property=... content=...> values and JSON-LD blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.ld, self._in_ld, self._buf = defaultdict(list), [], False, []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "meta" and (d.get("name") or d.get("property")) and d.get("content"):
            self.meta[(d.get("name") or d.get("property")).lower()].append(d["content"])
        if tag == "script" and (d.get("type") or "").lower() == "application/ld+json":
            self._in_ld, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            try:
                self.ld.append(json.loads("".join(self._buf)))
            except ValueError:
                pass

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)


_ABSTRACT_META = ("citation_abstract", "dc.description", "dc.description.abstract", "description",
                  "og:description", "twitter:description", "dcterms.abstract", "eprints.abstract")
_BOILERPLATE = re.compile(r"cookie|javascript is (disabled|required)", re.IGNORECASE)


def parse_landing(page, base_url=""):
    """Landing-page HTML -> provider result (longest plausible meta / JSON-LD abstract)."""
    mp = _MetaParser()
    try:
        mp.feed(page)
    except Exception:
        pass
    cands = [clean_abstract(v) for k in _ABSTRACT_META for v in mp.meta.get(k, [])]
    for blob in mp.ld:
        for node in blob if isinstance(blob, list) else [blob]:
            if isinstance(node, dict):
                cands += [clean_abstract(node[k]) for k in ("abstract", "description")
                          if isinstance(node.get(k), str)]
    cands = [c for c in cands if len(c) >= MIN_ABSTRACT and not _BOILERPLATE.search(c)]
    pdf = (mp.meta.get("citation_pdf_url") or [None])[0]
    arx = (mp.meta.get("citation_arxiv_id") or [None])[0]
    if not arx:
        m = _ARXIV_URL.search(base_url)
        arx = m.group(1) if m else None
    return _result(max(cands, key=len) if cands else "",
                   urllib.parse.urljoin(base_url, pdf) if pdf else None, arx)


def landing(url):
    """Fetch a landing page and extract its abstract + citation_pdf_url. Raises NetworkError."""
    if not url:
        return None
    st, body, final, meta = http(url, urllib.parse.urlparse(url).netloc or "landing",
                                 browser=True, retries=2, timeout=25)
    if st == 0:
        raise NetworkError(meta.get("error") or "no answer from " + url)
    if st != 200 or not body:
        return None
    if body[:5] == b"%PDF-":
        return _result(pdf=final)
    return parse_landing(body.decode("utf-8", errors="replace"), final)


def arxiv(title):
    """arXiv title search (ti:"..."); accepts only similarity >= 0.93. Raises NetworkError."""
    q = urllib.parse.quote('ti:"%s"' % re.sub(r"[\"']", " ", title))
    st, body, _, meta = http("%s?search_query=%s&max_results=3" % (URL_ARXIV, q), "arxiv", retries=3)
    if st == 0:
        raise NetworkError(meta.get("error") or "no answer from arXiv")
    if st != 200:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall("a:entry", ns):
        if title_sim(e.findtext("a:title", "", ns), title) >= TITLE_MATCH:
            aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
            return _result(clean_abstract(e.findtext("a:summary", "", ns)),
                           "https://arxiv.org/pdf/" + aid, aid)
    return None
