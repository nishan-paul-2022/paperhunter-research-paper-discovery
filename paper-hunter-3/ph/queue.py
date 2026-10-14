"""Work queue: packets for the agent (next), validated intake (submit), audit selection, sweep, status."""
import json, hashlib, sys, time
from . import io, schema

BATCH = {"screen": {"A": 3, "B": 8, "C": 20}, "audit": {"A": 3, "B": 5, "C": 8}, "sweep": {"A": 5, "B": 8, "C": 12}}
PASSES = ("screen", "audit", "sweep")

class Ctx:
    def __init__(self, d):
        self.d = d
        self.papers = io.read_jsonl(d / "papers.jsonl")
        self.by = {p["id"]: p for p in self.papers}
        self.sig = io.latest_by_id(io.read_jsonl(d / "signals.jsonl"))
        self.enr = io.latest_by_id(io.read_jsonl(d / "enrich.jsonl"))
        rows = io.read_jsonl(d / "decisions.jsonl")
        self.dec = {ps: io.latest_by_id([r for r in rows if r.get("pass") == ps]) for ps in PASSES}
    def lane(self, i): return self.sig.get(i, {}).get("lane", "B")

def _sel_path(d, ps): return d / f"{ps}_queue.json"
def selection(c, ps):
    if ps == "screen": return [p["id"] for p in c.papers]
    sp = _sel_path(c.d, ps)
    return json.loads(sp.read_text())["ids"] if sp.exists() else []

def pending(c, ps):
    ids = [i for i in selection(c, ps) if i not in c.dec[ps]]
    order = {"A": 0, "B": 1, "C": 2}
    return sorted(ids, key=lambda i: (order[c.lane(i)], -c.sig.get(i, {}).get("score", 0), c.by[i]["row"]))

def todo_hints(c, i, ps):
    e, lane, t = c.enr.get(i, {}), c.lane(i), []
    if not e.get("abstract"):
        t.append("NO ABSTRACT: get it via DOI landing page -> publisher page -> Semantic Scholar/OpenAlex/Google Scholar/arXiv. "
                 "Title match must be exact. Put it in 'abstract_web' when you submit." + ("" if c.by[i]["doi"] else " No DOI in CSV: web-search the exact title."))
    if lane == "A": t.append("DEEP: open publisher page; search the web for arXiv/preprint + code repo; read full text (intro+method+evaluation) unless the abstract settles all 4 criteria and the contribution type.")
    elif lane == "B": t.append("STANDARD: read abstract carefully; open DOI/publisher page if any criterion or the contribution type is unclear; escalate to full text if still unclear.")
    else: t.append("QUICK: read abstract. Escalate to STANDARD if it mentions LLM/agent/security/attack/exploit/pentest/CTF/red team terms.")
    return t

def packet_paper(c, i, ps):
    p, s, e = c.by[i], c.sig.get(i, {}), c.enr.get(i, {})
    o = {"id": i, "title": p["title"], "authors": p["authors"], "year": p["year"], "venue": p["venue"],
         "doi_url": p["doi_url"], "ee": p["ee"], "dblp_key": p["dblp_key"], "lane": c.lane(i), "score": s.get("score"),
         "hits": s.get("hits", {}), "role_hints": s.get("role_hints", []), "seed_match": s.get("seed_match", ""),
         "abstract": e.get("abstract", ""), "abstract_source": e.get("abstract_source", ""),
         "arxiv_url": e.get("arxiv_url", ""), "pdf_url": e.get("pdf_url", ""), "resolved_venue": e.get("resolved_venue", ""),
         "resolved_type": e.get("resolved_type", ""), "tasks": todo_hints(c, i, ps)}
    if ps != "screen":
        for q in ("screen", "audit"):
            pr = c.dec[q].get(i)
            if pr and not (q == "audit" and ps == "audit"):
                o["prior_" + q] = {k: pr.get(k) for k in ("tier", "roles", "primary_contribution", "criteria", "evidence_level", "reason", "confidence", "flags")}
        o["audit_reasons"] = _audit_reasons(c.d).get(i, [])
        o["tasks"].insert(0, "AUDIT: be adversarial. Try to REFUTE the prior verdict with fresh evidence (re-open sources; do not trust the prior reason). Same schema; set your own verdict.")
    return o

def _audit_reasons(d):
    p = _sel_path(d, "audit")
    return json.loads(p.read_text()).get("reasons", {}) if p.exists() else {}

def cmd_next(c, ps, out=sys.stdout):
    pd = c.d / "packets"; pd.mkdir(exist_ok=True)
    st_path = c.d / "queue_state.json"
    st = json.loads(st_path.read_text()) if st_path.exists() else {}
    pend = pending(c, ps)
    if not pend:
        print(f"next: pass '{ps}' has no pending papers." + (" Run: hunter.py audit/sweep/finalize as per prompt.md" if ps == "screen" else ""), file=out); return 0
    lane = c.lane(pend[0]); size = BATCH[ps][lane]
    ids = [i for i in pend if c.lane(i) == lane][:size]
    seq = st.get(ps, 0) + 1; st[ps] = seq; st_path.write_text(json.dumps(st))
    pkt = {"pass": ps, "packet": f"{ps}_{seq:04d}", "lane": lane, "remaining_after": len(pend) - len(ids),
           "papers": [packet_paper(c, i, ps) for i in ids]}
    (pd / f"{pkt['packet']}.json").write_text(json.dumps(pkt, ensure_ascii=False, indent=1))
    print(json.dumps(pkt, ensure_ascii=False, indent=1), file=out)
    return 0

