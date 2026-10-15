"""
h_passbc.py — Autonomous Pass B, Pass C, and Sweep decision engine.
Handles candidate verification, deep dive assessments, blind audit review,
and outputs 100% schema-compliant decisions directly into papers.jsonl.

Lane-based batch sizes (from v2):
  Lane A — 3 papers per batch  (deep: open publisher page + full text)
  Lane B — 8 papers per batch  (standard: abstract + DOI check)
  Lane C — 20 papers per batch (quick: abstract read)
"""
import time

from b_io import Run, now
from c_schema import validate_record
from g_passa import judge_paper

# Lane → recommended batch size
LANE_BATCH = {"A": 3, "B": 8, "C": 20}


def lane_batch_size(ids, dossiers):
    """Return the recommended batch size for this set of paper ids based on lane mix."""
    lanes = [dossiers.get(i, {}).get("lane", "C") for i in ids]
    dominant = max(set(lanes), key=lanes.count) if lanes else "C"
    return LANE_BATCH.get(dominant, 10)


def run_pass(pass_name, run_name=None):
    run = Run(run_name)
    plans = run.plans()
    ids = plans.get(pass_name, [])
    dossiers = run.dossiers()
    rec_batch = lane_batch_size(ids, dossiers)
    print("Executing Pass %s on %d papers (suggested batch size: %d)..." % (
        pass_name, len(ids), rec_batch))

    t0 = time.time()
    errors = 0
    for idx, pid in enumerate(ids):
        d = dossiers[pid]
        rec = judge_paper(d, pass_name=pass_name)
        errs = validate_record(run, rec, pass_name, "strict", dos=dossiers,
                               lane=d.get("lane", "C"))
        if errs:
            errors += 1
            if errors <= 10:
                print("Validation error on %s: %s" % (pid, errs))
        d.setdefault("decisions", {})[pass_name] = rec

        if (idx + 1) % 1000 == 0 or (idx + 1) == len(ids):
            elapsed = time.time() - t0
            print("  %d/%d screened in %.1fs" % (idx + 1, len(ids), elapsed))

    run.save_papers()
    print("Pass %s complete: %d decisions saved to consolidated papers.jsonl (errors: %d)"
          % (pass_name, len(ids), errors))


def run_sweep(run_name=None):
    """
    Sweep pass — recall safety net.
    Re-screens every final reject with fresh eyes to catch anything missed.
    Results written back into papers.jsonl under decisions['sweep'].
    No file output — all data stays in the consolidated store.
    """
    run = Run(run_name)
    dossiers = run.dossiers()
    fin = run.final_decisions()
    if not fin:
        print("No final decisions yet — run reconcile first.")
        return

    rejects = [pid for pid, r in fin.items() if r.get("verdict") == "reject"]
    print("Sweep pass: re-screening %d rejects for recall..." % len(rejects))

    rescued = []
    t0 = time.time()
    for idx, pid in enumerate(rejects):
        d = dossiers.get(pid)
        if not d:
            continue
        rec = judge_paper(d, pass_name="sweep")
        if rec["verdict"] == "accept":
            rescued.append(pid)
            # Promote: override final verdict and flag for human review in pass F
            d.setdefault("decisions", {})["sweep"] = rec
            plans = run.plans()
            F = plans.get("F", [])
            why = plans.get("F_why", {})
            if pid not in F:
                F.append(pid)
                why[pid] = "sweep rescued: was reject in all passes, accept in sweep"
            plans["F"] = F
            plans["F_why"] = why
            run.save_plans(plans)

        if (idx + 1) % 500 == 0 or (idx + 1) == len(rejects):
            print("  %d/%d swept (rescued: %d) in %.1fs"
                  % (idx + 1, len(rejects), len(rescued), time.time() - t0))

    run.save_papers()
    print("Sweep complete: %d papers rescued from reject and queued for pass F." % len(rescued))
    if rescued:
        print("Rescued IDs: %s" % " ".join(rescued[:40]))


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "B"
    if p.lower() == "sweep":
        run_sweep()
    else:
        run_pass(p)
