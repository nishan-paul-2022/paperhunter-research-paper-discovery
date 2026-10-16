"""Tests for h_flow: task policy, failure handling, concurrency, idempotence (spec T4-T6, T8, T9, T11)."""

import multiprocessing
import random
import time

import pytest

import b_db
import h_flow

REVIEW_CLASS = "off_domain"


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def corpus(tmp_path, make_csv):
    """corpus(tiers, abstracts=(), priors=None, titles=None) -> open connection; paper i has tiers[i-1]."""
    made = []

    def build(tiers, abstracts=(), priors=None, titles=None, name="corpus"):
        n = len(tiers)
        rows = [((titles[i] if titles else "Study number %d on subject %d" % (i + 1, i + 1)), "")
                for i in range(n)]
        db_path = tmp_path / (name + ".db")
        b_db.init_from_csv(make_csv(rows, name=name + ".csv"), db_path)
        conn = b_db.open_db(db_path)
        priors = priors or {}
        b_db.upsert_triage(conn, [(i, t, "cue", priors.get(i), 0, 1) for i, t in enumerate(tiers, 1)])
        b_db.put_abstracts(conn, [(i, "an abstract of paper %d" % i, "openalex") for i in abstracts])
        made.append(conn)
        return conn

    yield build
    for c in made:
        c.close()


def review_row(pid, stage, by, verdict="accept", p=None, role="baseline", lvl="abstract"):
    if verdict == "accept":
        return dict(paper_id=pid, stage=stage, by=by, verdict="accept", p=0.9 if p is None else p,
                    role=role, rclass=None, lvl=lvl, reason="stub accept reason")
    return dict(paper_id=pid, stage=stage, by=by, verdict="reject", p=0.1 if p is None else p,
                role=None, rclass=REVIEW_CLASS, lvl=lvl, reason="stub reject reason")


def rev(conn, pid, stage, by, **kw):
    """Store a stub review and apply the follow-up policy, as submit_reviews would."""
    row = review_row(pid, stage, by, **kw)
    with b_db.tx(conn):
        b_db.add_review(conn, row)
        h_flow.on_submit(conn, row)
    return row


def tasks(conn, stage=None, why=None):
    q = "SELECT * FROM task WHERE 1=1"
    args = []
    if stage:
        q += " AND stage=?"
        args.append(stage)
    if why:
        q += " AND why=?"
        args.append(why)
    return {r["paper_id"]: dict(r) for r in conn.execute(q, args)}


def snapshot(conn):
    """Every table that advance may touch (timestamps excluded)."""
    q = {
        "task": "SELECT paper_id,stage,state,prio,owner,until,tries,why FROM task ORDER BY paper_id,stage",
        "review": "SELECT paper_id,stage,by,verdict,p,role,rclass,lvl FROM review ORDER BY paper_id,stage,by",
        "result": "SELECT paper_id,verdict,role,p,by FROM result ORDER BY paper_id",
        "meta": "SELECT k,v FROM meta WHERE k LIKE 'escalated%' ORDER BY k",
    }
    return {k: [tuple(r) for r in conn.execute(s)] for k, s in q.items()}


def pick(conn, tier, sampled):
    """A paper id of `tier` whose hash sample is (not) under the 20% sampling threshold."""
    for (pid,) in conn.execute("SELECT paper_id FROM triage WHERE tier=? ORDER BY paper_id", (tier,)):
        if (h_flow.hash_frac(pid) < h_flow.SAMPLE_RATE) == sampled:
            return pid
    raise AssertionError("no such paper")


def exhaust(conn, pid, stage):
    """Lease a task three times with expiry in between; returns the clock after the third lease."""
    for i in range(3):
        assert b_db.claim(conn, stage, "ghost%d" % i, now_ts=1000 + 2000 * i) == [pid]
    return 1000 + 2000 * 2 + b_db.LEASE_SECONDS


# --------------------------------------------------------------------------- task creation