def parse_submission(text):
    text = text.strip()
    if text.startswith("```"): text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        o = json.loads(text)
    except ValueError:
        o = [json.loads(l) for l in text.splitlines() if l.strip()]
    if isinstance(o, dict): o = o.get("decisions", [o])
    return o

def cmd_submit(c, ps, text, out=sys.stdout):
    try: items = parse_submission(text)
    except ValueError as e:
        print(f"submit: REJECTED, invalid JSON: {e}", file=out); return 2
    ok, bad = 0, 0
    allowed = set(selection(c, ps))
    for dsn in items:
        i = dsn.get("id")
        if i not in c.by: print(f"  ! {i}: unknown id", file=out); bad += 1; continue
        if i not in allowed: print(f"  ! {i}: not in '{ps}' selection", file=out); bad += 1; continue
        ab = dsn.get("abstract_web") or c.enr.get(i, {}).get("abstract", "")
        E, W = schema.validate(dsn, c.lane(i), ab)
        if E:
            bad += 1; print(f"  ! {i} REJECTED:", "; ".join(E), file=out); continue
        p = c.by[i]
        rec = dict(dsn); rec.update({"pass": ps, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lane": c.lane(i),
                                     "title": p["title"], "authors": p["authors"], "year": p["year"], "venue": p["venue"],
                                     "doi": p["doi"], "url": p["url"], "warnings": W})
        if rec.get("abstract_web"): rec["abstract_web"] = rec["abstract_web"][:2500]
        io.append_jsonl(c.d / "decisions.jsonl", rec); c.dec[ps][i] = rec; ok += 1
        if W: print(f"  ~ {i}: " + "; ".join(W), file=out)
    print(f"submit: accepted {ok}, rejected {bad}. Pass '{ps}' pending: {len(pending(c, ps))}. "
          + ("Fix the rejected ones and resubmit just those." if bad else "Run `next` again."), file=out)
    return 1 if bad else 0

def cmd_audit(c, out=sys.stdout):
    import random
    rnd = random.Random(7); reasons = {}
    def add(i, r): reasons.setdefault(i, []).append(r)
    scr = c.dec["screen"]
    if len(scr) < len(c.papers):
        print(f"audit: screen incomplete ({len(scr)}/{len(c.papers)}). Finish screening first.", file=out); return 2
    for i, d in scr.items():
        lane, s = c.lane(i), c.sig.get(i, {})
        if d["tier"] in ("core", "related", "needs_manual"): add(i, f"kept:{d['tier']}")
        if d["tier"] == "reject":
            if float(d["confidence"]) < 0.7: add(i, "low_confidence_reject")
            if lane == "A": add(i, "lane_A_reject")
            if lane == "B" and (s.get("score", 0) >= 25 or s.get("role_hints") or d["evidence_level"] == "title_only"): add(i, "lane_B_strong_reject")
            if lane == "C" and (s.get("hits") and set(s["hits"]) & {"agent", "offense", "strong_offense"}): add(i, "lane_C_with_hits")
            if lane == "C" and rnd.random() < 0.15: add(i, "random_15pct_laneC")
            if s.get("seed_fuzzy", 0) >= 0.45 or s.get("typo_hits"): add(i, "fuzzy_near_miss")
            if d["evidence_level"] == "title_only": add(i, "title_only_reject")
    ids = sorted(reasons, key=lambda i: c.by[i]["row"])
    _sel_path(c.d, "audit").write_text(json.dumps({"ids": ids, "reasons": reasons}, indent=1))
    print(f"audit: selected {len(ids)} of {len(c.papers)} papers -> run `next --pass audit` until empty.", file=out); return 0

def final_rejects(c):
    from .export import reconcile
    return [(i, d) for i, d in reconcile(c).items() if d["tier"] == "reject"]

def cmd_sweep(c, recheck, out=sys.stdout):
    if recheck:
        ids = [i for i in recheck if i in c.by]
        _sel_path(c.d, "sweep").write_text(json.dumps({"ids": ids, "reasons": {}}))
        print(f"sweep: {len(ids)} papers queued -> run `next --pass sweep` until empty.", file=out); return 0
    rej = final_rejects(c)
    lines = ["# Recall sweep: final REJECTS (id | title | reason). Read ALL lines with fresh eyes.",
             "# Then run: hunter.py sweep <csv> --recheck ID ID ...   (ids that might be wrong; or none).", ""]
    for i, d in sorted(rej, key=lambda x: c.by[x[0]]["row"]):
        lines.append(f"{i[-5:]} | {c.by[i]['title'][:130]} | {' '.join(str(d['reason']).split()[:14])}")
    (c.d / "sweep_list.md").write_text("\n".join(lines))
    print("\n".join(lines), file=out)
    print(f"\n(ids above are the last 5 digits; full id = {c.d.name}_<5 digits>). List saved to {c.d/'sweep_list.md'}", file=out); return 0

def cmd_status(c, out=sys.stdout):
    from collections import Counter
    print(f"papers: {len(c.papers)}  enriched-with-abstract: {sum(1 for e in c.enr.values() if e.get('abstract'))}", file=out)
    print("lanes:", dict(sorted(Counter(c.lane(p['id']) for p in c.papers).items())), file=out)
    for ps in PASSES:
        sel = selection(c, ps)
        if ps != "screen" and not sel: print(f"{ps}: not started", file=out); continue
        done = sum(1 for i in sel if i in c.dec[ps])
        tiers = Counter(d["tier"] for d in c.dec[ps].values())
        print(f"{ps}: {done}/{len(sel)} done; tiers {dict(tiers)}", file=out)
    rem = pending(c, "screen")
    print("screen pending by lane:", dict(Counter(c.lane(i) for i in rem)), file=out)
