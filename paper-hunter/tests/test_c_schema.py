"""Tests for c_schema: DDL integrity (T3), provenance (T7) and the validator rules."""

import copy
import random
import sqlite3
import sys
import time
import types

import pytest

import b_db
import c_schema
from c_schema import normtext, quote_in, to_row, validate_review
from conftest import SAMPLE_PAPERS

ABSTRACT = SAMPLE_PAPERS[0][1]
QUOTE = "We present Incalmo, an LLM agent that autonomously performs multi-stage attacks"
TITLE_2 = "HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities"
TITLE_QUOTE = "Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities"
REASON = "Presents an LLM agent that autonomously attacks multi-host networks."


def accept_rec(**over):
    rec = {"id": 1, "verdict": "accept", "p": 0.93, "role": "baseline",
           "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "p"},
           "level": "abstract", "quote_src": "abstract", "quote": QUOTE, "reason": REASON}
    rec.update(over)
    return rec


def reject_rec(**over):
    rec = {"id": 4, "verdict": "reject", "p": 0.05, "reject_class": "off_domain",
           "level": "title", "reason": "Reinforcement learning paper without any security topic."}
    rec.update(over)
    return rec


def lease(conn, pid, stage="review", owner="w1"):
    assert b_db.add_task(conn, pid, stage, 5, "gate")
    assert pid in b_db.claim(conn, stage, owner, n=50)


def set_triage(conn, pid, tier, prior=None):
    b_db.upsert_triage(conn, [(pid, tier, "ai sec", prior, 1, 1)])


def check(conn, rec, stage="review", owner="w1", pid=None, lease_it=True):
    pid = pid or rec["id"]
    if lease_it and not b_db.get_task(conn, pid, stage):
        lease(conn, pid, stage, owner)
    return validate_review(conn, rec, stage, owner)


def has(errs, text):
    return any(text in e for e in errs)


# --------------------------------------------------------------------------- T3: DDL integrity

def _insert(conn, **kw):
    row = {"paper_id": 1, "stage": "review", "by": "w1", "verdict": "accept", "p": 0.9,
           "role": "baseline", "rclass": None, "lvl": "abstract", "reason": "x" * 12, "at": 1}
    row.update(kw)
    cols = list(row)
    conn.execute("INSERT INTO review(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))),
                 list(row.values()))


def test_t3_ddl_accepts_valid_row(tmp_db):
    _insert(tmp_db)


@pytest.mark.parametrize("kw", [
    {"role": None},
    {"verdict": "reject", "p": 0.7, "role": None, "rclass": "off_domain"},
    {"verdict": "reject", "role": None, "rclass": None, "p": 0.1},
    {"p": 0.2},
])
def test_t3_ddl_check_rejects(tmp_db, kw):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(tmp_db, **kw)


def test_t3_foreign_key(tmp_db):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(tmp_db, paper_id=9999)


# --------------------------------------------------------------------------- to_row

def test_to_row_maps_columns():
    rec = accept_rec(vs_prior="agree", quote="  " + QUOTE + " ", reason="  " + REASON + "  ", junk=1)
    row = to_row(rec, "final", "w9")
    assert set(row) == set(b_db.REVIEW_COLS)
    assert (row["paper_id"], row["stage"], row["by"], row["verdict"]) == (1, "final", "w9", "accept")
    assert (row["c_agent"], row["c_off"], row["c_bb"], row["c_lh"]) == ("y", "y", "y", "p")
    assert (row["lvl"], row["qsrc"], row["role"], row["rclass"]) == ("abstract", "abstract", "baseline", None)
    assert row["quote"] == QUOTE and row["reason"] == REASON and row["vs_prior"] == "agree"
    assert isinstance(row["p"], float) and isinstance(row["at"], int)


def test_to_row_reject_class_and_int_p():
    row = to_row(reject_rec(p=0), "review", "w1")
    assert row["rclass"] == "off_domain" and row["p"] == 0.0 and isinstance(row["p"], float)
    assert row["c_agent"] is None


@pytest.mark.parametrize("rec", [None, [], "x", 5, {}, {"criteria": "yes", "p": "high", "reason": None},
                                 {"id": [1], "verdict": {"a": 1}, "criteria": [1], "quote": 7}])
