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

from b_io import MIN_ABSTRACT, norm, strip_tags

UA = "paper-hunter/1.0 (literature screening; mailto:%s)" % os.environ.get(
    "PH_MAILTO", "unknown@example.org"
)
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

URL_CROSSREF = os.environ.get("PH_CROSSREF", "https://api.crossref.org").rstrip("/")
URL_OPENALEX = os.environ.get("PH_OPENALEX", "https://api.openalex.org").rstrip("/")
URL_S2 = os.environ.get("PH_S2", "https://api.semanticscholar.org").rstrip("/")
URL_ARXIV = os.environ.get("PH_ARXIV", "http://export.arxiv.org/api/query")
MAILTO = os.environ.get("PH_MAILTO", "")


class Throttle:
    def __init__(self, interval):
        self.interval, self.last, self.lock = interval, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            t = time.time()
            wait = self.last + self.interval - t
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
    if key in THROTTLES:
        return THROTTLES[key]
    with _host_lock:
        return _host_throttles.setdefault(key, Throttle(0.6))


_fail_count, _fail_lock = defaultdict(int), threading.Lock()
DEAD_AFTER = 6


def http(
    url,
    key,
    method="GET",
    data=None,
    headers=None,
    timeout=30,
    retries=4,
    browser=False,
    max_bytes=40_000_000,
):
    host = urllib.parse.urlparse(url).netloc
    if _fail_count[host] >= DEAD_AFTER:
        return (
            0,
            b"",
            url,
            {"error": "host skipped after repeated network failures: " + host},
        )
    hdrs = {"User-Agent": BROWSER_UA if browser else UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    last = ""
    for attempt in range(retries):
        throttle_for(key).wait()
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _fail_count[host] = 0
                return r.status, r.read(max_bytes), r.geturl(), dict(r.headers)
        except urllib.error.HTTPError as e:
            _fail_count[host] = 0
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = (
                    float(ra)
                    if ra and ra.replace(".", "").isdigit()
                    else 2**attempt * 3
                )
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
            if _fail_count[host] >= DEAD_AFTER:
                break
            time.sleep(min(2**attempt, 4))
    return 0, b"", url, {"error": last}


def jget(url, key, **kw):
    st, body, _, _ = http(url, key, **kw)
    if st != 200:
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def qdoi(doi):
    return urllib.parse.quote(doi, safe="/()")


def title_sim(a, b):
    import difflib

    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def p_crossref(doi=None, title=None, year=None):
    m_str = "&mailto=" + urllib.parse.quote(MAILTO) if MAILTO else ""
    msg = None
    if doi:
        j = jget(
            "%s/works/%s%s"
            % (URL_CROSSREF, qdoi(doi), "?" + m_str[1:] if m_str else ""),
            "crossref",
        )
        msg = j.get("message") if j else None
    elif title:
        j = jget(
            "%s/works?query.bibliographic=%s&rows=3%s"
            % (URL_CROSSREF, urllib.parse.quote(title), m_str),
            "crossref",
        )
        for it in (j or {}).get("message", {}).get("items") or []:
            if title_sim((it.get("title") or [""])[0], title) >= 0.93:
                msg = it
                break
    if not msg:
        return None
    dp = (
        msg.get("issued")
        or msg.get("published-online")
        or msg.get("published-print")
        or {}
    ).get("date-parts", [[None]])[0]
    rel = msg.get("relation") or {}
    pre = [
        x.get("id")
        for x in (rel.get("has-preprint") or rel.get("is-preprint-of") or [])
        if x.get("id")
    ]
    retracted = any(
        (u.get("type") or "").lower() in ("retraction", "withdrawal", "removal")
        for u in (msg.get("update-to") or [])
    )
    return {
        "abstract": strip_tags(msg.get("abstract", "")),
        "title": (msg.get("title") or [""])[0],
        "container": (msg.get("container-title") or [""])[0],
        "type": msg.get("type"),
        "date": "-".join(str(x) for x in dp if x) if dp and dp[0] else "",
        "subjects": msg.get("subject") or [],
        "publisher": msg.get("publisher"),
        "ref_count": msg.get("reference-count"),
        "cited_by": msg.get("is-referenced-by-count"),
        "preprint_ids": pre,
        "retracted": retracted,
        "doi": (msg.get("DOI") or "").lower(),
        "pdf_links": [
            link.get("URL")
            for link in (msg.get("link") or [])
            if "pdf" in (link.get("content-type") or "") and link.get("URL")
        ],
    }


OA_SELECT = (
    "id,doi,title,publication_year,type,abstract_inverted_index,"
    "primary_location,best_oa_location,locations,topics,keywords,"
    "cited_by_count,referenced_works_count,is_retracted,ids"
)


def oa_abstract(inv):
    if not inv:
        return ""
    pos = []
    for w, idxs in inv.items():
        for i in idxs:
            pos.append((i, w))
    return " ".join(w for _, w in sorted(pos))


def p_openalex(doi=None, title=None, year=None):
    m_str = "&mailto=" + urllib.parse.quote(MAILTO) if MAILTO else ""
    w = None
    if doi:
        w = jget(
            "%s/works/doi:%s?select=%s%s" % (URL_OPENALEX, qdoi(doi), OA_SELECT, m_str),
            "openalex",
        )
    elif title:
        j = jget(
            "%s/works?filter=title.search:%s&per-page=3&select=%s%s"
            % (
                URL_OPENALEX,
                urllib.parse.quote(re.sub(r"[,:;|]", " ", title)),
                OA_SELECT,
                m_str,
            ),
            "openalex",
        )
        for it in (j or {}).get("results") or []:
            if title_sim(it.get("title"), title) >= 0.93 and (
                not year or abs((it.get("publication_year") or year) - year) <= 1
            ):
                w = it
                break
    if not w:
        return None
    pl = w.get("primary_location") or {}
    pdfs = []
    for loc in [w.get("best_oa_location") or {}] + (w.get("locations") or []):
        if loc.get("pdf_url") and loc["pdf_url"] not in pdfs:
            pdfs.append(loc["pdf_url"])
    arx = ""
    for loc in w.get("locations") or []:
        lu = loc.get("landing_page_url") or ""
        mm = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?$", lu)
        if mm:
            arx = mm.group(1)
            break
    return {
        "abstract": oa_abstract(w.get("abstract_inverted_index")),
        "title": w.get("title"),
        "container": ((pl.get("source") or {}).get("display_name")) or "",
        "type": w.get("type"),
        "topics": [t.get("display_name") for t in (w.get("topics") or [])][:6],
        "keywords": [k.get("display_name") for k in (w.get("keywords") or [])][:12],
        "cited_by": w.get("cited_by_count"),
        "ref_count": w.get("referenced_works_count"),
        "retracted": bool(w.get("is_retracted")),
        "pdf_links": pdfs,
        "arxiv_id": arx,
        "openalex_id": w.get("id"),
        "doi": (w.get("doi") or "").replace("https://doi.org/", "").lower(),
    }


S2_FIELDS = (
    "title,abstract,venue,year,externalIds,openAccessPdf,"
    "tldr,citationCount,publicationTypes,publicationVenue"
)


def s2_batch(dois):
    out = {}
    hdr = {"Content-Type": "application/json"}
    if os.environ.get("S2_API_KEY"):
        hdr["x-api-key"] = os.environ["S2_API_KEY"]
    for i in range(0, len(dois), 100):
        chunk = dois[i : i + 100]
        body = json.dumps({"ids": ["DOI:" + d for d in chunk]}).encode()
        st, resp, _, _ = http(
            "%s/graph/v1/paper/batch?fields=%s" % (URL_S2, S2_FIELDS),
            "s2",
            method="POST",
            data=body,
            headers=hdr,
            retries=6,
        )
        if st != 200:
            continue
        try:
            arr = json.loads(resp.decode("utf-8", errors="replace"))
        except Exception:
            continue
        for d, rec in zip(chunk, arr):
            if rec:
                ext = rec.get("externalIds") or {}
                out[d] = {
                    "abstract": rec.get("abstract") or "",
                    "tldr": ((rec.get("tldr") or {}).get("text")) or "",
                    "arxiv_id": ext.get("ArXiv") or "",
                    "venue": rec.get("venue") or "",
                    "pub_types": rec.get("publicationTypes") or [],
                    "pdf": ((rec.get("openAccessPdf") or {}).get("url")) or "",
                    "cited_by": rec.get("citationCount"),
                    "s2_id": rec.get("paperId"),
                }
    return out


def openalex_batch(dois):
    out = {}
    m_str = "&mailto=" + urllib.parse.quote(MAILTO or "research-ph@gmail.com")
    chunk_size = 50
    for i in range(0, len(dois), chunk_size):
        chunk = dois[i : i + chunk_size]
        filt = "doi:" + "|".join(chunk)
        url = "%s/works?filter=%s&per-page=50&select=%s%s" % (
            URL_OPENALEX,
            urllib.parse.quote(filt),
            OA_SELECT,
            m_str,
        )
        j = jget(url, "openalex")
        if not j:
            continue
        for w in j.get("results") or []:
            d = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if not d:
                continue
            pl = w.get("primary_location") or {}
            pdfs = []
            for loc in [w.get("best_oa_location") or {}] + (w.get("locations") or []):
                if loc.get("pdf_url") and loc["pdf_url"] not in pdfs:
                    pdfs.append(loc["pdf_url"])
            arx = ""
            for loc in w.get("locations") or []:
                lu = loc.get("landing_page_url") or ""
                mm = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?$", lu)
                if mm:
                    arx = mm.group(1)
                    break
            out[d] = {
                "abstract": oa_abstract(w.get("abstract_inverted_index")),
                "title": w.get("title"),
                "container": ((pl.get("source") or {}).get("display_name")) or "",
                "type": w.get("type"),
                "topics": [t.get("display_name") for t in (w.get("topics") or [])][:6],
                "keywords": [k.get("display_name") for k in (w.get("keywords") or [])][
                    :12
                ],
                "cited_by": w.get("cited_by_count"),
                "ref_count": w.get("referenced_works_count"),
                "retracted": bool(w.get("is_retracted")),
                "pdf_links": pdfs,
                "arxiv_id": arx,
                "openalex_id": w.get("id"),
                "doi": d,
            }
    return out


class _MetaParser(HTMLParser):
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
            except Exception:
                pass

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)


