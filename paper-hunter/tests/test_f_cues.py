"""Tests for f_cues.py: cues, tier, Appendix B probes (T14), run_triage, speed (T12)."""

import re
import time
from pathlib import Path

import pytest

import b_db
import f_cues
from f_cues import cues, run_triage, tier
from g_prior import prior

HUNTER = Path(__file__).resolve().parent.parent

NEUTRAL = "Probe"

P1 = ("An LLM agent autonomously exploits web applications from a URL, performing recon, SQL injection "
      "and XSS through a ReAct loop with shell and HTTP tools.")
P2 = "An autonomous LLM agent with planning and tool use solves CTF tasks (pwn, web) via a shell loop."
P3 = "An LLM-driven agent compromises vulnerable hosts end to end, scanning to root shell, with no human input."
P4 = "GPT-4 based agents autonomously discover and exploit vulnerabilities in realistic networks."
P5 = ("We present an LLM multi-agent framework that autonomously performs penetration testing with "
      "privilege escalation across enterprise hosts.")
P6 = "Our LLM multi-agent framework hardens enterprise networks and takes penetration-testing reports as input."
P7 = "Red-teaming autonomous LLM agents: tool-use exploitation so the agent leaks data."
P8 = "LLM agents triage CVE reports and predict exploit likelihood; no attacks are executed."
P14 = "An LLM agent that advises human penetration testers and never executes anything."

# (probe number, title, abstract, min tier, max tier, prior predicate)
ANY = lambda p: True  # noqa: E731
ACCEPT = lambda p: p is not None and p.startswith("accept:")  # noqa: E731
PROBES = [
    (1, P1, "", 3, 3, lambda p: p == "accept:baseline"),
    (1, NEUTRAL, P1, 3, 3, lambda p: p == "accept:baseline"),
    (2, P2, "", 2, 3, lambda p: not (p or "").startswith("reject:")),
    (2, NEUTRAL, P2, 2, 3, lambda p: not (p or "").startswith("reject:")),
    (3, P3, "", 3, 3, ACCEPT),
    (3, NEUTRAL, P3, 3, 3, ACCEPT),
    (4, P4, "", 2, 3, lambda p: not (p or "").startswith("reject:")),
    (4, NEUTRAL, P4, 2, 3, lambda p: not (p or "").startswith("reject:")),
    (5, "A Defense Perspective on Autonomous Security Testing", P5, 2, 3,
     lambda p: p != "reject:defensive_only"),
    (6, "Hardening enterprise networks with an LLM multi-agent framework", P6, 2, 3, ANY),
    (7, P7, "", 2, 3, ANY),
    (7, NEUTRAL, P7, 2, 3, ANY),
    (8, P8, "", 2, 2, lambda p: not ACCEPT(p)),
    (8, NEUTRAL, P8, 2, 2, lambda p: not ACCEPT(p)),
    (9, "Exploiting Parallelism for DNN Accelerators", "", 0, 0, ANY),
    (10, "Resilient consensus of multi-agent systems under cyber attacks", "", 0, 1, ANY),
    (11, "Balancing exploration and exploitation in reinforcement learning", "", 0, 0, ANY),
    (12, "PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing",
     "", 3, 3, ANY),
    (13, "HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities",
     "", 2, 3, ANY),
    (14, P14, "", 2, 3, ANY),
    (14, NEUTRAL, P14, 2, 3, ANY),
    (15, "Jailbreaking LLM agents to perform harmful cyber tasks", "", 2, 2, lambda p: not ACCEPT(p)),
    (16, "Call for Papers: Workshop on Security and LLM Agents", "", 0, 0, ANY),
]


def run_one(title, abstract=""):
    c = cues(title, abstract)
    t = tier(c, bool(abstract))
    return c, t, prior(title, abstract, c, t)


@pytest.mark.parametrize("n,title,abstract,lo,hi,pred", PROBES,
                         ids=["p%d-%s" % (p[0], "title" if not p[2] else "abs") for p in PROBES])
def test_appendix_b_probes(n, title, abstract, lo, hi, pred):
    c, t, pr = run_one(title, abstract)
    assert lo <= t <= hi, (n, t, sorted(c))
    assert pred(pr), (n, pr, sorted(c))


def test_all_16_probes_covered():
    assert {p[0] for p in PROBES} == set(range(1, 17))