def test_to_row_never_raises(rec):
    row = to_row(rec, "review", "w1")
    assert set(row) == set(b_db.REVIEW_COLS)


def test_to_row_row_is_storable(tmp_db):
    lease(tmp_db, 1)
    assert not validate_review(tmp_db, accept_rec(), "review", "w1")
    assert b_db.add_review(tmp_db, to_row(accept_rec(), "review", "w1")) > 0


# --------------------------------------------------------------------------- happy paths

def test_valid_accept_and_no_mutation(tmp_db):
    rec = accept_rec()
    before = copy.deepcopy(rec)
    assert check(tmp_db, rec) == []
    assert rec == before


def test_valid_reject_tier0_title_level(tmp_db):
    assert check(tmp_db, reject_rec()) == []


def test_returns_all_errors(tmp_db):
    rec = {"id": 1, "verdict": "accept", "p": 3, "level": "weird", "reason": "short"}
    errs = check(tmp_db, rec)
    assert len(errs) >= 5


# --------------------------------------------------------------------------- structure, lease, id

def test_missing_lease(tmp_db):
    errs = check(tmp_db, accept_rec(), lease_it=False)
    assert has(errs, "no 'review' task")


def test_wrong_owner(tmp_db):
    lease(tmp_db, 1, owner="w1")
    errs = validate_review(tmp_db, accept_rec(), "review", "w2")
    assert has(errs, "leased to another owner")


def test_task_for_other_stage(tmp_db):
    lease(tmp_db, 1, stage="review")
    errs = validate_review(tmp_db, accept_rec(), "blind", "w1")
    assert has(errs, "no 'blind' task")


def test_task_not_leased(tmp_db):
    lease(tmp_db, 1)
    b_db.finish_task(tmp_db, 1, "review", "w1")
    assert has(validate_review(tmp_db, accept_rec(), "review", "w1"), "is done, not leased")


def test_todo_task_is_not_leased(tmp_db):
    b_db.add_task(tmp_db, 1, "review", 5, "gate")
    assert has(validate_review(tmp_db, accept_rec(), "review", "w1"), "not leased")


def test_bad_stage(tmp_db):
    assert has(validate_review(tmp_db, accept_rec(), "nope", "w1"), "stage must be one of")


@pytest.mark.parametrize("pid", [None, "1", 1.0, True, 9999])
def test_bad_or_unknown_id(tmp_db, pid):
    errs = validate_review(tmp_db, accept_rec(id=pid), "review", "w1")
    assert has(errs, "id must be an integer") or has(errs, "unknown paper id")


def test_duplicate_paper_is_not_reviewable(tmp_db):
    with b_db.tx(tmp_db):
        tmp_db.execute("UPDATE paper SET dup_of=1 WHERE id=4")
    errs = validate_review(tmp_db, reject_rec(), "review", "w1")
    assert has(errs, "duplicate")


@pytest.mark.parametrize("rec", [None, [], "x", {}])
def test_garbage_record_gives_errors_not_exceptions(tmp_db, rec):
    assert validate_review(tmp_db, rec, "review", "w1")


# --------------------------------------------------------------------------- verdict consistency

@pytest.mark.parametrize("over,msg", [
    ({"role": None}, "accept needs role"),
    ({"role": "paper"}, "accept needs role"),
    ({"reject_class": "off_domain"}, "must not carry reject_class"),
    ({"p": 0.4}, "accept needs p >= 0.5"),
    ({"verdict": "maybe"}, "verdict must be"),
    ({"p": "0.9"}, "p must be a number"),
    ({"p": 1.5}, "p must be a number"),
    ({"p": True}, "p must be a number"),
    ({"p": None}, "p must be a number"),
    ({"level": None}, "level must be"),
])
def test_accept_consistency(tmp_db, over, msg):
    assert has(check(tmp_db, accept_rec(**over)), msg)