def p_landing(url):
    if not url:
        return None
    st, body, final, _ = http(
        url,
        urllib.parse.urlparse(url).netloc or "landing",
        browser=True,
        retries=2,
        timeout=25,
    )
    if st != 200 or not body:
        return None
    if body[:5] == b"%PDF-":
        return {
            "pdf_links": [final],
            "abstract": "",
            "keywords": [],
            "final_url": final,
        }
    mp = _MetaParser()
    try:
        mp.feed(body.decode("utf-8", errors="replace"))
    except Exception:
        pass
    m = mp.meta
    cands = []
    for k in (
        "citation_abstract",
        "dc.description",
        "dc.description.abstract",
        "description",
        "og:description",
        "twitter:description",
        "dcterms.abstract",
        "eprints.abstract",
    ):
        for v in m.get(k, []):
            cands.append(strip_tags(v))
    for blob in mp.ld:
        for node in blob if isinstance(blob, list) else [blob]:
            if isinstance(node, dict):
                for k in ("abstract", "description"):
                    if isinstance(node.get(k), str):
                        cands.append(strip_tags(node[k]))
    cands = [
        c
        for c in cands
        if len(c) >= MIN_ABSTRACT
        and not re.search(r"cookie|javascript is (disabled|required)", c, re.IGNORECASE)
    ]
    abstract = max(cands, key=len) if cands else ""
    kws = []
    for k in ("citation_keywords", "keywords", "dc.subject"):
        for v in m.get(k, []):
            kws += [x.strip() for x in re.split(r"[;,|]", v) if x.strip()]
    pdfs = list(dict.fromkeys(m.get("citation_pdf_url", [])))
    return {
        "abstract": abstract,
        "keywords": kws[:15],
        "pdf_links": pdfs,
        "final_url": final,
        "citation_doi": (m.get("citation_doi") or [""])[0],
    }


def p_arxiv(title):
    q = urllib.parse.quote('ti:"%s"' % re.sub(r'["\'"]', " ", title))
    st, body, _, _ = http(
        "%s?search_query=%s&max_results=3" % (URL_ARXIV, q), "arxiv", retries=3
    )
    if st != 200:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall("a:entry", ns):
        t = re.sub(r"\s+", " ", (e.findtext("a:title", "", ns) or "")).strip()
        if title_sim(t, title) >= 0.93:
            aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
            return {
                "arxiv_id": aid,
                "title": t,
                "abstract": re.sub(
                    r"\s+", " ", e.findtext("a:summary", "", ns) or ""
                ).strip(),
                "published": (e.findtext("a:published", "", ns) or "")[:10],
                "pdf_links": ["https://arxiv.org/pdf/" + aid],
            }
    return None