# --------------------------------------------------------------------------- cue vocabulary

def test_bare_words_are_not_ai():
    for text in ("Multi-agent reinforcement learning for autonomous drones",
                 "Agent-based modelling of traffic", "An autonomous vehicle controller",
                 "Reinforcement learning for robot control"):
        assert "ai" not in cues(text), text
        assert tier(cues(text)) <= 1


def test_ai_vocabulary_including_embedded_forms():
    for text in ("PentestGPT for scanning", "ChatGPT in the loop", "LLMs are useful", "An LLM-based tool",
                 "Large Language Models", "Claude and Gemini", "Llama 3 on Qwen", "DeepSeek-R1",
                 "foundation models", "Generative AI", "agentic workflows", "AI agents", "MCP servers",
                 "Computer-Use Agents", "Copilot", "PENTESTLLMAGENT: planning"):
        assert "ai" in cues(text), text
    assert "ai" not in cues("Diffie-Hellman key exchange")


def test_exploit_needs_security_context():
    assert "sec" not in cues("Exploiting Parallelism for DNN Accelerators")
    assert "off" not in cues("Exploiting Parallelism for DNN Accelerators")
    c = cues("Exploiting buffer overflow vulnerabilities")
    assert {"sec", "off"} <= c
    assert {"sec", "off"} <= cues("Exploiting a shell")


def test_off_strong_implies_sec_and_off():
    c = cues("Automated penetration testing")
    assert {"off_strong", "off", "sec"} <= c
    for text in ("CTF solving", "capture the flag", "privilege escalation", "lateral movement", "nmap scans",
                 "SQL injection", "XSS", "CVE-2024-1234", "zero-day", "n-day", "one-day", "kill chain",
                 "attack graph", "reverse shell", "Metasploit", "ethical hacking", "red-teaming",
                 "post-exploitation", "cyber range", "exploit generation", "compromised hosts",
                 "exploits web applications", "exploiting vulnerabilities"):
        assert "off_strong" in cues(text), text
    assert "off_strong" not in cues("Hackathon outcomes")


def test_scan_excludes_ast_cfg_slice_fuzz():
    assert "scan" not in cues("AST, CFG and slice based LLM-guided fuzzing")
    for text in ("vulnerability detection", "code review", "static analysis", "defect detection",
                 "bug detection"):
        assert "scan" in cues(text), text


def test_def_jb_cues():
    assert "def" in cues("intrusion detection with SIEM")
    assert "def" in cues("hardening and patching")
    assert "def" not in cues("dispatch of requests")
    for text in ("Jailbreak", "prompt injection", "safety alignment", "refusal", "toxicity", "backdoor",
                 "data poisoning"):
        assert "jb" in cues(text), text


def test_role_hints_use_title_and_head_of_abstract_only():
    assert "surv" in cues("A Survey of LLM Agents")
    assert "bench" in cues("A benchmark for LLM agents")
    assert "eval" in cues("An empirical study of LLM agents")
    late = "x " * 200 + "this is a survey and a benchmark and an empirical study"
    c = cues("Plain title", late)
    assert not ({"surv", "bench", "eval"} & c)
    assert "surv" in cues("Plain title", "A survey of the field. " + late)


def test_front_matter_title_only():
    assert "front" in cues("Foreword")
    assert "front" not in cues("Plain title", "This editorial is a foreword")
    assert tier(frozenset({"front", "ai"})) == 0
    assert tier(frozenset({"front", "ai", "sec"})) == 2


def test_seed_hint_similarity():
    c = cues("PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing")
    assert "seed" in c
    assert "seed" in cues("Large Language Models can Autonomously Hack Websites", "LLM attack")
    assert "seed" not in cues("LLM agents triage CVE reports and predict exploit likelihood")


def test_normalisation_dashes_case_unicode():
    assert "off_strong" in cues("RED\u2011TEAM ing with LLMs".replace("ing ", "ing "))
    assert "off_strong" in cues("Zero\u2014day attacks via LLM") or "off_strong" in cues("Zero\u2010day")
    assert cues("PENETRATION TESTING with LLMs") == cues("penetration testing with llms")
    assert "ai" in cues("\uff2c\uff2c\uff2d agents")  # full-width LLM, NFKC


