"""e_fetch: fetch passes, bit marking, resumability. Providers are faked; no network."""

import pytest

import b_db
import d_network as net
import e_fetch

OA, S2, CR, LD, AX = (b_db.TRIED_OPENALEX, b_db.TRIED_S2, b_db.TRIED_CROSSREF,
                      b_db.TRIED_LANDING, b_db.TRIED_ARXIV)


def text(tag):
    return ("%s: an LLM agent autonomously plans and executes multi-stage attacks against "
            "multi-host networks, evaluated against several strong baselines." % tag)


def rec(tag=None, pdf=None, arxiv=None, short=False):
    return {"abstract": ("too short" if short else text(tag)) if tag else "", "pdf": pdf, "arxiv": arxiv}


def doi(pid):
    return "10.1000/test.%d" % pid


def state(conn, pid):
    return b_db.get_fetch_state(conn, pid)


def abstract(conn, pid):
    a = b_db.get_abstract(conn, pid)
    return a and a["src"]


@pytest.fixture
def quiet():
    msgs = []
    return msgs, msgs.append


def run(conn, quiet, **kw):
    kw.setdefault("workers", 1)
    return e_fetch.run_fetch(conn, progress=quiet[1], **kw)


# --------------------------------------------------------------------------- pass 1

@pytest.fixture
def providers(monkeypatch):
    """Fake OpenAlex/S2: set `.oa` / `.s2` dicts (doi -> rec); calls are recorded."""

    class P:
        oa, s2, oa_calls, s2_calls = {}, {}, [], []
        oa_fail = s2_fail = False

    p = P()
    p.oa, p.s2, p.oa_calls, p.s2_calls = {}, {}, [], []

    def oa(dois):
        p.oa_calls.append(list(dois))
        if p.oa_fail:
            raise net.NetworkError("down")
        return {d: p.oa[d] for d in dois if d in p.oa}

    def s2(dois):
        p.s2_calls.append(list(dois))
        if p.s2_fail:
            raise net.NetworkError("down")
        return {d: p.s2[d] for d in dois if d in p.s2}

    monkeypatch.setattr(net, "openalex_batch", oa)
    monkeypatch.setattr(net, "s2_batch", s2)
    return p


def test_pass1_pipeline_marks_and_handles(tmp_db, providers, quiet):
    providers.oa = {doi(2): rec("oa2", pdf="https://oa/2.pdf"),
                    doi(5): rec(None, pdf="https://oa/5.pdf", arxiv="2401.5")}
    providers.s2 = {doi(4): rec("s24", arxiv="2402.4")}
    out = run(tmp_db, quiet)

    assert out["candidates"] == 3 and out["abstracts_found"] == 2
    assert out["by_src"] == {"openalex": 1, "s2": 1} and not out["aborted"]
    assert providers.s2_calls == [[doi(4), doi(5)]]          # only DOIs OpenAlex could not resolve
    assert abstract(tmp_db, 2) == "openalex" and abstract(tmp_db, 4) == "s2" and abstract(tmp_db, 5) is None
    assert abstract(tmp_db, 1) == "openalex" and b_db.get_abstract(tmp_db, 1)["text"].startswith("Security")
    assert state(tmp_db, 2) == {"tried": OA, "pdf": "https://oa/2.pdf", "arxiv": None}
    assert state(tmp_db, 4) == {"tried": OA | S2, "pdf": None, "arxiv": "2402.4"}
    assert state(tmp_db, 5) == {"tried": OA | S2, "pdf": "https://oa/5.pdf", "arxiv": "2401.5"}
    assert state(tmp_db, 1)["tried"] == 0 and state(tmp_db, 3)["tried"] == 0   # not candidates


