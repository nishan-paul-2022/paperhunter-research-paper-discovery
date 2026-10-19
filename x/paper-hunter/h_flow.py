"""Workflow state machine: task creation, follow-up policy, finalization, and fallbacks.

Policy lives here. Pure functions of table contents. _evaluate() is the single policy
evaluator called by on_submit() and advance(). Idempotent: running advance() twice is safe.

Flow: triage → review → blind (on uncertainty) → final (on conflict) → result.
Decisions are deterministic. Content only: no bibliographic fields read.
"""

import b_db

ROLES = ("baseline", "benchmark", "evaluation", "survey")
LVL_RANK = {"title": 0, "abstract": 1, "fulltext": 2}

UNCERTAIN_LO, UNCERTAIN_HI = 0.25, 0.75
SAMPLE_RATE = 0.20
FALLBACK_ACCEPT_P, FALLBACK_REJECT_P = 0.5, 0.4

AUDIT_PRIO = 6
MIN_AUDIT = 100
ESCALATION_FACTOR = 10
ESCALATION_CAP = 5000
EVAL_BATCH = 200

_ABSTRACT = "EXISTS(SELECT 1 FROM paper_abstract a WHERE a.paper_id=t.paper_id)"
# tier -> (escalation meta key, audit `why`, stratum condition over alias t, base sampling rate)
STRATA = {
    0: ("escalated_t0", "audit0", "t.tier=0", 0.005),
    1: ("escalated_t1", "audit1", "t.tier=1 AND " + _ABSTRACT, 0.05),
}

_HASH = "((t.paper_id*2654435761) % 10000)"
_NO_RESULT = "NOT EXISTS(SELECT 1 FROM hunt_result x WHERE x.paper_id=t.paper_id)"
_NO_REVIEW = "NOT EXISTS(SELECT 1 FROM hunt_review r WHERE r.paper_id=t.paper_id)"
_NO_TASK = "NOT EXISTS(SELECT 1 FROM hunt_task k WHERE k.paper_id=t.paper_id)"
# papers with a review that still lack a result; blind-only rejects are the exempt audit hits
_PENDING = (
    "SELECT g.paper_id FROM (SELECT paper_id FROM hunt_review GROUP BY paper_id "
    "HAVING SUM(stage<>'blind' OR verdict='accept')>0) g "
    "WHERE NOT EXISTS(SELECT 1 FROM hunt_result x WHERE x.paper_id=g.paper_id)"
)


def hash_frac(pid):
    """Deterministic sampling value in [0,1) for a paper id (reproducible, no RNG state)."""
    return ((pid * 2654435761) % 10000) / 10000


# --------------------------------------------------------------------------- results and fallbacks

def _set_result(conn, pid, verdict, role, p, by):
    """Upsert the result only when it differs, so a repeated evaluation writes nothing."""
    cur = b_db.get_result(conn, pid)
    if cur and (cur["verdict"], cur["role"], cur["p"], cur["by"]) == (verdict, role, p, by):
        return
    b_db.upsert_result(conn, pid, verdict, role, p, by)


def _from_review(conn, pid, rv, by):
    _set_result(conn, pid, rv["verdict"], rv["role"], rv["p"], by)


def _fallback_prior(conn, pid, prior):
    """`accept:<role>` accepts at p=0.5; anything else rejects at p=0.4 (role None)."""
    kind, _, role = (prior or "").partition(":")
    if kind == "accept" and role in ROLES:
        _set_result(conn, pid, "accept", role, FALLBACK_ACCEPT_P, "fallback")
    else:
        _set_result(conn, pid, "reject", None, FALLBACK_REJECT_P, "fallback")


def _fallback_mean(conn, pid, reviews):
    """Mean p decides (>= 0.5 accepts); the role comes from the deepest-evidence accepting review."""
    mean = sum(r["p"] for r in reviews) / len(reviews)
    accepting = [r for r in reviews if r["verdict"] == "accept"]
    if mean >= 0.5 and accepting:
        best = max(accepting, key=lambda r: (LVL_RANK[r["lvl"]], r["p"]))
        _set_result(conn, pid, "accept", best["role"], mean, "fallback")
    else:
        _set_result(conn, pid, "reject", None, mean, "fallback")


# --------------------------------------------------------------------------- policy

def _agree(r, b):
    return r["verdict"] == b["verdict"] and (r["verdict"] == "reject" or r["role"] == b["role"])


def _need_final(conn, pid, reviews):
    """Queue the adjudication; once it can no longer be answered, decide by mean p."""
    task = b_db.get_task(conn, pid, "final")
    if task is None:
        b_db.add_task(conn, pid, "final", 0, "conflict")
    elif task["state"] in ("done", "failed"):
        _fallback_mean(conn, pid, reviews)