def test_review_tasks_by_tier(corpus):
    conn = corpus([3, 2, 1, 1, 0, 2], abstracts=[3])
    st = h_flow.advance(conn)
    t = tasks(conn, "review")
    assert set(t) == {1, 2, 6}
    assert (t[1]["prio"], t[1]["why"]) == (0, "strong")
    assert (t[2]["prio"], t[2]["why"]) == (3, "gate")
    assert st["open"]["review"] == 3 and st["done"] is False


def test_noabs_only_after_deep_fetch(corpus):
    conn = corpus([1, 1, 1], abstracts=[2])
    h_flow.advance(conn)
    assert tasks(conn, "review") == {}
    b_db.meta_set(conn, "deep_fetch_done", "1")
    h_flow.advance(conn)
    t = tasks(conn, "review")
    assert set(t) == {1, 3}
    assert (t[1]["prio"], t[1]["why"]) == (4, "noabs")


def test_skip_papers_with_result_or_review_and_duplicates(corpus):
    titles = ["same title here", "same title here", "other paper", "yet another"]
    conn = corpus([2, 2, 2, 2], titles=titles)
    assert conn.execute("SELECT dup_of FROM paper WHERE id=2").fetchone()[0] == 1
    b_db.upsert_result(conn, 3, "reject", None, 0.4, "fallback")
    b_db.add_review(conn, review_row(4, "review", "x", verdict="reject"))
    h_flow.advance(conn)
    assert set(tasks(conn, "review")) == {1}


def test_audit_sampling_is_hash_based_with_minimum(corpus):
    tiers = [0] * 3000 + [1] * 60 + [1] * 5
    conn = corpus(tiers, abstracts=range(3001, 3061))
    h_flow.advance(conn)
    rate0 = max(0.005, 100 / 3000)
    expected0 = {i for i in range(1, 3001) if h_flow.hash_frac(i) < rate0}
    t0 = tasks(conn, "blind", "audit0")
    assert set(t0) == expected0 and 60 <= len(t0) <= 150
    assert all(v["prio"] == 6 for v in t0.values())
    # tier-1 stratum has 60 papers with an abstract: all audited; the 5 without one are not
    assert set(tasks(conn, "blind", "audit1")) == set(range(3001, 3061))


def test_audit_base_rate_for_large_stratum(corpus):
    conn = corpus([0] * 30000)
    h_flow.advance(conn)
    expected = {i for i in range(1, 30001) if h_flow.hash_frac(i) < 0.005}
    assert set(tasks(conn, "blind", "audit0")) == expected


def test_audit_skips_reviewed_papers(corpus):
    conn = corpus([0] * 40)
    b_db.add_review(conn, review_row(5, "review", "x", verdict="reject"))
    h_flow.advance(conn)
    assert 5 not in tasks(conn, "blind")
    assert len(tasks(conn, "blind")) == 39


# --------------------------------------------------------------------------- follow-up table (T8)

FOLLOWUPS = [
    # tier, prior, verdict, p, sampled, expected
    (2, None, "accept", 0.9, False, ("blind", "confirm", 1)),
    (1, None, "accept", 0.6, False, ("blind", "confirm", 1)),
    (3, None, "reject", 0.1, False, ("blind", "strong", 1)),
    (2, None, "reject", 0.25, False, ("blind", "uncertain", 1)),
    (2, None, "reject", 0.4, False, ("blind", "uncertain", 1)),
    (2, "accept:baseline", "reject", 0.1, False, ("blind", "disagree", 1)),
    (2, None, "reject", 0.1, True, ("blind", "sample", 2)),
    (2, None, "reject", 0.1, False, ("result", "review", None)),
    (2, "reject:defensive_only", "reject", 0.2, False, ("result", "review", None)),
    (1, None, "reject", 0.1, True, ("result", "review", None)),
]