@pytest.mark.parametrize("over,msg", [
    ({"reject_class": None}, "reject needs reject_class"),
    ({"reject_class": "other"}, "reject needs reject_class"),
    ({"p": 0.5}, "reject needs p < 0.5"),
    ({"p": 0.9}, "reject needs p < 0.5"),
    ({"role": "baseline"}, "reject must not carry role"),
])
def test_reject_consistency(tmp_db, over, msg):
    assert has(check(tmp_db, reject_rec(**over)), msg)


@pytest.mark.parametrize("cls", c_schema.REJECT_CLASSES)
def test_all_reject_classes_accepted(tmp_db, cls):
    assert check(tmp_db, reject_rec(reject_class=cls)) == []


def test_bad_criteria_values(tmp_db):
    rec = accept_rec(criteria={"agent": "yes", "offense": "y", "black_box": "maybe"})
    errs = check(tmp_db, rec)
    assert has(errs, "criteria.agent must be one of") and has(errs, "criteria.black_box must be one of")


def test_criteria_not_an_object(tmp_db):
    assert has(check(tmp_db, accept_rec(criteria="y")), "criteria must be an object")


def test_reject_criteria_optional_but_validated(tmp_db):
    assert check(tmp_db, reject_rec(criteria={"agent": "n"})) == []
    lease_errs = check(tmp_db, reject_rec(criteria={"agent": "no"}))
    assert has(lease_errs, "criteria.agent must be one of")


# --------------------------------------------------------------------------- accept gate

@pytest.mark.parametrize("crit", [
    {"agent": "n", "offense": "y"},
    {"agent": "y", "offense": "p"},
    {"agent": "u", "offense": "u"},
    {"offense": "y"},
    None,
])
def test_accept_gate(tmp_db, crit):
    rec = accept_rec(role="benchmark")
    rec["criteria"] = crit
    assert has(check(tmp_db, rec), "criteria.agent='y' and criteria.offense='y'")


@pytest.mark.parametrize("key", ["black_box", "long_horizon"])
def test_baseline_soft_criteria_reject_n(tmp_db, key):
    rec = accept_rec()
    rec["criteria"][key] = "n"
    assert has(check(tmp_db, rec), "baseline must not have criteria.%s='n'" % key)


@pytest.mark.parametrize("val", ["y", "p", "u"])
def test_baseline_soft_criteria_pass(tmp_db, val):
    rec = accept_rec()
    rec["criteria"].update(black_box=val, long_horizon=val)
    assert check(tmp_db, rec) == []


@pytest.mark.parametrize("role", ["benchmark", "evaluation", "survey"])
def test_non_baseline_ignores_soft_criteria(tmp_db, role):
    rec = accept_rec(role=role)
    rec["criteria"].update(black_box="n", long_horizon="n")
    assert check(tmp_db, rec) == []


# --------------------------------------------------------------------------- T7 provenance

def test_t7_fulltext_level_without_row(tmp_db):
    errs = check(tmp_db, accept_rec(level="fulltext"))
    assert has(errs, "no full text is stored")


def test_t7_abstract_level_without_row(tmp_db):
    rec = accept_rec(id=2, quote=TITLE_QUOTE, quote_src="title")
    assert has(check(tmp_db, rec), "no abstract is stored")


def test_t7_fulltext_level_with_row(tmp_db):
    text = ("Filler sentence number %d about nothing. " * 120) % tuple(range(120))
    b_db.put_fulltext(tmp_db, 1, text + " The agent obtains root access on the target host. " + text, "web")
    rec = accept_rec(level="fulltext", quote_src="fulltext",
                     quote="The agent obtains root access on the target host.")
    assert check(tmp_db, rec) == []


def test_t7_quote_not_in_text(tmp_db):
    rec = accept_rec(quote="We present a quantum annealing compiler for hardware accelerators")
    assert has(check(tmp_db, rec), "quote not found in the stored abstract")


def test_quote_must_come_from_claimed_source(tmp_db):
    rec = accept_rec(quote=TITLE_QUOTE)
    assert has(check(tmp_db, rec), "quote not found")


@pytest.mark.parametrize("start", ["Research paper on LLM agents for penetration testing",
                                   "paper on autonomous attack agents in networks",
                                   "FRONT MATTER and other content of the volume"])