def _blind_reason(r, tier, prior, pid):
    """Why a blind review is required after first review `r`, or None to finalise now."""
    if r["verdict"] == "accept":
        return "confirm", 1
    if tier == 3:
        return "strong", 1
    if UNCERTAIN_LO <= r["p"] <= UNCERTAIN_HI:
        return "uncertain", 1
    if (prior or "").startswith("accept"):
        return "disagree", 1
    if tier == 2 and hash_frac(pid) < SAMPLE_RATE:
        return "sample", 2
    return None


def _after_review(conn, pid, r, tier, prior):
    blind = b_db.get_task(conn, pid, "blind")
    if blind is None:
        reason = _blind_reason(r, tier, prior, pid)
        if reason:
            b_db.add_task(conn, pid, "blind", reason[1], reason[0])
        else:
            _from_review(conn, pid, r, "review")
    elif blind["state"] in ("done", "failed"):
        _from_review(conn, pid, r, "review")


def _evaluate(conn, pid):
    """Apply the follow-up policy to one paper from the current table contents."""
    r = b_db.latest_review(conn, pid, "review")
    b = b_db.latest_review(conn, pid, "blind")
    f = b_db.latest_review(conn, pid, "final")
    if f:
        return _from_review(conn, pid, f, "final")
    tri = conn.execute("SELECT tier, prior FROM hunt_triage WHERE paper_id=?", (pid,)).fetchone()
    tier, prior = (tri["tier"], tri["prior"]) if tri else (0, None)
    if r and b:
        if _agree(r, b):
            return _set_result(conn, pid, r["verdict"], r["role"], (r["p"] + b["p"]) / 2, "consensus")
        return _need_final(conn, pid, [r, b])
    if b:
        if b["verdict"] == "accept":
            _need_final(conn, pid, [b])
            _escalate(conn, tier)
        return None
    if r:
        return _after_review(conn, pid, r, tier, prior)
    task = b_db.get_task(conn, pid, "review")
    if task and task["state"] == "failed":
        _fallback_prior(conn, pid, prior)
    return None


# --------------------------------------------------------------------------- audit sampling

def _stratum_rate(conn, tier):
    """Sampling rate of a stratum: the base rate, raised so small strata still yield MIN_AUDIT papers."""
    _key, _why, cond, base = STRATA[tier]
    n = conn.execute(
        "SELECT COUNT(*) FROM hunt_triage t JOIN hunt_paper p ON p.paper_id=t.paper_id WHERE p.dup_of IS NULL AND " + cond
    ).fetchone()[0]
    return max(base, min(1.0, MIN_AUDIT / n)) if n else 0.0


def _add_audits(conn, tier, rate, why, limit=-1):
    """Queue blind audits for unreviewed stratum papers whose hash is under `rate` (limit -1 = none)."""
    if limit == 0 or rate <= 0:
        return
    cond = STRATA[tier][2]
    with b_db.tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO hunt_task(paper_id,stage,state,prio,why) "
            "SELECT t.paper_id,'blind','todo',?,? FROM hunt_triage t JOIN hunt_paper p ON p.paper_id=t.paper_id "
            "WHERE p.dup_of IS NULL AND " + cond + " AND CAST(" + _HASH + " AS REAL)/10000.0 < ? AND "
            + _NO_TASK + " AND " + _NO_REVIEW + " AND " + _NO_RESULT +
            " ORDER BY " + _HASH + ", t.paper_id LIMIT ?",
            (AUDIT_PRIO, why, rate, limit))


def _escalate(conn, tier):
    """Once per stratum: audit at ESCALATION_FACTOR x the rate, capped at ESCALATION_CAP papers."""
    if tier not in STRATA or b_db.meta_get(conn, STRATA[tier][0]):
        return
    with b_db.tx(conn):
        b_db.meta_set(conn, STRATA[tier][0], 1)
        used = conn.execute(
            "SELECT COUNT(*) FROM hunt_task k JOIN hunt_triage t ON t.paper_id=k.paper_id WHERE k.stage='blind' "
            "AND k.why IN ('audit0','audit1','escalate') AND t.tier=?", (tier,)).fetchone()[0]
        rate = min(1.0, ESCALATION_FACTOR * _stratum_rate(conn, tier))
        _add_audits(conn, tier, rate, "escalate", max(0, ESCALATION_CAP - used))


# --------------------------------------------------------------------------- advance steps

def _resolve_failed(conn, now_ts):
    """Mark exhausted leases failed, then resolve every failed task that has no result yet."""
    b_db.fail_exhausted(conn, now_ts)
    pids = [r[0] for r in conn.execute(
        "SELECT DISTINCT paper_id FROM hunt_task WHERE state='failed' AND "
        "NOT EXISTS(SELECT 1 FROM hunt_result x WHERE x.paper_id=hunt_task.paper_id)")]
    _evaluate_all(conn, pids)