def test_pass1_negative_cache(tmp_db, providers, quiet):
    run(tmp_db, quiet)
    assert providers.oa_calls and providers.s2_calls
    before = (len(providers.oa_calls), len(providers.s2_calls))
    out = run(tmp_db, quiet)
    assert out["candidates"] == 0 and out["abstracts_found"] == 0
    assert (len(providers.oa_calls), len(providers.s2_calls)) == before
    assert all(state(tmp_db, i)["tried"] == OA | S2 for i in (2, 4, 5))


def test_pass1_min_abstract_length(tmp_db, providers, quiet):
    providers.oa = {doi(2): rec("x", short=True)}
    providers.s2 = {doi(2): rec("x", short=True)}
    out = run(tmp_db, quiet)
    assert out["abstracts_found"] == 0 and abstract(tmp_db, 2) is None
    assert providers.s2_calls and doi(2) in providers.s2_calls[0]      # short OA text -> still goes to S2
    assert state(tmp_db, 2)["tried"] == OA | S2
    assert b_db.MIN_ABSTRACT == 120 and len(text("x")) >= 120


def test_pass1_failed_openalex_batch_not_marked(tmp_db, providers, quiet):
    providers.oa_fail = True
    out = run(tmp_db, quiet)
    assert out["abstracts_found"] == 0 and providers.s2_calls == []
    assert all(state(tmp_db, i)["tried"] == 0 for i in (2, 4, 5))
    providers.oa_fail = False
    providers.oa = {doi(2): rec("oa2")}
    assert run(tmp_db, quiet)["abstracts_found"] == 1               # a re-run picks them up again


def test_pass1_failed_s2_batch_not_marked(tmp_db, providers, quiet):
    providers.oa = {doi(2): rec("oa2"), doi(4): rec(None, pdf="https://oa/4.pdf")}
    providers.s2_fail = True
    run(tmp_db, quiet)
    assert state(tmp_db, 2)["tried"] == OA                          # satisfied by OpenAlex
    assert state(tmp_db, 4) == {"tried": 0, "pdf": "https://oa/4.pdf", "arxiv": None}   # handle kept
    assert state(tmp_db, 5)["tried"] == 0
    providers.s2_fail = False
    providers.s2 = {doi(4): rec("s24")}
    assert run(tmp_db, quiet)["by_src"] == {"s2": 1}
    assert state(tmp_db, 4)["pdf"] == "https://oa/4.pdf"


def test_pass1_batch_sizes_and_overlap(tmp_db, providers, quiet, monkeypatch):
    monkeypatch.setattr(net, "OA_BATCH", 1)
    monkeypatch.setattr(net, "S2_BATCH", 2)
    run(tmp_db, quiet, workers=3)
    assert len(providers.oa_calls) == 3
    assert sorted(len(c) for c in providers.s2_calls) == [1, 2]
    assert sorted(d for c in providers.s2_calls for d in c) == [doi(2), doi(4), doi(5)]


def test_pass1_network_probe_aborts(tmp_db, providers, quiet, monkeypatch):
    for pid in range(10, 20):
        tmp_db.execute("INSERT INTO paper(id,title,doi) VALUES(?,?,?)", (pid, "Paper %d" % pid, doi(pid)))
    providers.oa_fail = True
    monkeypatch.setattr(net, "OA_BATCH", 1)
    out = run(tmp_db, quiet)
    assert out["aborted"] and len(providers.oa_calls) == e_fetch.PROBE_N
    assert any("ERROR" in m and "probe" in m for m in quiet[0])
    assert tmp_db.execute("SELECT COUNT(*) FROM fetch_state").fetchone()[0] == 0


def test_pass1_interrupted_run_keeps_committed_chunks(tmp_db, providers, quiet, monkeypatch):
    monkeypatch.setattr(net, "OA_BATCH", 1)
    calls = []

    def oa(dois):
        calls.append(dois)
        if len(calls) == 2:
            raise RuntimeError("interrupt")
        return {dois[0]: rec("oa")}

    monkeypatch.setattr(net, "openalex_batch", oa)
    with pytest.raises(RuntimeError):
        run(tmp_db, quiet)
    assert abstract(tmp_db, 2) == "openalex" and state(tmp_db, 2)["tried"] == OA
    assert state(tmp_db, 4)["tried"] == 0


