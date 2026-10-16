"""d_network: providers, parsing, http dead-host logic. No network: `http` / urlopen are faked."""

import json
import urllib.error

import pytest

import d_network as net

LONG = ("We study autonomous agents that plan and execute multi-stage network attacks, and we "
        "evaluate them on realistic multi-host environments with several baselines. ") * 2


def fake_http(monkeypatch, handler):
    """Route net.http through handler(url, **kw) -> (status, body_bytes_or_obj); records URLs."""
    calls = []

    def _http(url, key, **kw):
        calls.append((url, kw))
        st, body = handler(url, **kw)
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        return st, body, url, {} if st else {"error": "boom"}

    monkeypatch.setattr(net, "http", _http)
    return calls


# --------------------------------------------------------------------------- text helpers

def test_oa_abstract_reconstruction():
    inv = {"agents": [1, 5], "Autonomous": [0], "attack": [2], "and": [4], "plan": [3]}
    assert net.oa_abstract(inv) == "Autonomous agents attack plan and agents"
    assert net.oa_abstract(None) == "" and net.oa_abstract({}) == ""


def test_clean_abstract_strips_markup_and_leading_label():
    raw = "<jats:title>Abstract</jats:title><jats:p>We study <jats:italic>LLM</jats:italic>s &amp; agents.</jats:p>"
    assert net.clean_abstract(raw) == "We study LLMs & agents."
    assert net.clean_abstract("Abstract: Text here") == "Text here"
    assert net.clean_abstract("Abstractions are useful") == "Abstractions are useful"
    assert net.clean_abstract(None) == ""


def test_title_sim():
    assert net.title_sim("Hello, World!", "hello world") == 1.0
    assert net.title_sim("", "x") == 0.0
    assert net.title_sim("deep sea fishing", "quantum chromodynamics") < 0.5


# --------------------------------------------------------------------------- landing page

LANDING_HTML = """<html><head>
<meta name="description" content="Cookies: we use cookies to improve your experience on this site, accept all of them please and continue browsing for a long while okay okay okay okay okay okay.">
<meta name="citation_pdf_url" content="/files/paper.pdf">
<meta name="citation_arxiv_id" content="2401.01234">
<meta property="og:description" content="Short.">
<script type="application/ld+json">{"@type": "ScholarlyArticle", "abstract": "%s"}</script>
</head><body></body></html>""" % LONG


def test_parse_landing_fixture():
    rec = net.parse_landing(LANDING_HTML, "https://pub.example.org/article/1")
    assert rec["abstract"] == net.clean_abstract(LONG)
    assert rec["pdf"] == "https://pub.example.org/files/paper.pdf"
    assert rec["arxiv"] == "2401.01234"


def test_parse_landing_meta_abstract_and_arxiv_from_url():
    html_ = '<meta name="citation_abstract" content="%s">' % LONG
    rec = net.parse_landing(html_, "https://arxiv.org/abs/2301.00001")
    assert rec["abstract"] and rec["arxiv"] == "2301.00001" and rec["pdf"] is None
    assert net.parse_landing("<html></html>", "https://x.org/") is None


def test_landing_statuses(monkeypatch):
    fake_http(monkeypatch, lambda url, **kw: (200, LANDING_HTML.encode()))
    assert net.landing("https://pub.example.org/a")["abstract"]
    fake_http(monkeypatch, lambda url, **kw: (200, b"%PDF-1.7 ..."))
    rec = net.landing("https://pub.example.org/a.pdf")
    assert rec == {"abstract": "", "pdf": "https://pub.example.org/a.pdf", "arxiv": None}
    fake_http(monkeypatch, lambda url, **kw: (404, b""))
    assert net.landing("https://pub.example.org/a") is None
    fake_http(monkeypatch, lambda url, **kw: (0, b""))
    with pytest.raises(net.NetworkError):
        net.landing("https://pub.example.org/a")
    assert net.landing("") is None


# --------------------------------------------------------------------------- OpenAlex

def oa_work(doi, text="alpha beta", pdf=None, arxiv_url=None):
    inv = {}
    for i, w in enumerate(text.split()):
        inv.setdefault(w, []).append(i)
    return {"doi": "https://doi.org/" + doi.upper(), "title": "t", "abstract_inverted_index": inv,
            "best_oa_location": {"pdf_url": pdf} if pdf else None,
            "locations": [{"landing_page_url": arxiv_url}] if arxiv_url else []}


def test_openalex_batch_chunks_keys_and_handles(monkeypatch):
    dois = ["10.1000/test.%d" % i for i in range(120)]

    def handler(url, **kw):
        assert "mailto" not in url and "cited_by" not in url
        return 200, {"results": [oa_work("10.1000/TEST.0", pdf="https://x/p.pdf",
                                         arxiv_url="https://arxiv.org/abs/2402.00001")]}

    calls = fake_http(monkeypatch, handler)
    out = net.openalex_batch(dois)
    assert len(calls) == 3                               # 50 + 50 + 20
    assert calls[0][0].count("%7C") == 49                # '|' separated filter
    assert out["10.1000/test.0"] == {"abstract": "alpha beta", "pdf": "https://x/p.pdf",
                                     "arxiv": "2402.00001"}


def test_openalex_batch_failure_raises(monkeypatch):
    fake_http(monkeypatch, lambda url, **kw: (0, b""))
    with pytest.raises(net.NetworkError):
        net.openalex_batch(["10.1000/a"])
    fake_http(monkeypatch, lambda url, **kw: (400, b"{}"))
    with pytest.raises(net.NetworkError):
        net.openalex_batch(["10.1000/a"])


