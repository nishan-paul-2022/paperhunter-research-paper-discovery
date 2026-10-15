"""Reconcile passes and export the filtered CSVs + report."""
import csv
from collections import Counter
from . import io

ORIG = ["dblp_key", "title", "authors", "year", "venue", "ee", "url"]
NEW = ["doi", "arxiv_url", "tier", "roles", "related_type", "primary_contribution", "publication_status",
       "c1_llm_agent", "c2_offensive", "c3_blackbox", "c4_multistep", "evidence_level", "confidence",
       "evidence", "reason", "flags", "decided_by", "abstract_source"]
TIER_ORDER = {"core": 0, "related": 1, "needs_manual": 2, "reject": 3}
ROLE_ORDER = ["baseline", "baseline_candidate", "survey", "benchmark", "evaluation", "background"]

def reconcile(c):
    """Later pass wins (sweep > audit > screen), except: a low-confidence audit/sweep reject of a paper the
    screen kept becomes related+flag 'contested' (inclusive on ties)."""
    out = {}
    for p in c.papers:
        i = p["id"]; scr = c.dec["screen"].get(i)
        if not scr: continue
        final, by = scr, "screen"
        for ps in ("audit", "sweep"):
            d = c.dec[ps].get(i)
            if not d: continue
            if scr["tier"] in ("core", "related") and d["tier"] == "reject" and float(d["confidence"]) < 0.7 and by == "screen":
                keep = dict(scr); keep["tier"] = "related"; keep["related_type"] = scr.get("related_type") or "offense_adjacent_other"
                keep["roles"] = scr["roles"] or ["background"]
                keep["flags"] = list(scr.get("flags") or []) + ["contested"]
                final, by = keep, ps + "(contested)"
            else:
                final, by = d, ps
        f = dict(final); f["decided_by"] = by; out[i] = f
    return out

def row(c, i, d):
    p, e = c.by[i], c.enr.get(i, {})
    cr = d["criteria"]
    ab_src = "provided" if e.get("abstract") else ("web" if d.get("abstract_web") else "none")
    return {"dblp_key": p["dblp_key"], "title": p["title"], "authors": p["authors"], "year": p["year"], "venue": p["venue"],
            "ee": p["ee"], "url": p["url"], "doi": p["doi"], "arxiv_url": d.get("arxiv_url") or e.get("arxiv_url", ""),
            "tier": d["tier"], "roles": ";".join(sorted(d["roles"], key=lambda r: ROLE_ORDER.index(r))),
            "related_type": d.get("related_type", ""), "primary_contribution": d["primary_contribution"],
            "publication_status": d["publication_status"], "c1_llm_agent": cr["llm_agent"], "c2_offensive": cr["offensive_security"],
            "c3_blackbox": cr["black_box"], "c4_multistep": cr["long_horizon"], "evidence_level": d["evidence_level"],
            "confidence": d["confidence"], "evidence": d.get("evidence", ""), "reason": d["reason"],
            "flags": ";".join(d.get("flags") or []), "decided_by": d["decided_by"], "abstract_source": ab_src}

def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ORIG + NEW); w.writeheader(); w.writerows(rows)

def finalize(c, allow_incomplete=False, out=print):
    fin = reconcile(c)
    missing = [p["id"] for p in c.papers if p["id"] not in fin]
    if missing and not allow_incomplete:
        out(f"finalize: {len(missing)} papers have no screen decision (e.g. {missing[:5]}). Finish screening or use --allow-incomplete."); return 2
    stem, od = c.d.name, c.d / "out"
    rows = []
    for i, d in fin.items():
        r = row(c, i, d); r["_k"] = (TIER_ORDER[d["tier"]], min([ROLE_ORDER.index(x) for x in d["roles"]] or [9]), -float(d["confidence"]), c.by[i]["row"]); rows.append(r)
    rows.sort(key=lambda r: r["_k"])
    for r in rows: r.pop("_k")
    kept = [r for r in rows if r["tier"] != "reject"]
    _write(od / f"{stem}_filtered.csv", kept)
    _write(od / f"{stem}_all_decisions.csv", rows)
    for role in ("baseline", "survey", "benchmark", "evaluation"):
        _write(od / f"{stem}_role_{role}.csv", [r for r in kept if role in r["roles"].split(";")])
    # report
    tiers = Counter(r["tier"] for r in rows); roles = Counter(x for r in kept for x in r["roles"].split(";") if x)
    lv = Counter(r["evidence_level"] for r in rows)
    L = [f"# Paper-hunter report: {stem}", "", f"- papers decided: {len(rows)} / {len(c.papers)}",
         f"- tiers: {dict(tiers)}", f"- roles among kept: {dict(roles)}", f"- evidence levels: {dict(lv)}",
         f"- contested: {sum(1 for r in kept if 'contested' in r['flags'])}",
         f"- low-confidence (<0.6) kept: {sum(1 for r in kept if float(r['confidence']) < 0.6)}",
         f"- title_only rejects: {sum(1 for r in rows if r['tier']=='reject' and r['evidence_level']=='title_only')}",
         f"- audited: {len(c.dec['audit'])}, swept: {len(c.dec['sweep'])}", ""]
    for role in ("baseline", "baseline_candidate", "survey", "benchmark", "evaluation", "background"):
        sub = [r for r in kept if role in r["roles"].split(";")]
        if sub:
            L.append(f"## {role} ({len(sub)})")
            for r in sub: L.append(f"- [{r['tier']}] {r['title']} ({r['venue']}, {r['year']}) conf={r['confidence']} {r['doi']}")
            L.append("")
    nm = [r for r in kept if r["tier"] == "needs_manual"]
    if nm:
        L.append(f"## needs_manual ({len(nm)})")
        for r in nm: L.append(f"- {r['title']} | flags: {r['flags']} | {r['reason']}")
    (od / "report.md").write_text("\n".join(L), encoding="utf-8")
    out(f"finalize: kept {len(kept)} of {len(rows)} -> {od/(stem+'_filtered.csv')}")
    out(f"          tiers {dict(tiers)}  roles {dict(roles)}")
    return 0