def test_t7_placeholder_quote(tmp_db, start):
    assert has(check(tmp_db, accept_rec(quote=start)), "placeholder")


def test_quote_word_limits(tmp_db):
    assert has(check(tmp_db, accept_rec(quote="We present Incalmo")), "quote has 3 words")
    b_db.put_abstract(tmp_db, 3, " ".join("word%d" % i for i in range(60)), "web", replace=True)
    rec = reject_rec(id=3, level="abstract", quote_src="abstract")
    rec["quote"] = " ".join("word%d" % i for i in range(46))
    assert has(check(tmp_db, rec), "quote has 46 words")
    rec["quote"] = " ".join("word%d" % i for i in range(45))
    assert check(tmp_db, rec) == []


def test_quote_required_for_accept(tmp_db):
    errs = check(tmp_db, accept_rec(quote=None, quote_src=None))
    assert has(errs, "quote is required")


def test_quote_src_required_with_quote(tmp_db):
    errs = check(tmp_db, accept_rec(quote_src=None))
    assert has(errs, "quote_src must be one of")


@pytest.mark.parametrize("level,qsrc", [("title", "abstract"), ("abstract", "fulltext")])
def test_quote_src_not_deeper_than_level(tmp_db, level, qsrc):
    b_db.put_fulltext(tmp_db, 4, "plain words here. " * 400, "web")
    rec = reject_rec(level=level, quote_src=qsrc, quote="Balancing exploration and exploitation in reinforcement learning")
    errs = check(tmp_db, rec)
    assert has(errs, "is deeper than level")


def test_quote_src_shallower_than_level_ok(tmp_db):
    b_db.put_fulltext(tmp_db, 1, "Filler words go here. " * 300, "web")
    rec = accept_rec(level="fulltext")
    assert check(tmp_db, rec) == []


def test_quote_fuzzy_match_in_abstract(tmp_db):
    rec = accept_rec(quote="We present Incalmo, an LLM agent that autonomously performs multi-stage attack")
    assert check(tmp_db, rec) == []


def test_quote_matches_after_normalisation(tmp_db):
    rec = accept_rec(quote="WE PRESENT  Incalmo,\nan LLM agent that autonomously performs multi\u2011stage attacks")
    assert check(tmp_db, rec) == []


# --------------------------------------------------------------------------- title level

def test_title_reject_only_when_no_abstract(tmp_db):
    assert has(check(tmp_db, reject_rec(id=1)), "not allowed for a reject")
    assert check(tmp_db, reject_rec(id=4)) == []


def test_abstract_level_reject_with_abstract_ok(tmp_db):
    rec = reject_rec(id=3, level="abstract", quote_src="abstract",
                     quote="Along with the fast evolution of deep neural networks")
    assert check(tmp_db, rec) == []


def test_title_reject_tier2_needs_quote_and_title_src_ok(tmp_db):
    set_triage(tmp_db, 2, 2)
    set_triage(tmp_db, 4, 2)
    errs = check(tmp_db, reject_rec(id=2, reject_class="llm_no_offense"))
    assert has(errs, "quote is required")
    ok = reject_rec(id=2, reject_class="llm_no_offense", quote_src="title", quote=TITLE_QUOTE)
    assert check(tmp_db, ok) == []


def test_reject_quote_optional_below_tier2(tmp_db):
    set_triage(tmp_db, 4, 1)
    assert check(tmp_db, reject_rec()) == []


def test_reject_quote_validated_when_given_below_tier2(tmp_db):
    rec = reject_rec(quote_src="title", quote="this text is not the paper title at all")
    assert has(check(tmp_db, rec), "quote not found")


def _fake_cues(monkeypatch, found):
    mod = types.ModuleType("f_cues")
    mod.cues = lambda title, abstract: frozenset(found)
    monkeypatch.setitem(sys.modules, "f_cues", mod)


def title_accept(**over):
    return accept_rec(id=2, level="title", quote_src="title", quote=TITLE_QUOTE, p=0.8, **over)


def test_title_accept_final_exception(tmp_db, monkeypatch):
    _fake_cues(monkeypatch, {"ai", "off_strong"})
    assert check(tmp_db, title_accept(), stage="final") == []