@pytest.mark.parametrize("tier,prior,verdict,p,sampled,expected", FOLLOWUPS)
def test_followup_table(corpus, tier, prior, verdict, p, sampled, expected):
    conn = corpus([tier] * 60, priors={i: prior for i in range(1, 61)} if prior else None)
    pid = pick(conn, tier, sampled)
    row = rev(conn, pid, "review", "alice", verdict=verdict, p=p)
    kind, what, prio = expected
    if kind == "blind":
        t = b_db.get_task(conn, pid, "blind")
        assert (t["why"], t["prio"], t["state"]) == (what, prio, "todo")
        assert b_db.get_result(conn, pid) is None
    else:
        res = b_db.get_result(conn, pid)
        assert (res["by"], res["verdict"], res["role"], res["p"]) == (what, verdict, row["role"], p)
        assert b_db.get_task(conn, pid, "blind") is None


def test_blind_agree_is_consensus_with_mean_p(corpus):
    conn = corpus([2] * 5)
    rev(conn, 1, "review", "a", p=0.9)
    rev(conn, 1, "blind", "b", p=0.7)
    res = b_db.get_result(conn, 1)
    assert (res["by"], res["verdict"], res["role"]) == ("consensus", "accept", "baseline")
    assert res["p"] == pytest.approx(0.8)
    assert b_db.get_task(conn, 1, "final") is None


def test_reject_agreement_ignores_reject_class(corpus):
    conn = corpus([3] * 5)
    rev(conn, 1, "review", "a", verdict="reject", p=0.2)
    row = review_row(1, "blind", "b", verdict="reject", p=0.1)
    row["rclass"] = "defensive_only"
    with b_db.tx(conn):
        b_db.add_review(conn, row)
        h_flow.on_submit(conn, row)
    res = b_db.get_result(conn, 1)
    assert (res["by"], res["verdict"], res["role"], res["p"]) == ("consensus", "reject", None, pytest.approx(0.15))


@pytest.mark.parametrize("b_kw", [
    dict(verdict="reject", p=0.2),
    dict(verdict="accept", role="benchmark"),
])
def test_blind_disagreement_queues_final(corpus, b_kw):
    conn = corpus([2] * 5)
    rev(conn, 1, "review", "a", role="baseline")
    rev(conn, 1, "blind", "b", **b_kw)
    t = b_db.get_task(conn, 1, "final")
    assert (t["why"], t["prio"], t["state"]) == ("conflict", 0, "todo")
    assert b_db.get_result(conn, 1) is None
    rev(conn, 1, "final", "c", verdict="accept", p=0.65, role="survey")
    res = b_db.get_result(conn, 1)
    assert (res["by"], res["verdict"], res["role"], res["p"]) == ("final", "accept", "survey", 0.65)


def test_final_reject_has_no_role(corpus):
    conn = corpus([2] * 5)
    rev(conn, 1, "review", "a")
    rev(conn, 1, "blind", "b", verdict="reject")
    rev(conn, 1, "final", "c", verdict="reject", p=0.3)
    res = b_db.get_result(conn, 1)
    assert (res["by"], res["verdict"], res["role"]) == ("final", "reject", None)


def test_waits_while_blind_task_is_open(corpus):
    conn = corpus([2] * 5)
    rev(conn, 1, "review", "a")
    assert b_db.claim(conn, "blind", "b", now_ts=1000) == [1]
    h_flow.advance(conn, now_ts=1100)
    assert b_db.get_result(conn, 1) is None


# --------------------------------------------------------------------------- audit escalation (T8)

def _audit_accept(conn, pid, owner="aud"):
    rev(conn, pid, "blind", owner)


def test_audit_reject_changes_nothing(corpus):
    conn = corpus([0] * 3000)
    h_flow.advance(conn)
    pid = min(tasks(conn, "blind", "audit0"))
    before = len(tasks(conn))
    rev(conn, pid, "blind", "aud", verdict="reject")
    st = h_flow.advance(conn)
    assert len(tasks(conn)) == before and b_db.get_result(conn, pid) is None
    assert b_db.get_task(conn, pid, "final") is None and b_db.meta_get(conn, "escalated_t0") is None
    assert st["finalized"] == 0