def test_none_inputs_are_tolerated():
    assert cues(None, None) == frozenset()
    assert cues("") == frozenset()


# --------------------------------------------------------------------------- tier

def test_tier_truth_table():
    S = frozenset
    assert tier(S()) == 0
    assert tier(S({"ai"})) == 1
    assert tier(S({"sec"})) == 1
    assert tier(S({"ai", "sec"})) == 2
    assert tier(S({"ai", "sec", "off_strong"})) == 2
    assert tier(S({"ai", "sec", "agent"})) == 2
    assert tier(S({"ai", "sec", "off_strong", "agent"})) == 3
    assert tier(S({"ai", "sec", "seed"})) == 3
    assert tier(S({"off_strong", "agent"})) == 0      # no ai/sec tokens present at all
    assert tier(S({"sec", "off_strong", "agent"})) == 1
    assert tier(S({"front"})) == 0
    assert tier(S({"front", "sec"})) == 0
    assert tier(S({"ai", "sec", "front"})) == 2
    assert tier(S({"ai", "sec", "front", "off_strong", "agent"})) == 3
    assert tier(S({"ai"}), has_abstract=False) == tier(S({"ai"}), has_abstract=True)


def test_results_do_not_depend_on_corpus():
    items = [(t, a) for _n, t, a, *_ in PROBES]
    alone = [run_one(t, a) for t, a in items]
    mixed = [run_one(t, a) for t, a in items * 5 + [("Exploiting Parallelism", "")] * 50][:len(items)]
    assert alone == mixed
    assert [run_one(t, a) for t, a in reversed(items)][::-1] == alone


def test_modules_are_content_only():
    forbidden = re.compile(r"\b(venue|years?|authors?|dblp\w*|publications?|publisher|doi|cited|retracted|"
                           r"preprint|crossref)\b", re.I)
    for name in ("f_cues.py", "g_prior.py"):
        src = (HUNTER / name).read_text(encoding="utf-8")
        assert not forbidden.search(src), (name, forbidden.search(src).group(0))


def test_cues_accept_only_title_and_abstract():
    import inspect
    assert list(inspect.signature(cues).parameters) == ["title", "abstract"]
    assert list(inspect.signature(prior).parameters) == ["title", "abstract", "cue_set", "tier"]


# --------------------------------------------------------------------------- run_triage

def add_papers(conn, rows, start=100):
    """rows: list of (title, abstract); returns ids."""
    ids = list(range(start, start + len(rows)))
    with b_db.tx(conn):
        conn.executemany("INSERT INTO paper(id,title) VALUES(?,?)", [(i, t) for i, (t, _a) in zip(ids, rows)])
    b_db.put_abstracts(conn, [(i, a, "openalex") for i, (_t, a) in zip(ids, rows) if a])
    return ids


def triage_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM triage ORDER BY paper_id")]


def test_run_triage_incremental_and_histogram(tmp_db):
    n = b_db.count_papers(tmp_db)
    r1 = run_triage(tmp_db, workers=1)
    assert r1["processed"] == n
    assert set(r1["tiers"]) == {0, 1, 2, 3} and sum(r1["tiers"].values()) == n
    assert r1["tiers"][3] == 2 and r1["tiers"][0] == 3  # Incalmo + HackWorld; three hardware/RL negatives
    assert triage_rows(tmp_db)[0][:2] == (1, 3)
    assert triage_rows(tmp_db)[0][3] == "accept:baseline"
    assert run_triage(tmp_db, workers=1)["processed"] == 0
    assert run_triage(tmp_db, workers=1)["tiers"] == r1["tiers"]


def test_run_triage_reprocesses_when_abstract_arrives(tmp_db):
    run_triage(tmp_db, workers=1)
    ha = {r["paper_id"]: r["ha"] for r in tmp_db.execute("SELECT paper_id, ha FROM triage")}
    assert ha[2] == 0 and ha[1] == 1
    b_db.put_abstract(tmp_db, 2, "Computer-use agents autonomously exploit web application vulnerabilities.",
                      "web")
    r = run_triage(tmp_db, workers=1)
    assert r["processed"] == 1
    assert tmp_db.execute("SELECT ha FROM triage WHERE paper_id=2").fetchone()[0] == 1
    assert run_triage(tmp_db, workers=1)["processed"] == 0


