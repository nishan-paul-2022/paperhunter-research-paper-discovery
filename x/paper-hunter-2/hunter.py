#!/usr/bin/env python3
"""paper-hunter v2 CLI (stdlib only). The AI agent runs these; humans don't have to.
  prep      <csv>                  init + enrich + signals (+ deep enrich for lanes A/B)
  init|enrich|signals <csv>        individual stages
  next      <csv> [--pass screen|audit|sweep]     print the next work packet (JSON)
  submit    <csv> [--pass P] (--file F | stdin)   validate + store decisions (JSON array)
  status    <csv>
  audit     <csv>                  select papers for the adversarial audit pass
  sweep     <csv> [--recheck ID..] final-reject recall sweep list / queue rechecks
  finalize  <csv> [--allow-incomplete]            reconcile + write out/*.csv + report.md
  selftest  <csv>                  offline smoke test on a temp copy (no network)"""
import argparse, json, sys, shutil, csv as _csv
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ph import io

def _ctx(a):
    from ph import queue
    d = io.run_dir(a.csv)
    if not (d / "papers.jsonl").exists():
        print("ERROR: run `hunter.py prep <csv>` first", file=sys.stderr); sys.exit(2)
    return queue.Ctx(d)

def cmd_init(a):
    papers, rep = io.load_papers(a.csv)
    d = io.run_dir(a.csv); pp = d / "papers.jsonl"
    if pp.exists() and not a.force:
        old = {p["id"] for p in io.read_jsonl(pp)}
        if old != {p["id"] for p in papers}:
            print("ERROR: papers.jsonl exists and differs from the CSV. Use --force to rebuild "
                  "(this does NOT delete decisions.jsonl).", file=sys.stderr); return 2
        print(f"init: already initialised ({len(old)} papers)."); return 0
    io.write_jsonl(pp, papers)
    (d / "init_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(f"init: {rep['kept']}/{rep['rows']} papers -> {pp} (duplicates {len(rep['duplicates'])}, no DOI {rep['no_doi']})")
    return 0

def cmd_signals(a):
    from ph import signals
    d = io.run_dir(a.csv); papers = io.read_jsonl(d / "papers.jsonl")
    if not papers: print("ERROR: run init first", file=sys.stderr); return 2
    sig = signals.compute(papers, io.latest_by_id(io.read_jsonl(d / "enrich.jsonl")))
    io.write_jsonl(d / "signals.jsonl", sig)
    print(f"signals: lanes {dict(sorted(Counter(s['lane'] for s in sig).items()))} basis {dict(Counter(s['text_basis'] for s in sig))}")
    if a.show:
        by = {p["id"]: p for p in papers}
        for s in sorted(sig, key=lambda x: -x["score"])[: a.show]: print(f"{s['lane']} {s['score']:5.1f} {by[s['id']]['title'][:100]}")
    return 0

def cmd_enrich(a):
    from ph import enrich
    d = io.run_dir(a.csv); papers = io.read_jsonl(d / "papers.jsonl")
    if not papers: print("ERROR: run init first", file=sys.stderr); return 2
    enrich.run(papers, d, io.latest_by_id(io.read_jsonl(d / "signals.jsonl")), retry=a.retry,
               refresh_deep=a.deep, all_deep=a.all_deep)
    return 0

def cmd_prep(a):
    if cmd_init(a): return 2
    a.retry = a.deep = a.all_deep = False; a.show = 0
    cmd_enrich(a); cmd_signals(a)
    a.deep = True; cmd_enrich(a); cmd_signals(a)   # deep pass (S2 + arXiv) for lane A/B, then recompute lanes with abstracts
    print("prep: done. Next: `hunter.py status <csv>` then loop `next` / `submit`.")
    return 0

def cmd_next(a):
    from ph import queue; return queue.cmd_next(_ctx(a), a.pass_)
def cmd_submit(a):
    from ph import queue
    text = Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()
    return queue.cmd_submit(_ctx(a), a.pass_, text)
def cmd_status(a):
    from ph import queue; return queue.cmd_status(_ctx(a)) or 0
def cmd_audit(a):
    from ph import queue; return queue.cmd_audit(_ctx(a))
def cmd_sweep(a):
    from ph import queue; return queue.cmd_sweep(_ctx(a), a.recheck)
def cmd_finalize(a):
    from ph import export; return export.finalize(_ctx(a), a.allow_incomplete)

def cmd_selftest(a):
    """Offline smoke test: 12 real rows + fake decisions through validate/queue/export. Cleans up after itself."""
    from ph import queue, schema, export
    rows = io.read_input_csv(a.csv)[:12]
    tmp = Path("/tmp/ph_selftest.csv")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    a2 = argparse.Namespace(csv=str(tmp), force=True, show=0, retry=False, deep=False, all_deep=False)
    d = io.run_dir(tmp); shutil.rmtree(d, ignore_errors=True); d = io.run_dir(tmp)
    cmd_init(a2); cmd_signals(a2)
    c = queue.Ctx(d)
    def dec(i, tier="reject", **kw):
        base = {"id": i, "tier": tier, "roles": [], "primary_contribution": "other", "publication_status": "published",
                "criteria": {k: "no" for k in schema.CRIT_KEYS}, "evidence_level": "abstract", "reason": "Unrelated to offensive LLM agents.",
                "confidence": 0.9, "flags": [], "sources_checked": ["x"]}
        base.update(kw); return base
    ids = [p["id"] for p in c.papers]
    batch = [dec(i) for i in ids[1:]]
    batch.append(dec(ids[0], "core", roles=["baseline"], primary_contribution="agent_system", evidence_level="full_text",
                     criteria={k: "yes" for k in schema.CRIT_KEYS}, evidence="agent autonomously performs penetration testing"))
    bad = dec(ids[1], "core", roles=["baseline"]); batch_bad = [bad]
    assert queue.cmd_submit(c, "screen", json.dumps(batch_bad), out=sys.stderr) == 1, "validator must reject bad baseline"
    assert queue.cmd_submit(c, "screen", json.dumps(batch), out=sys.stderr) == 0
    assert not queue.pending(c, "screen")
    queue.cmd_audit(c); c = queue.Ctx(d)
    queue.cmd_sweep(c, None, out=open("/dev/null", "w"))
    assert export.finalize(c) == 0
    assert (d / "out" / "ph_selftest_filtered.csv").exists()
    shutil.rmtree(d, ignore_errors=True); tmp.unlink()
    print("selftest: OK"); return 0

CMDS = {"prep": cmd_prep, "init": cmd_init, "enrich": cmd_enrich, "signals": cmd_signals, "next": cmd_next,
        "submit": cmd_submit, "status": cmd_status, "audit": cmd_audit, "sweep": cmd_sweep,
        "finalize": cmd_finalize, "selftest": cmd_selftest}

def main():
    ap = argparse.ArgumentParser(prog="hunter.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=sorted(CMDS)); ap.add_argument("csv", help="input CSV path")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--retry", action="store_true", help="enrich: refetch papers without abstract")
    ap.add_argument("--deep", action="store_true", help="enrich: add Semantic Scholar+arXiv for lane A/B")
    ap.add_argument("--all-deep", action="store_true", help="enrich: deep for every paper")
    ap.add_argument("--pass", dest="pass_", default="screen", choices=["screen", "audit", "sweep"])
    ap.add_argument("--file"); ap.add_argument("--allow-incomplete", action="store_true")
    ap.add_argument("--recheck", nargs="*", default=None, help="sweep: full ids or last-5-digit ids")
    a = ap.parse_args()
    if a.cmd == "sweep" and a.recheck:
        stem = Path(a.csv).stem
        a.recheck = [r if r.startswith(stem) else f"{stem}_{r.zfill(5)}" for r in a.recheck]
    sys.exit(CMDS[a.cmd](a))

if __name__ == "__main__":
    main()
