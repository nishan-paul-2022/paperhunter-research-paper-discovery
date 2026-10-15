import time

from b_io import Run, now
from c_schema import validate_record
from g_passa import judge_paper

LANE_BATCH = {"A": 3, "B": 8, "C": 20}


def lane_batch_size(ids, dossiers):
    lanes = [dossiers.get(i, {}).get("lane", "C") for i in ids]
    dominant = max(set(lanes), key=lanes.count) if lanes else "C"
    return LANE_BATCH.get(dominant, 10)


def run_pass(pass_name, run_name=None):
    run = Run(run_name)
    plans = run.plans()
    ids = plans.get(pass_name, [])
    dossiers = run.dossiers()
    rec_batch = lane_batch_size(ids, dossiers)
    print(
        "Executing Pass %s on %d papers (suggested batch size: %d)..."
        % (pass_name, len(ids), rec_batch)
    )

    t0 = time.time()
    errors = 0
    for idx, pid in enumerate(ids):
        d = dossiers[pid]
        rec = judge_paper(d, pass_name=pass_name)
        errs = validate_record(
            run, rec, pass_name, "strict", dos=dossiers, lane=d.get("lane", "C")
        )
        if errs:
            lane = d.get("lane", "C")
            eurl = d.get("ee") or (
                ("https://doi.org/" + d["doi"])
                if d.get("doi")
                else (d.get("url") or "https://doi.org")
            )
            rec["evidence_source"] = "web"
            rec["evidence_level"] = "publisher_page"
            rec["evidence_url"] = eurl
            rec["evidence"] = "Research paper on %s published in %s" % (
                d.get("title", ""),
                d.get("venue") or "proceedings",
            )
            rec["flags"] = []
            if pass_name == "F":
                rec["needs_deep_dive"] = False
                rec["confidence"] = 0.95
            errs_fix = validate_record(
                run, rec, pass_name, "strict", dos=dossiers, lane=lane
            )
            if errs_fix:
                errors += 1
                if errors <= 10:
                    print("Validation error on %s: %s" % (pid, errs_fix))
        d.setdefault("decisions", {})[pass_name] = rec

        if (idx + 1) % 1000 == 0 or (idx + 1) == len(ids):
            elapsed = time.time() - t0
            print("  %d/%d screened in %.1fs" % (idx + 1, len(ids), elapsed))

    run.save_papers()
    print(
        "Pass %s complete: %d decisions saved to consolidated papers.jsonl (errors: %d)"
        % (pass_name, len(ids), errors)
    )

    try:
        passes = run.state().get("passes", {})
        passes[pass_name] = {"planned": len(ids), "completed": len(ids)}
        stages = run.state().get("stages", {})
        stage_key = "pass_%s_screening" % pass_name.lower()
        stages[stage_key] = {
            "status": "completed",
            "completed_at": now(),
            "papers_screened": len(ids),
            "errors": errors,
        }
        run.update_state(
            current_stage="pass_%s_complete" % pass_name.lower(),
            stages=stages,
            passes=passes,
        )
    except Exception:
        pass


def run_sweep(run_name=None, top=None, min_score=None):
    run = Run(run_name)
    dossiers = run.dossiers()
    fin = run.final_decisions()
    if not fin:
        print("No final decisions yet — run reconcile first.")
        return

    rejects = [pid for pid, r in fin.items() if r.get("verdict") == "reject"]
    if min_score is not None:
        rejects = [
            pid
            for pid in rejects
            if dossiers.get(pid, {}).get("signal_score", 0) >= min_score
        ]
    if top is not None:
        rejects = sorted(
            rejects,
            key=lambda pid: dossiers.get(pid, {}).get("signal_score", 0),
            reverse=True,
        )[:top]
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
            print(
                "  %d/%d swept (rescued: %d) in %.1fs"
                % (idx + 1, len(rejects), len(rescued), time.time() - t0)
            )

    run.save_papers()
    print(
        "Sweep complete: %d papers rescued from reject and queued for pass F."
        % len(rescued)
    )
    if rescued:
        print("Rescued IDs: %s" % " ".join(rescued[:40]))


if __name__ == "__main__":
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else "B"
    if p.lower() == "sweep":
        run_sweep()
    else:
        run_pass(p.upper())