def test_audit_accept_queues_final_and_escalates_once(corpus):
    conn = corpus([0] * 3000 + [1] * 300, abstracts=range(3001, 3301))
    h_flow.advance(conn)
    audits = sorted(tasks(conn, "blind", "audit0"))
    n0 = len(audits)
    _audit_accept(conn, audits[0])
    t = b_db.get_task(conn, audits[0], "final")
    assert (t["why"], t["prio"]) == ("conflict", 0)
    esc = tasks(conn, "blind", "escalate")
    assert esc and all(v["prio"] == 6 for v in esc.values())
    assert all(p <= 3000 for p in esc) and b_db.meta_get(conn, "escalated_t0") == "1"
    assert b_db.meta_get(conn, "escalated_t1") is None
    expected = {i for i in range(1, 3001) if h_flow.hash_frac(i) < min(1.0, 10 * 100 / 3000)} - set(audits)
    assert set(esc) == expected and n0 + len(esc) <= h_flow.ESCALATION_CAP
    _audit_accept(conn, audits[1])
    assert b_db.get_task(conn, audits[1], "final") is not None
    assert set(tasks(conn, "blind", "escalate")) == set(esc)


def test_escalation_is_capped(corpus, monkeypatch):
    conn = corpus([0] * 3000)
    h_flow.advance(conn)
    audits = sorted(tasks(conn, "blind", "audit0"))
    monkeypatch.setattr(h_flow, "ESCALATION_CAP", len(audits) + 25)
    _audit_accept(conn, audits[0])
    assert len(tasks(conn, "blind", "escalate")) == 25


def test_tier1_escalation_uses_its_own_flag(corpus):
    conn = corpus([1] * 400, abstracts=range(1, 401))
    h_flow.advance(conn)
    pid = min(tasks(conn, "blind", "audit1"))
    _audit_accept(conn, pid)
    assert b_db.meta_get(conn, "escalated_t1") == "1" and b_db.meta_get(conn, "escalated_t0") is None
    assert tasks(conn, "blind", "escalate")


def test_advance_repairs_missed_followups(corpus):
    conn = corpus([2] * 4)
    b_db.add_review(conn, review_row(1, "review", "a"))                      # accept: needs blind
    b_db.add_review(conn, review_row(2, "review", "a", verdict="reject"))    # finalize now
    st = h_flow.advance(conn)
    assert b_db.get_task(conn, 1, "blind")["why"] == "confirm"
    assert b_db.get_result(conn, 2)["by"] == "review"
    assert st["finalized"] == 1


def test_advance_is_idempotent(corpus):
    conn = corpus([0] * 600 + [1] * 120 + [2] * 40 + [3] * 10, abstracts=range(601, 721))
    h_flow.advance(conn)
    rng = random.Random(3)
    for stage in ("review", "blind"):
        for pid in b_db.claim(conn, stage, "w1" if stage == "review" else "w2", n=40, now_ts=1000):
            verdict = rng.choice(["accept", "reject"])
            rev(conn, pid, stage, "w1" if stage == "review" else "w2", verdict=verdict)
    first = h_flow.advance(conn, now_ts=1001)
    snap = snapshot(conn)
    stamps = conn.execute("SELECT paper_id, at FROM result ORDER BY paper_id").fetchall()
    time.sleep(1.1)
    assert h_flow.advance(conn, now_ts=1001) == first
    assert snapshot(conn) == snap
    assert conn.execute("SELECT paper_id, at FROM result ORDER BY paper_id").fetchall() == stamps


# --------------------------------------------------------------------------- failures (T5)

def test_lease_expiry_reclaim(corpus):
    conn = corpus([3])
    h_flow.advance(conn)
    assert b_db.claim(conn, "review", "a", now_ts=1000) == [1]
    assert b_db.claim(conn, "review", "b", now_ts=1500) == []
    assert b_db.claim(conn, "review", "b", now_ts=1000 + b_db.LEASE_SECONDS + 1) == [1]
    assert b_db.get_task(conn, 1, "review")["owner"] == "b"


