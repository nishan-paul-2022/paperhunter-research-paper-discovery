"""Best-effort metadata/abstract enrichment: Crossref, OpenAlex, Semantic Scholar, arXiv.
Offline-safe: if the network is unreachable it stops early and writes enrich_todo.jsonl so the
agent fetches abstracts with its own web tools. Results are cached in enrich.jsonl (append-only)."""
import json, re, os, time, html, urllib.request, urllib.parse, urllib.error, difflib, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from . import io

MAILTO = os.environ.get("PH_MAILTO", "paper-hunter@example.org")
UA = f"paper-hunter/2.0 (mailto:{MAILTO})"
S2_KEY = os.environ.get("S2_API_KEY", "")

def http(url, timeout=20, retries=2, accept="application/json"):
    """-> (text|None, err|None); err in {'net','http:<code>'}"""
    hdr = {"User-Agent": UA, "Accept": accept}
    if S2_KEY and "semanticscholar" in url: hdr["x-api-key"] = S2_KEY
    last = "net"
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            last = f"http:{e.code}"
            if e.code in (429, 500, 502, 503): time.sleep(2 * (i + 1)); continue
            return None, last
        except Exception:
            last = "net"; time.sleep(1 + i)
    return None, last

def jget(url, **kw):
    t, e = http(url, **kw)
    if t is None: return None, e
    try: return json.loads(t), None
    except ValueError: return None, "badjson"