def test_pass1_progress_reports(tmp_db, providers, quiet, monkeypatch):
    monkeypatch.setattr(e_fetch, "EVERY", 1)
    run(tmp_db, quiet)
    assert any(m.startswith("fetch: ") and "/3 papers" in m for m in quiet[0])


# --------------------------------------------------------------------------- pass 2

@pytest.fixture
def deep(tmp_db, monkeypatch):
    """Triage: paper 2 tier 2, paper 4 tier 1, paper 5 tier 0. Fake deep providers record calls."""
    b_db.upsert_triage(tmp_db, [(2, 2, "ai sec", None, 0, 1), (4, 1, "ai", None, 0, 1),
                                (5, 0, "", None, 0, 1)])

    class D:
        calls = []
        table = {}          # (provider, pid_key) -> rec | Exception
        default = None

    d = D()
    d.calls, d.table = [], {}

    def make(name):
        def fn(*a, **kw):
            key = (a[0] if a else kw.get("doi") or kw.get("title"))
            d.calls.append((name, key))
            r = d.table.get(name)
            if isinstance(r, Exception):
                raise r
            return r
        return fn

    for name in ("openalex_title", "crossref", "landing", "arxiv"):
        monkeypatch.setattr(net, name, make(name))
    return d


def names(deep):
    return [n for n, _ in deep.calls]


def test_deep_requires_triage(tmp_path, make_csv, quiet):
    db = tmp_path / "n.db"
    b_db.init_from_csv(make_csv(), db)
    conn = b_db.open_db(db)
    out = run(conn, quiet, deep=True)
    assert out["candidates"] == 0 and any("triage" in m for m in quiet[0])
    assert b_db.meta_get(conn, "deep_fetch_done") is None
    conn.close()


def test_deep_provider_order_and_arxiv_tier_gate(tmp_db, deep, quiet):
    out = run(tmp_db, quiet, deep=True)
    assert out["candidates"] == 2 and out["abstracts_found"] == 0
    assert sorted(names(deep)) == ["arxiv", "crossref", "crossref", "landing", "landing"]
    assert [k for n, k in deep.calls if n == "arxiv"] == [b_db.get_paper(tmp_db, 2)["title"]]   # tier 2 only
    assert sorted(k for n, k in deep.calls if n == "landing") == ["https://doi.org/" + doi(2),
                                                                   "https://doi.org/" + doi(4)]
    assert state(tmp_db, 2)["tried"] == CR | LD | AX
    assert state(tmp_db, 4)["tried"] == CR | LD
    assert state(tmp_db, 5)["tried"] == 0                            # tier 0 never fetched
    assert b_db.meta_get(tmp_db, "deep_fetch_done") == "1"


def test_deep_order_for_single_paper(tmp_db, deep, quiet):
    b_db.upsert_triage(tmp_db, [(4, 0, "", None, 0, 1)])             # only paper 2 left
    run(tmp_db, quiet, deep=True)
    assert deep.calls == [("crossref", doi(2)), ("landing", "https://doi.org/" + doi(2)),
                          ("arxiv", b_db.get_paper(tmp_db, 2)["title"])]


def test_deep_stops_at_first_usable_abstract(tmp_db, deep, quiet):
    b_db.upsert_triage(tmp_db, [(4, 0, "", None, 0, 1)])
    deep.table["landing"] = rec("land", pdf="https://pub/2.pdf")
    deep.table["crossref"] = rec("tiny", short=True, pdf="https://cr/2.pdf")
    out = run(tmp_db, quiet, deep=True)
    assert out["by_src"] == {"landing": 1} and names(deep) == ["crossref", "landing"]   # no arXiv call
    assert abstract(tmp_db, 2) == "landing"
    assert state(tmp_db, 2) == {"tried": CR | LD, "pdf": "https://cr/2.pdf", "arxiv": None}   # first handle wins