def test_title_accept_not_final(tmp_db, monkeypatch):
    _fake_cues(monkeypatch, {"ai", "off_strong"})
    for stage in ("review", "blind"):
        errs = check(tmp_db, title_accept(), stage=stage)
        assert has(errs, "only at stage 'final'")


def test_title_accept_needs_both_cues(tmp_db, monkeypatch):
    _fake_cues(monkeypatch, {"ai", "sec"})
    assert has(check(tmp_db, title_accept(), stage="final"), "AI and strong-offence cues")


def test_title_accept_needs_p(tmp_db, monkeypatch):
    _fake_cues(monkeypatch, {"ai", "off_strong"})
    rec = title_accept()
    rec["p"] = 0.55
    assert has(check(tmp_db, rec, stage="final"), "p >= 0.6")


def test_title_accept_needs_title_quote_src(tmp_db, monkeypatch):
    _fake_cues(monkeypatch, {"ai", "off_strong"})
    rec = title_accept()
    rec["quote_src"] = None
    errs = check(tmp_db, rec, stage="final")
    assert has(errs, "quote_src='title'")


def test_title_accept_without_f_cues_is_not_satisfied(tmp_db, monkeypatch):
    monkeypatch.setitem(sys.modules, "f_cues", None)
    assert has(check(tmp_db, title_accept(), stage="final"), "AI and strong-offence cues")


def test_title_accept_with_real_cues(tmp_db):
    pytest.importorskip("f_cues")
    title = "PentestGPT: LLM-Empowered Automatic Penetration Testing"
    with b_db.tx(tmp_db):
        tmp_db.execute("INSERT INTO paper(id,title) VALUES(100,?)", (title,))
    rec = accept_rec(id=100, level="title", quote_src="title", p=0.8, quote="LLM-Empowered Automatic Penetration Testing")
    assert check(tmp_db, rec, stage="final") == []
    weak = accept_rec(id=4, level="title", quote_src="title", p=0.8,
                      quote="Balancing exploration and exploitation in reinforcement learning")
    assert has(check(tmp_db, weak, stage="final"), "AI and strong-offence cues")


# --------------------------------------------------------------------------- vs_prior

def test_vs_prior_required_when_prior_set(tmp_db):
    set_triage(tmp_db, 1, 3, "accept:baseline")
    for stage in ("review", "final"):
        errs = check(tmp_db, accept_rec(), stage=stage)
        assert has(errs, "vs_prior"), stage
        assert check(tmp_db, accept_rec(vs_prior="agree"), stage=stage) == []
        assert check(tmp_db, accept_rec(vs_prior="override"), stage=stage) == []


def test_vs_prior_not_required_without_prior(tmp_db):
    set_triage(tmp_db, 1, 3, None)
    assert check(tmp_db, accept_rec()) == []
    assert check(tmp_db, accept_rec(vs_prior="agree")) == []


def test_vs_prior_forbidden_at_blind(tmp_db):
    set_triage(tmp_db, 1, 3, "accept:baseline")
    assert has(check(tmp_db, accept_rec(vs_prior="agree"), stage="blind"), "absent at stage 'blind'")
    assert check(tmp_db, accept_rec(), stage="blind") == []


def test_vs_prior_enum(tmp_db):
    assert has(check(tmp_db, accept_rec(vs_prior="maybe")), "vs_prior must be")


# --------------------------------------------------------------------------- reason

def test_reason_length(tmp_db):
    assert has(check(tmp_db, accept_rec(reason="too short")), "at least 10 characters")
    assert has(check(tmp_db, accept_rec(reason="   ")), "at least 10 characters")
    assert has(check(tmp_db, accept_rec(reason=None)), "at least 10 characters")
    assert has(check(tmp_db, accept_rec(reason=["a"])), "reason must be a string")
    assert has(check(tmp_db, accept_rec(reason=" ".join(["word"] * 61))), "61 words")
    assert check(tmp_db, accept_rec(reason=" ".join(["word"] * 60))) == []


def _store_reasons(conn, n, owner, reason):
    row = to_row(accept_rec(reason=reason), "review", owner)
    for _ in range(n):
        b_db.add_review(conn, row)