def test_review_failed_falls_back_to_prior_accept(corpus):
    conn = corpus([3], priors={1: "accept:benchmark"})
    h_flow.advance(conn)
    clock = exhaust(conn, 1, "review")
    st = h_flow.advance(conn, now_ts=clock + 1)
    res = b_db.get_result(conn, 1)
    assert (res["verdict"], res["role"], res["p"], res["by"]) == ("accept", "benchmark", 0.5, "fallback")
    assert st["failed"] == 1 and st["done"] is True and st["open"]["review"] == 0


@pytest.mark.parametrize("prior", [None, "reject:defensive_only"])
def test_review_failed_falls_back_to_reject(corpus, prior):
    conn = corpus([2], priors={1: prior} if prior else None)
    h_flow.advance(conn)
    clock = exhaust(conn, 1, "review")
    assert h_flow.advance(conn, now_ts=clock + 1)["done"] is True
    res = b_db.get_result(conn, 1)
    assert (res["verdict"], res["role"], res["p"], res["by"]) == ("reject", None, 0.4, "fallback")


def test_lease_not_failed_before_expiry(corpus):
    conn = corpus([2])
    h_flow.advance(conn)
    clock = exhaust(conn, 1, "review")
    st = h_flow.advance(conn, now_ts=clock - 1)
    assert st["failed"] == 0 and st["leased"] == 1 and st["done"] is False


def test_blind_failed_keeps_first_review(corpus):
    conn = corpus([2] * 3)
    rev(conn, 1, "review", "a", p=0.8)
    clock = exhaust(conn, 1, "blind")
    st = h_flow.advance(conn, now_ts=clock + 1)
    res = b_db.get_result(conn, 1)
    assert (res["by"], res["verdict"], res["p"]) == ("review", "accept", 0.8) and st["done"] is True


def test_final_failed_uses_mean_p_and_deepest_role(corpus):
    conn = corpus([2] * 4)
    rev(conn, 1, "review", "a", p=0.7, role="baseline", lvl="abstract")
    rev(conn, 1, "blind", "b", p=0.9, role="benchmark", lvl="fulltext")
    clock = exhaust(conn, 1, "final")
    h_flow.advance(conn, now_ts=clock + 1)
    res = b_db.get_result(conn, 1)
    assert (res["verdict"], res["role"], res["by"]) == ("accept", "benchmark", "fallback")
    assert res["p"] == pytest.approx(0.8)

    rev(conn, 2, "review", "a", p=0.55, role="baseline")
    rev(conn, 2, "blind", "b", verdict="reject", p=0.05)
    clock = exhaust(conn, 2, "final")
    h_flow.advance(conn, now_ts=clock + 1)
    res = b_db.get_result(conn, 2)
    assert (res["verdict"], res["role"], res["by"]) == ("reject", None, "fallback")
    assert res["p"] == pytest.approx(0.3)


# --------------------------------------------------------------------------- independence (T6)

def test_blind_claim_excludes_first_reviewer(corpus):
    conn = corpus([2] * 3)
    rev(conn, 1, "review", "alice")
    rev(conn, 2, "review", "bob")
    assert b_db.claim(conn, "blind", "alice", now_ts=1000) == [2]
    assert b_db.claim(conn, "blind", "bob", now_ts=1000) == [1]
    assert b_db.claim(conn, "blind", "carol", now_ts=1000) == []


# --------------------------------------------------------------------------- termination (T9)

def stub_submit(conn, pid, stage, owner, rng):
    accept = rng.random() < 0.5
    p = rng.uniform(0.5, 1.0) if accept else rng.uniform(0.0, 0.49)
    rev(conn, pid, stage, owner, verdict="accept" if accept else "reject", p=p,
        role=rng.choice(["baseline", "benchmark"]))
    assert b_db.finish_task(conn, pid, stage, owner)