def test_deep_arxiv_result_and_handles(tmp_db, deep, quiet):
    b_db.upsert_triage(tmp_db, [(4, 0, "", None, 0, 1)])
    deep.table["arxiv"] = rec("ax", pdf="https://arxiv.org/pdf/2403.1", arxiv="2403.1")
    run(tmp_db, quiet, deep=True)
    assert abstract(tmp_db, 2) == "arxiv"
    assert state(tmp_db, 2) == {"tried": CR | LD | AX, "pdf": "https://arxiv.org/pdf/2403.1", "arxiv": "2403.1"}


def test_deep_no_doi_uses_openalex_title_and_link(tmp_db, deep, quiet):
    b_db.upsert_triage(tmp_db, [(2, 0, "", None, 0, 1)])             # only paper 4 left (tier 1)
    tmp_db.execute("UPDATE paper SET doi=NULL, link='https://pub.example/p4' WHERE id=4")
    run(tmp_db, quiet, deep=True)
    title = b_db.get_paper(tmp_db, 4)["title"]
    assert deep.calls == [("openalex_title", title), ("crossref", title), ("landing", "https://pub.example/p4")]
    assert state(tmp_db, 4)["tried"] == OA | CR | LD


def test_deep_network_error_not_marked(tmp_db, deep, quiet):
    b_db.upsert_triage(tmp_db, [(4, 0, "", None, 0, 1)])
    deep.table["crossref"] = net.NetworkError("down")
    run(tmp_db, quiet, deep=True)
    assert names(deep) == ["crossref", "landing", "arxiv"]           # chain continues past the failure
    assert state(tmp_db, 2)["tried"] == LD | AX                      # crossref bit NOT set
    deep.calls.clear()
    deep.table["crossref"] = rec("cr")
    out = run(tmp_db, quiet, deep=True)
    assert names(deep) == ["crossref"] and out["by_src"] == {"crossref": 1}


def test_deep_negative_cache_second_run(tmp_db, deep, quiet):
    run(tmp_db, quiet, deep=True)
    deep.calls.clear()
    out = run(tmp_db, quiet, deep=True)
    assert deep.calls == [] and out["candidates"] == 0
    assert b_db.meta_get(tmp_db, "deep_fetch_done") == "1"


def test_deep_flag_not_set_when_interrupted(tmp_db, deep, quiet):
    deep.table["landing"] = RuntimeError("interrupt")
    with pytest.raises(RuntimeError):
        run(tmp_db, quiet, deep=True)
    assert b_db.meta_get(tmp_db, "deep_fetch_done") is None


def test_deep_flag_not_set_when_probe_aborts(tmp_db, deep, quiet):
    for pid in range(10, 20):
        tmp_db.execute("INSERT INTO paper(id,title,doi) VALUES(?,?,?)", (pid, "Paper %d" % pid, doi(pid)))
    b_db.upsert_triage(tmp_db, [(pid, 2, "ai sec", None, 0, 1) for pid in range(10, 20)])
    for name in ("crossref", "landing", "arxiv"):
        deep.table[name] = net.NetworkError("down")
    out = run(tmp_db, quiet, deep=True)
    assert out["aborted"] and b_db.meta_get(tmp_db, "deep_fetch_done") is None
    assert any("ERROR" in m for m in quiet[0])
    assert tmp_db.execute("SELECT COUNT(*) FROM fetch_state WHERE tried != 0").fetchone()[0] == 0


def test_deep_threaded_run_matches(tmp_db, deep, quiet):
    deep.table["crossref"] = rec("cr")
    out = run(tmp_db, quiet, deep=True, workers=8)
    assert out["by_src"] == {"crossref": 2} and abstract(tmp_db, 2) == abstract(tmp_db, 4) == "crossref"