# tier-1 papers without an abstract wait until a deep fetch has covered one of their runs
_DEEP_DONE = ("EXISTS(SELECT 1 FROM hunt_run_paper rp JOIN hunt_run u ON u.name=rp.run "
              "WHERE rp.paper_id=t.paper_id AND u.deep_fetch_done=1)")


def _create_review_tasks(conn):
    with b_db.tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO hunt_task(paper_id,stage,state,prio,why) "
            "SELECT t.paper_id,'review','todo',"
            "CASE t.tier WHEN 3 THEN 0 WHEN 2 THEN 3 ELSE 4 END,"
            "CASE t.tier WHEN 3 THEN 'strong' WHEN 2 THEN 'gate' ELSE 'noabs' END "
            "FROM hunt_triage t JOIN hunt_paper p ON p.paper_id=t.paper_id "
            "WHERE t.tier>=1 AND p.dup_of IS NULL AND " + _NO_RESULT + " AND " + _NO_REVIEW +
            " AND (t.tier>=2 OR (" + _DEEP_DONE + " AND NOT " + _ABSTRACT + "))")


def _sample_audits(conn):
    for tier, (_key, why, _cond, _base) in STRATA.items():
        _add_audits(conn, tier, _stratum_rate(conn, tier), why)


def _evaluate_all(conn, pids):
    for i in range(0, len(pids), EVAL_BATCH):
        with b_db.tx(conn):
            for pid in pids[i:i + EVAL_BATCH]:
                _evaluate(conn, pid)


def _evaluate_pending(conn):
    _evaluate_all(conn, [r[0] for r in conn.execute(_PENDING)])


def advance(conn, now_ts=None):
    """Single entry point: expire leases, create tasks, apply the policy, report status.

    Returns {"open": {stage: n}, "leased", "failed", "finalized", "done", "tiers"}.
    """
    _resolve_failed(conn, now_ts)
    _create_review_tasks(conn)
    _sample_audits(conn)
    _evaluate_pending(conn)
    return _status(conn)


def _status(conn):
    open_ = {"review": 0, "blind": 0, "final": 0}
    leased = failed = 0
    for stage, state, n in conn.execute("SELECT stage, state, COUNT(*) FROM hunt_task GROUP BY 1,2"):
        if state in ("todo", "leased"):
            open_[stage] += n
        if state == "leased":
            leased += n
        elif state == "failed":
            failed += n
    finalized = conn.execute("SELECT COUNT(*) FROM hunt_result").fetchone()[0]
    has_triage = conn.execute("SELECT 1 FROM hunt_triage LIMIT 1").fetchone() is not None
    unresolved = conn.execute("SELECT EXISTS(%s)" % _PENDING).fetchone()[0]
    return {
        "open": open_, "leased": leased, "failed": failed, "finalized": finalized,
        "done": has_triage and not any(open_.values()) and not unresolved,
        "tiers": b_db.tier_histogram(conn),
    }


# --------------------------------------------------------------------------- submit

def on_submit(conn, review_row):
    """Apply the follow-up policy for the paper of a stored review (same transaction as the insert)."""
    with b_db.tx(conn):
        _evaluate(conn, review_row["paper_id"])


def _submit_one(conn, c_schema, rec, owner, stage):
    pid = rec.get("id") if isinstance(rec, dict) else None
    out = {"id": pid, "ok": False, "errors": [], "duplicate": False}
    try:
        with b_db.tx(conn):
            if isinstance(pid, int) and conn.execute(
                    "SELECT 1 FROM hunt_review WHERE paper_id=? AND stage=? AND by=?", (pid, stage, owner)).fetchone():
                out.update(ok=True, duplicate=True)
                return out
            errors = c_schema.validate_review(conn, rec, stage, owner)
            if errors:
                out["errors"] = errors
                return out
            row = c_schema.to_row(rec, stage, owner)
            b_db.add_review(conn, row)
            if not b_db.finish_task(conn, pid, stage, owner):
                raise ValueError("the '%s' task for paper %s is not leased to %s" % (stage, pid, owner))
            on_submit(conn, row)
        out["ok"] = True
    except Exception as e:  # one bad record must never affect the others
        out["errors"] = ["%s: %s" % (type(e).__name__, e)]
    return out


def submit_reviews(conn, records, owner, stage):
    """Validate and store each record in its own transaction.

    Returns one {"id", "ok", "errors", "duplicate"} per record. A resubmission by the same
    owner for (paper, stage) is ok+duplicate and changes nothing.
    """
    import c_schema
    return [_submit_one(conn, c_schema, rec, owner, stage) for rec in records]