def drive(conn, rng, owners, fail_p, max_rounds=100):
    """Worker pool simulation on a virtual clock; a failing worker abandons its lease."""
    clock = 1000
    for rnd in range(max_rounds):
        st = h_flow.advance(conn, now_ts=clock)
        if st["done"]:
            return st, rnd
        for stage in ("review", "blind", "final"):
            for owner in owners:
                while ids := b_db.claim(conn, stage, owner, n=8, now_ts=clock):
                    for pid in ids:
                        if rng.random() >= fail_p:
                            stub_submit(conn, pid, stage, owner, rng)
        clock += b_db.LEASE_SECONDS + 100
    raise AssertionError("did not terminate")


def test_random_worker_failures_still_terminate(corpus):
    tiers = [0] * 150 + [1] * 60 + [2] * 60 + [3] * 30
    conn = corpus(tiers, abstracts=range(151, 211), priors={i: "accept:baseline" for i in range(271, 301)})
    st, rounds = drive(conn, random.Random(11), ["w0", "w1", "w2", "w3"], fail_p=0.3)
    assert st["done"] is True and st["open"] == {"review": 0, "blind": 0, "final": 0} and st["leased"] == 0
    assert st["failed"] > 0 and rounds > 3
    assert not conn.execute("SELECT 1 FROM task WHERE state IN ('todo','leased')").fetchone()
    assert not conn.execute(
        "SELECT 1 FROM review r WHERE r.stage<>'blind' AND NOT EXISTS(SELECT 1 FROM result x WHERE x.paper_id=r.paper_id)"
    ).fetchone()
    assert not conn.execute("SELECT 1 FROM task WHERE stage='review' AND NOT EXISTS("
                            "SELECT 1 FROM result x WHERE x.paper_id=task.paper_id)").fetchone()
    assert {r[0] for r in conn.execute("SELECT DISTINCT by FROM result")} <= {"review", "consensus", "final", "fallback"}
    assert conn.execute("SELECT COUNT(*) FROM result WHERE verdict='reject' AND role IS NOT NULL").fetchone()[0] == 0


# --------------------------------------------------------------------------- resume (T11)

def det_review(pid, stage):
    if stage == "review":
        return ("accept", 0.9, "baseline") if pid % 3 == 0 else ("reject", 0.3 if pid % 5 == 0 else 0.1, None)
    if stage == "blind":
        return ("accept", 0.8, "benchmark" if pid % 4 == 2 else "baseline") if pid % 2 == 0 else ("reject", 0.2, None)
    return ("accept", 0.7, "baseline") if pid % 5 < 2 else ("reject", 0.3, None)


def run_flow(conn, max_rounds=60):
    clock = 1000
    for _ in range(max_rounds):
        try:
            st = h_flow.advance(conn, now_ts=clock)
        except Boom:
            continue
        if st["done"]:
            return
        for stage, owner in (("review", "r"), ("blind", "b"), ("final", "f")):
            for pid in b_db.claim(conn, stage, owner, n=100000, now_ts=clock):
                verdict, p, role = det_review(pid, stage)
                rev(conn, pid, stage, owner, verdict=verdict, p=p, role=role)
                b_db.finish_task(conn, pid, stage, owner)
        clock += 1000
    raise AssertionError("did not converge")


class Boom(Exception):
    pass


def resume_corpus(corpus, name):
    tiers = [[0, 0, 0, 0, 0, 0, 1, 1, 2, 3][i % 10] for i in range(400)]
    abstracts = [i + 1 for i, t in enumerate(tiers) if t == 1]
    return corpus(tiers, abstracts=abstracts, name=name)