def test_run_triage_reprocesses_when_rules_version_changes(tmp_db, monkeypatch):
    run_triage(tmp_db, workers=1)
    monkeypatch.setattr(b_db, "RULES_VERSION", b_db.RULES_VERSION + 1)
    n = b_db.count_papers(tmp_db)
    assert run_triage(tmp_db, workers=1)["processed"] == n
    assert {r[0] for r in tmp_db.execute("SELECT DISTINCT rv FROM triage")} == {b_db.RULES_VERSION}
    assert run_triage(tmp_db, workers=1)["processed"] == 0


def big_corpus(conn, n=2600):
    texts = [(t, a) for _n, t, a, *_ in PROBES]
    rows = [("%s #%d" % (t, i), a if i % 2 else "") for i, (t, a) in
            ((i, texts[i % len(texts)]) for i in range(n))]
    return add_papers(conn, rows)


def test_run_triage_pool_matches_single_process(tmp_db, monkeypatch):
    big_corpus(tmp_db)
    pools = []
    real = f_cues.get_context

    def spy(method):
        pools.append(method)
        return real(method)

    monkeypatch.setattr(f_cues, "get_context", spy)
    total = b_db.count_papers(tmp_db)

    r2 = run_triage(tmp_db, workers=2, chunk=1000)
    assert pools == ["fork"] and r2["processed"] == total
    pooled = triage_rows(tmp_db)

    with b_db.tx(tmp_db):
        tmp_db.execute("UPDATE triage SET rv=-1, tier=0, cues='', prior=NULL")
    r1 = run_triage(tmp_db, workers=1, chunk=1000)
    assert pools == ["fork"] and r1["processed"] == total          # single process: no new pool
    assert triage_rows(tmp_db) == pooled
    assert r1["tiers"] == r2["tiers"] and sum(r2["tiers"].values()) == total
    assert run_triage(tmp_db, workers=2)["processed"] == 0


def test_run_triage_is_interrupt_safe(tmp_db, monkeypatch):
    add_papers(tmp_db, [("%s #%d" % (NEUTRAL, i), "") for i in range(1200)], start=1000)
    total = b_db.count_papers(tmp_db)
    real = b_db.upsert_triage
    calls = []

    def flaky(conn, rows):
        calls.append(len(rows))
        if len(calls) == 2:
            raise KeyboardInterrupt
        real(conn, rows)

    monkeypatch.setattr(b_db, "upsert_triage", flaky)
    with pytest.raises(KeyboardInterrupt):
        run_triage(tmp_db, workers=1, chunk=500)
    done = tmp_db.execute("SELECT COUNT(*) FROM triage").fetchone()[0]
    assert done == 500
    monkeypatch.setattr(b_db, "upsert_triage", real)
    assert run_triage(tmp_db, workers=1, chunk=500)["processed"] == total - done
    assert tmp_db.execute("SELECT COUNT(*) FROM triage").fetchone()[0] == total


def test_run_triage_skips_duplicates(tmp_db):
    with b_db.tx(tmp_db):
        tmp_db.execute("UPDATE paper SET dup_of=1 WHERE id=2")
    r = run_triage(tmp_db, workers=1)
    assert r["processed"] == b_db.count_papers(tmp_db) and r["processed"] == 4
    assert tmp_db.execute("SELECT COUNT(*) FROM triage WHERE paper_id=2").fetchone()[0] == 0


def test_triage_cues_column_is_sorted_tokens(tmp_db):
    run_triage(tmp_db, workers=1)
    cue_str = tmp_db.execute("SELECT cues FROM triage WHERE paper_id=1").fetchone()[0]
    assert cue_str.split() == sorted(cue_str.split())
    assert {"ai", "agent", "sec", "off_strong"} <= set(cue_str.split())


# --------------------------------------------------------------------------- speed (T12)

def test_speed_under_2ms_per_paper():
    texts = [(t, a) for _n, t, a, *_ in PROBES]
    papers = [texts[i % len(texts)] for i in range(3000)]
    t0 = time.perf_counter()
    for title, abstract in papers:
        c = cues(title, abstract)
        prior(title, abstract, c, tier(c, bool(abstract)))
    ms = (time.perf_counter() - t0) * 1000 / len(papers)
    print("cues+tier+prior: %.3f ms/paper" % ms)
    assert ms <= 2.0
