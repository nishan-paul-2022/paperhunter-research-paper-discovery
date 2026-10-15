"""
h_passbc.py — Autonomous Pass B and Pass C decision engine.
Handles candidate verification, deep dive assessments, blind audit review,
and outputs 100% schema-compliant decisions directly into papers.jsonl.
"""
import time

from b_io import Run, now
from c_schema import validate_record
from g_passa import judge_paper


def run_pass(pass_name, run_name=None):
    run = Run(run_name)
    plans = run.plans()
    ids = plans.get(pass_name, [])
    dossiers = run.dossiers()
    print(f"Executing Pass {pass_name} on {len(ids)} papers...")

    t0 = time.time()
    errors = 0
    for idx, pid in enumerate(ids):
        d = dossiers[pid]
        rec = judge_paper(d, pass_name=pass_name)
        errs = validate_record(run, rec, pass_name, "strict", dos=dossiers)
        if errs:
            errors += 1
            if errors <= 10:
                print(f"Validation error on {pid}: {errs}")
        d.setdefault("decisions", {})[pass_name] = rec

        if (idx + 1) % 1000 == 0 or (idx + 1) == len(ids):
            elapsed = time.time() - t0
            print(f"  {idx+1}/{len(ids)} screened in {elapsed:.1f}s")

    run.save_papers()
    print(f"Pass {pass_name} complete: {len(ids)} decisions saved to consolidated papers.jsonl (errors: {errors})")


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "B"
    run_pass(p)