def test_repeated_reason_rule(tmp_db):
    _store_reasons(tmp_db, 5, "w1", REASON)
    assert check(tmp_db, accept_rec()) == []
    _store_reasons(tmp_db, 1, "w1", REASON)
    assert has(check(tmp_db, accept_rec()), "already submitted more than 5 times")


def test_repeated_reason_is_per_owner(tmp_db):
    _store_reasons(tmp_db, 8, "w2", REASON)
    assert check(tmp_db, accept_rec()) == []


def test_repeated_reason_other_text_unaffected(tmp_db):
    _store_reasons(tmp_db, 8, "w1", "A different, reused template sentence.")
    assert check(tmp_db, accept_rec()) == []


# --------------------------------------------------------------------------- content-only

def test_validator_ignores_bibliographic_columns(tmp_db):
    assert check(tmp_db, accept_rec(venue="X", year=1900, authors="Z", publication_status="preprint")) == []
    cols = {r["name"] for r in tmp_db.execute("PRAGMA table_info(paper)")}
    assert cols == {"id", "title", "doi", "link", "dup_of"}


# --------------------------------------------------------------------------- normtext / quote_in

def test_normtext():
    assert normtext("  Multi\u2011Host \u2014 \u201cRed\u201d Team\u2019s\n\tTools ") == 'multi-host - "red" team\'s tools'
    assert normtext("\ufb01nd") == "find"
    assert normtext(None) == "" and normtext(5) == ""


def test_quote_in_exact():
    assert quote_in("red teams to simulate", ABSTRACT)
    assert quote_in("RED   TEAMS\nto simulate", ABSTRACT)
    assert not quote_in("blue teams to simulate", ABSTRACT)
    assert not quote_in("", ABSTRACT) and not quote_in("x y z w", "")


def test_quote_in_fuzzy_small_text():
    assert quote_in("an LLM agent that autonomously performs multi-stage attack", ABSTRACT)
    assert quote_in("an LLM agent that autonomously performs multi-stage attacs", ABSTRACT)
    assert not quote_in("an LLM agent that autonomous performs multi-stage attacks", ABSTRACT)
    assert not quote_in("a transformer model that automatically schedules hardware kernels", ABSTRACT)


def _big_text(n_words=25_000, seed=7):
    rnd = random.Random(seed)
    vocab = ["w%dx" % i for i in range(3000)]
    return [rnd.choice(vocab) for _ in range(n_words)]


def test_quote_in_fulltext_shingles():
    words = _big_text()
    text = " ".join(words)
    assert len(text) > 100_000
    exact = " ".join(words[9000:9040])
    assert quote_in(exact, text)
    tail_typo = " ".join(words[9000:9039] + ["zzz"])
    assert quote_in(tail_typo, text)
    middle_typo = " ".join(words[9000:9020] + ["zzz"] + words[9021:9040])
    assert not quote_in(middle_typo, text)
    assert not quote_in(" ".join(_big_text(40, seed=99)), text)
    assert not quote_in("one two three", text)


def test_quote_in_fulltext_flag_forces_shingles_on_short_text():
    short = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
    near = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambdas"
    assert quote_in(near, short, fulltext=False)
    assert not quote_in(near, short, fulltext=True)


def test_quote_in_never_runs_sequence_matcher_on_large_text(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SequenceMatcher used on a large text")

    monkeypatch.setattr(c_schema.difflib, "SequenceMatcher", boom)
    words = _big_text()
    assert quote_in(" ".join(words[100:140]), " ".join(words))
    assert not quote_in(" ".join(_big_text(40, seed=5)), " ".join(words))


def test_quote_in_speed_150kb():
    words = _big_text(30_000)
    text = " ".join(words)
    assert len(text) >= 150_000
    miss = " ".join(_big_text(40, seed=11))
    near = " ".join(words[5000:5039] + ["zzz"])
    t0 = time.perf_counter()
    assert not quote_in(miss, text)
    assert quote_in(near, text)
    assert quote_in(" ".join(words[20000:20040]), text)
    assert time.perf_counter() - t0 < 0.5