def test_openalex_batch_skips_unfilterable_dois(monkeypatch):
    calls = fake_http(monkeypatch, lambda url, **kw: (200, {"results": []}))
    assert net.openalex_batch(["10.1000/a,b", "10.1000/c|d"]) == {}
    assert calls == []


def test_openalex_title_threshold(monkeypatch):
    work = oa_work("10.1/x", text=LONG)
    work["title"] = "Autonomous agents for network attacks"
    fake_http(monkeypatch, lambda url, **kw: (200, {"results": [work]}))
    assert net.openalex_title("Autonomous agents for network attacks")["abstract"]
    assert net.openalex_title("A completely different title about fish") is None


# --------------------------------------------------------------------------- S2

def test_s2_batch(monkeypatch):
    dois = ["10.1000/test.%d" % i for i in range(150)]

    def handler(url, **kw):
        ids = json.loads(kw["data"])["ids"]
        assert "tldr" not in url and "fields=title,abstract,externalIds,openAccessPdf" in url
        recs = [None] * len(ids)
        if ids[0] == "DOI:10.1000/test.0":
            recs[0] = {"abstract": "<p>%s</p>" % LONG, "externalIds": {"ArXiv": "2401.00002"},
                       "openAccessPdf": {"url": "https://s2/pdf"}}
            recs[1] = {"abstract": None, "externalIds": {}, "openAccessPdf": None}
        return 200, recs

    calls = fake_http(monkeypatch, handler)
    out = net.s2_batch(dois)
    assert [len(json.loads(kw["data"])["ids"]) for _, kw in calls] == [100, 50]
    assert set(out) == {"10.1000/test.0"}
    assert out["10.1000/test.0"]["arxiv"] == "2401.00002" and out["10.1000/test.0"]["pdf"] == "https://s2/pdf"


def test_s2_batch_failure_raises(monkeypatch):
    fake_http(monkeypatch, lambda url, **kw: (0, b""))
    with pytest.raises(net.NetworkError):
        net.s2_batch(["10.1000/a"])


# --------------------------------------------------------------------------- Crossref / arXiv

def test_crossref_by_doi_and_title(monkeypatch):
    msg = {"abstract": "<jats:p>%s</jats:p>" % LONG, "title": ["Some Title"],
           "link": [{"URL": "https://cr/p.pdf", "content-type": "application/pdf"}],
           "is-referenced-by-count": 9, "container-title": ["VENUE"]}
    fake_http(monkeypatch, lambda url, **kw: (200, {"message": msg}))
    rec = net.crossref(doi="10.1000/a")
    assert rec == {"abstract": net.clean_abstract(LONG), "pdf": "https://cr/p.pdf", "arxiv": None}
    fake_http(monkeypatch, lambda url, **kw: (200, {"message": {"items": [msg]}}))
    assert net.crossref(title="Some Title")["abstract"]
    assert net.crossref(title="Totally unrelated words here") is None
    assert net.crossref() is None
    fake_http(monkeypatch, lambda url, **kw: (404, b""))
    assert net.crossref(doi="10.1000/none") is None


ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2403.00007v1</id><title>Agents  for
 Attacks</title><summary>  An abstract
 spanning lines.  </summary></entry></feed>"""


def test_arxiv_title_match(monkeypatch):
    fake_http(monkeypatch, lambda url, **kw: (200, ATOM))
    rec = net.arxiv("Agents for Attacks")
    assert rec == {"abstract": "An abstract spanning lines.", "pdf": "https://arxiv.org/pdf/2403.00007v1",
                   "arxiv": "2403.00007v1"}
    assert net.arxiv("Something else entirely") is None
    fake_http(monkeypatch, lambda url, **kw: (0, b""))
    with pytest.raises(net.NetworkError):
        net.arxiv("Agents for Attacks")


def test_provider_throttles():
    assert [net.THROTTLES[k].interval for k in ("crossref", "openalex", "s2", "arxiv")] == [0.05, 0.11, 1.1, 3.1]
    assert net.throttle_for("some.host").interval == 0.6
    assert net.throttle_for("some.host") is net.throttle_for("some.host")


# --------------------------------------------------------------------------- http

@pytest.fixture
def fast_http(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    net._fail_count.clear()
    net._fail_time.clear()
    yield
    net._fail_count.clear()
    net._fail_time.clear()


def test_http_dead_host_is_skipped(monkeypatch, fast_http):
    attempts = []

    def urlopen(req, timeout=0):
        attempts.append(req.full_url)
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(net.urllib.request, "urlopen", urlopen)
    for _ in range(2):
        st, _, _, meta = net.http("http://dead.example/x", "dead.example")
        assert st == 0 and "error" in meta
    n = len(attempts)
    assert n == net.DEAD_AFTER
    st, _, _, meta = net.http("http://dead.example/y", "dead.example")
    assert st == 0 and "skipped" in meta["error"] and len(attempts) == n
    net.http("http://other.example/y", "other.example")   # other hosts are still attempted
    assert len(attempts) > n


def test_http_retries_then_succeeds(monkeypatch, fast_http):
    seq = []

    class Resp:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"ok"

        def geturl(self):
            return "http://h.example/"

    def urlopen(req, timeout=0):
        seq.append(1)
        if len(seq) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {"Retry-After": "0"}, None)
        return Resp()

    monkeypatch.setattr(net.urllib.request, "urlopen", urlopen)
    st, body, _, _ = net.http("http://h.example/", "h.example")
    assert (st, body, len(seq)) == (200, b"ok", 3)


def test_http_client_error_not_retried(monkeypatch, fast_http):
    seq = []

    def urlopen(req, timeout=0):
        seq.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setattr(net.urllib.request, "urlopen", urlopen)
    assert net.http("http://h.example/", "h.example")[0] == 404 and len(seq) == 1
    assert net.jget("http://h.example/", "h.example") is None