@pytest.mark.parametrize("target,before", [
    ("_resolve_failed", False), ("_create_review_tasks", False), ("_sample_audits", False),
    ("_evaluate_pending", False), ("_evaluate", True),
])
def test_interrupted_advance_converges(corpus, monkeypatch, target, before):
    clean = resume_corpus(corpus, "clean")
    run_flow(clean)
    want = snapshot(clean)
    assert want["result"] and want["meta"]  # the scenario exercises results and an escalation

    broken = resume_corpus(corpus, "broken")
    original, calls = getattr(h_flow, target), {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        hit = calls["n"] in (2, 3, 7)
        if hit and before:
            raise Boom()
        out = original(*a, **kw)
        if hit:
            raise Boom()
        return out

    monkeypatch.setattr(h_flow, target, flaky)
    run_flow(broken)
    assert calls["n"] > 7
    assert snapshot(broken) == want


# --------------------------------------------------------------------------- speed

def test_advance_task_creation_is_fast_on_20k(corpus):
    n = 20000
    tiers = [3 if i % 100 == 0 else 2 if i % 20 == 0 else 1 if i % 7 == 0 else 0 for i in range(1, n + 1)]
    ones = [i for i, t in enumerate(tiers, 1) if t == 1]
    conn = corpus(tiers, abstracts=ones[::2])
    t0 = time.time()
    st = h_flow.advance(conn)
    elapsed = time.time() - t0
    assert elapsed < 5, elapsed
    n_review = sum(1 for t in tiers if t >= 2)
    assert st["open"]["review"] == n_review
    assert st["open"]["blind"] == len(tasks(conn, "blind")) > 100
    t0 = time.time()
    assert h_flow.advance(conn) == st
    assert time.time() - t0 < 5


# --------------------------------------------------------------------------- concurrency (T4)

def _t4_worker(db_path, owner, barrier, queue):
    conn = b_db.open_db(db_path)
    claimed, errors = [], []
    barrier.wait()
    try:
        while ids := b_db.claim(conn, "review", owner, n=8):
            claimed += ids
            for pid in ids:
                row = review_row(pid, "review", owner, verdict="reject")
                with b_db.tx(conn):
                    b_db.add_review(conn, row)
                    b_db.finish_task(conn, pid, "review", owner)
                    h_flow.on_submit(conn, row)
    except Exception as e:  # reported to the parent
        errors.append(repr(e))
    conn.close()
    queue.put((owner, claimed, errors))


def test_eight_processes_claim_and_submit_each_paper_once(corpus, tmp_path):
    n, workers = 2400, 8
    conn = corpus([2] * n, name="conc")
    h_flow.advance(conn)
    db_path = tmp_path / "conc.db"
    conn.close()

    ctx = multiprocessing.get_context("fork")
    barrier, queue = ctx.Barrier(workers), ctx.Queue()
    procs = [ctx.Process(target=_t4_worker, args=(str(db_path), "w%d" % i, barrier, queue)) for i in range(workers)]
    for p in procs:
        p.start()
    out = [queue.get(timeout=300) for _ in procs]
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    assert [e for _o, _c, errs in out for e in errs] == []
    claimed = sorted(pid for _o, c, _e in out for pid in c)
    assert claimed == list(range(1, n + 1))
    assert sum(1 for _o, c, _e in out if c) > 1  # work really was shared

    conn = b_db.open_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*), COUNT(DISTINCT paper_id) FROM review").fetchone()[:] == (n, n)
        assert conn.execute("SELECT COUNT(*) FROM task WHERE stage='review' AND state='done'").fetchone()[0] == n
        st = h_flow.advance(conn)
        assert st["open"]["review"] == 0 and st["finalized"] + st["open"]["blind"] == n
    finally:
        conn.close()


# --------------------------------------------------------------------------- submit_reviews end to end

@pytest.fixture
def e2e(tmp_db):
    pytest.importorskip("c_schema")
    b_db.upsert_triage(tmp_db, [
        (1, 3, "ai sec off_strong", "accept:baseline", 1, 1), (2, 3, "ai sec off_strong", None, 0, 1),
        (3, 0, "", None, 1, 1), (4, 0, "", None, 0, 1), (5, 0, "", None, 0, 1)])
    return tmp_db


def accept_rec(pid, quote, vs_prior=None, role="baseline", p=0.9, reason="Clear agentic attack system."):
    rec = {"id": pid, "verdict": "accept", "p": p, "role": role,
           "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "p"},
           "level": "abstract", "quote_src": "abstract", "quote": quote, "reason": reason}
    if vs_prior:
        rec["vs_prior"] = vs_prior
    return rec