def strip_markup(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(abstract|summary)[:.\s]+", "", s, flags=re.I)

def tmatch(a, b):
    return difflib.SequenceMatcher(None, io.norm_title(a), io.norm_title(b)).ratio()

def inv_index(ii):
    if not ii: return ""
    pos = {}
    for w, ps in ii.items():
        for p in ps: pos[p] = w
    return " ".join(pos[k] for k in sorted(pos))

def crossref(p):
    if p["doi"]:
        j, e = jget("https://api.crossref.org/works/" + urllib.parse.quote(p["doi"]) + f"?mailto={MAILTO}")
        m = (j or {}).get("message")
    else:
        j, e = jget("https://api.crossref.org/works?rows=3&query.bibliographic=" + urllib.parse.quote(p["title"]) + f"&mailto={MAILTO}")
        m = None
        for it in ((j or {}).get("message", {}).get("items") or []):
            if tmatch(p["title"], (it.get("title") or [""])[0]) >= 0.9: m = it; break
    if not m: return {}, e
    dp = (m.get("issued") or {}).get("date-parts") or [[None]]
    return {"abstract": strip_markup(m.get("abstract", "")), "title": (m.get("title") or [""])[0],
            "venue": (m.get("container-title") or [""])[0], "type": m.get("type", ""),
            "doi": m.get("DOI", ""), "published": "-".join(str(x) for x in dp[0] if x),
            "issn": ",".join(m.get("ISSN") or []), "publisher": m.get("publisher", "")}, None

def openalex(p):
    if p["doi"]:
        j, e = jget("https://api.openalex.org/works/doi:" + urllib.parse.quote(p["doi"]) + f"?mailto={MAILTO}")
    else:
        r, e = jget("https://api.openalex.org/works?per-page=3&search=" + urllib.parse.quote(p["title"]) + f"&mailto={MAILTO}")
        j = next((x for x in (r or {}).get("results", []) if tmatch(p["title"], x.get("title") or "") >= 0.9), None)
    if not j: return {}, e
    loc = j.get("primary_location") or {}
    src = loc.get("source") or {}
    return {"abstract": inv_index(j.get("abstract_inverted_index")), "title": j.get("title") or "",
            "venue": src.get("display_name") or "", "type": j.get("type") or "",
            "doi": (j.get("doi") or "").replace("https://doi.org/", ""),
            "published": j.get("publication_date") or "", "is_oa": (j.get("open_access") or {}).get("is_oa"),
            "pdf_url": loc.get("pdf_url") or "", "landing": loc.get("landing_page_url") or ""}, None

def s2(p):
    f = "title,abstract,venue,year,externalIds,publicationTypes,tldr,openAccessPdf"
    if p["doi"]:
        j, e = jget(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(p['doi'])}?fields={f}")
    else:
        r, e = jget("https://api.semanticscholar.org/graph/v1/paper/search/match?fields=" + f + "&query=" + urllib.parse.quote(p["title"]))
        j = next((x for x in (r or {}).get("data", []) if tmatch(p["title"], x.get("title") or "") >= 0.9), None)
    time.sleep(0.4 if not S2_KEY else 0.05)
    if not j or j.get("error"): return {}, e
    ax = (j.get("externalIds") or {}).get("ArXiv")
    return {"abstract": j.get("abstract") or "", "title": j.get("title") or "", "venue": j.get("venue") or "",
            "types": j.get("publicationTypes") or [], "tldr": (j.get("tldr") or {}).get("text", ""),
            "arxiv": f"https://arxiv.org/abs/{ax}" if ax else "",
            "pdf_url": (j.get("openAccessPdf") or {}).get("url", "")}, None

def arxiv(p):
    q = urllib.parse.quote('ti:"' + re.sub(r'[^A-Za-z0-9 ]+', ' ', p["title"]) + '"')
    t, e = http(f"http://export.arxiv.org/api/query?search_query={q}&max_results=3", accept="application/atom+xml")
    if not t: return {}, e
    try: root = ET.fromstring(t)
    except ET.ParseError: return {}, "badxml"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for en in root.findall("a:entry", ns):
        ti = re.sub(r"\s+", " ", (en.findtext("a:title", "", ns) or "")).strip()
        if tmatch(p["title"], ti) >= 0.92:
            return {"arxiv": (en.findtext("a:id", "", ns) or "").replace("http://", "https://"),
                    "abstract": re.sub(r"\s+", " ", en.findtext("a:summary", "", ns) or "").strip(), "title": ti}, None
    return {}, None

def enrich_one(p, deep):
    r = {"id": p["id"], "status": "none", "abstract": "", "abstract_source": "", "arxiv_url": "", "pdf_url": "",
         "resolved_venue": "", "resolved_type": "", "published": "", "title_match": None, "tldr": "",
         "errors": [], "deep": deep, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    steps = [("crossref", crossref), ("openalex", openalex)]
    got = {}
    for name, fn in steps:
        d, e = fn(p); got[name] = d
        if e: r["errors"].append(f"{name}:{e}")
    need_s2 = deep or not any(g.get("abstract") for g in got.values())
    if need_s2:
        d, e = s2(p); got["s2"] = d
        if e: r["errors"].append(f"s2:{e}")
    for name in ("crossref", "openalex", "s2"):
        g = got.get(name) or {}
        if g.get("abstract") and len(g["abstract"]) > 60 and not r["abstract"]:
            r["abstract"], r["abstract_source"] = g["abstract"], name
        r["resolved_venue"] = r["resolved_venue"] or g.get("venue", "")
        r["resolved_type"] = r["resolved_type"] or g.get("type", "")
        r["published"] = r["published"] or g.get("published", "")
        r["pdf_url"] = r["pdf_url"] or g.get("pdf_url", "")
        r["tldr"] = r["tldr"] or g.get("tldr", "")
        r["arxiv_url"] = r["arxiv_url"] or g.get("arxiv", "")
        if g.get("title") and r["title_match"] is None: r["title_match"] = round(tmatch(p["title"], g["title"]), 2)
    if deep or not r["abstract"]:
        d, e = arxiv(p)
        if e: r["errors"].append(f"arxiv:{e}")
        if d.get("arxiv"):
            r["arxiv_url"] = r["arxiv_url"] or d["arxiv"]
            if not r["abstract"] and d.get("abstract"): r["abstract"], r["abstract_source"] = d["abstract"], "arxiv"
    if r["abstract"]: r["status"] = "ok"
    elif r["errors"] and all(x.split(":", 1)[1] == "net" for x in r["errors"]): r["status"] = "offline"
    return r

def run(papers, run_dir, signals_by_id=None, retry=False, refresh_deep=False, all_deep=False, workers=4):
    path = run_dir / "enrich.jsonl"
    cache = io.latest_by_id(io.read_jsonl(path))
    todo = []
    for p in papers:
        lane = (signals_by_id or {}).get(p["id"], {}).get("lane")
        deep = all_deep or lane in ("A", "B")
        c = cache.get(p["id"])
        if c is None: todo.append((p, deep))
        elif retry and c["status"] != "ok": todo.append((p, deep))
        elif refresh_deep and deep and not c.get("deep"): todo.append((p, deep))
    print(f"enrich: {len(todo)} to fetch ({len(cache)} cached)")
    probe = todo[:6]; offline = False
    if probe:
        res = [enrich_one(p, d) for p, d in probe]
        for r in res: io.append_jsonl(path, r)
        offline = all(r["status"] == "offline" for r in res)
        todo = todo[len(probe):]
    if offline:
        print("enrich: NETWORK UNREACHABLE from this shell -> skipping. The agent must obtain abstracts with its web tools "
              "(see enrich_todo.jsonl).")
        todo = []
    if todo:
        with ThreadPoolExecutor(workers) as ex:
            for n, r in enumerate(ex.map(lambda t: enrich_one(*t), todo), 1):
                io.append_jsonl(path, r)
                if n % 50 == 0: print(f"  ...{n}/{len(todo)}")
    cache = io.latest_by_id(io.read_jsonl(path))
    missing = [p["id"] for p in papers if not (cache.get(p["id"]) or {}).get("abstract")]
    sb = signals_by_id or {}
    missing.sort(key=lambda i: -(sb.get(i, {}).get("score", 0)))
    io.write_jsonl(run_dir / "enrich_todo.jsonl", [{"id": i, "reason": "no_abstract"} for i in missing])
    ok = len(papers) - len(missing)
    print(f"enrich: abstracts for {ok}/{len(papers)}; {len(missing)} need agent web fetch -> enrich_todo.jsonl")
    return {"with_abstract": ok, "missing": len(missing), "offline": offline}