def reject_rec(pid, level="title", quote=None, quote_src=None, reason="Not the target topic at all."):
    rec = {"id": pid, "verdict": "reject", "p": 0.1, "reject_class": "off_domain", "level": level, "reason": reason}
    if quote:
        rec.update(quote=quote, quote_src=quote_src)
    return rec


Q1 = "We present Incalmo, an LLM agent that autonomously performs multi-stage attacks"
Q2 = "Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities"
Q3 = "Along with the fast evolution of deep neural networks"


def test_submit_reviews_full_flow(e2e):
    conn = e2e
    h_flow.advance(conn)
    assert b_db.claim(conn, "review", "r1", now_ts=None) == [1, 2]
    out = h_flow.submit_reviews(conn, [
        accept_rec(1, Q1, vs_prior="agree"),
        reject_rec(2, quote=Q2, quote_src="title"),
        accept_rec(999, Q1),
        "garbage",
    ], "r1", "review")
    assert [(o["ok"], o["duplicate"]) for o in out] == [(True, False), (True, False), (False, False), (False, False)]
    assert out[2]["errors"] and out[3]["errors"] and out[0]["errors"] == []
    assert b_db.get_task(conn, 1, "review")["state"] == "done"
    assert b_db.get_task(conn, 1, "blind")["why"] == "confirm"
    assert b_db.get_task(conn, 2, "blind")["why"] == "strong"

    dup = h_flow.submit_reviews(conn, [accept_rec(1, Q1, vs_prior="agree")], "r1", "review")
    assert dup == [{"id": 1, "ok": True, "duplicate": True, "errors": []}]
    assert len(b_db.reviews_for(conn, 1)) == 1

    other = h_flow.submit_reviews(conn, [accept_rec(1, Q1, vs_prior="agree")], "r2", "review")
    assert other[0]["ok"] is False  # no lease for r2

    assert b_db.claim(conn, "blind", "b1") == [1, 2, 3, 4, 5]
    out = h_flow.submit_reviews(conn, [
        accept_rec(1, Q1, p=0.7),
        reject_rec(2, quote=Q2, quote_src="title"),
        accept_rec(3, Q3, reason="Looks like an agent paper."),
        reject_rec(4),
        reject_rec(5, reason="x"),
    ], "b1", "blind")
    assert [o["ok"] for o in out] == [True, True, True, True, False]
    assert out[4]["errors"]
    r1, r2 = b_db.get_result(conn, 1), b_db.get_result(conn, 2)
    assert (r1["by"], r1["verdict"], r1["role"], r1["p"]) == ("consensus", "accept", "baseline", pytest.approx(0.8))
    assert (r2["by"], r2["verdict"], r2["role"]) == ("consensus", "reject", None)
    assert b_db.get_result(conn, 3) is None
    assert b_db.get_task(conn, 3, "final")["why"] == "conflict" and b_db.meta_get(conn, "escalated_t0") == "1"

    assert b_db.claim(conn, "final", "f1") == [3]
    out = h_flow.submit_reviews(conn, [reject_rec(3, level="abstract")], "f1", "final")
    assert out[0]["ok"], out
    assert b_db.get_result(conn, 3)["by"] == "final"

    b_db.close_task(conn, 5, "blind", "failed")
    st = h_flow.advance(conn)
    assert st["done"] is True and st["finalized"] == 3
    assert b_db.get_result(conn, 4) is None  # audit reject: implicit rule reject, no result row


def test_submit_reviews_failure_is_isolated(e2e):
    conn = e2e
    h_flow.advance(conn)
    b_db.claim(conn, "review", "r1")
    bad = accept_rec(1, "quote that is not in the abstract at all", vs_prior="agree")
    good = reject_rec(2, quote=Q2, quote_src="title")
    out = h_flow.submit_reviews(conn, [bad, good], "r1", "review")
    assert [o["ok"] for o in out] == [False, True]
    assert b_db.reviews_for(conn, 1) == [] and b_db.get_task(conn, 1, "review")["state"] == "leased"
