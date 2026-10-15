"""
e_enrich.py — per-paper enrichment (Crossref, OpenAlex, S2, landing page, arXiv)
and dossier/signal building for paper-hunter.

All enrichment data and signals are saved directly into the consolidated
papers.jsonl store. No separate dossier, database, or text files are created.

Network probe: tests 6 papers before the full batch; aborts cleanly if all fail.
Lane-gated deep enrichment: with --deep, only lane A/B papers get S2+arXiv.
"""
import math, os, re, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from b_io      import MIN_ABSTRACT, now
from d_network import (p_crossref, p_openalex, p_landing, p_arxiv,
                      s2_batch, openalex_batch, BROWSER_UA)
from f_signals import compute_signals


# ── single-paper fetch ────────────────────────────────────────────────────────

def enrich_one(run, p, s2rec, oarec=None, deep=True):
    cached = run.get_enrichment(p["id"])
    if cached and cached.get("done"):
        return p["id"], "cached"

    c   = {"id": p["id"], "errors": []}
    doi, title, year = p.get("doi"), p["title"], p.get("year")
    lane = p.get("lane", "C")
    try:
        c["s2"] = s2rec
        c["openalex"] = oarec or (p_openalex(doi=doi) if doi else p_openalex(title=title, year=year))
        have = any(len((c.get(k) or {}).get("abstract", "")) >= MIN_ABSTRACT
                   for k in ("openalex", "s2"))
        if not have:
            c["crossref"] = p_crossref(doi=doi) if doi else p_crossref(title=title, year=year)
            have = have or len((c.get("crossref") or {}).get("abstract", "")) >= MIN_ABSTRACT
        else:
            c["crossref"] = None

        landing_url = p.get("ee") or p.get("url") or ""
        if landing_url and "doi.org" in landing_url:
            landing_url = ""
        if landing_url and not landing_url.startswith("http"):
            landing_url = ""
        # Landing page: always try; not gated on lane (gives abstracts for all)
        if landing_url and not have:
            c["landing"] = p_landing(landing_url)
            have = have or len((c.get("landing") or {}).get("abstract", "")) >= MIN_ABSTRACT
        else:
            c["landing"] = None

        # arXiv: always try if no abstract yet, or if landing is arxiv
        c["arxiv"] = None
        do_arxiv = (not have) or ("arxiv" in (landing_url or "").lower())
        # Lane-gated deep enrichment: if deep=False, only fetch arXiv for lane A/B
        if do_arxiv and (deep or lane in ("A", "B")):
            arxiv_result = p_arxiv(title)
            if arxiv_result:
                c["arxiv"] = arxiv_result

    except Exception as e:
        c["errors"].append("%s: %s" % (type(e).__name__, e))
    c["done"]       = True
    c["fetched_at"] = now()

    run.save_enrichment(p["id"], c)
    return p["id"], "fetched"


# ── dossier merge ─────────────────────────────────────────────────────────────

def _merge_one(p, c, manual):
    d = p
    cr = (c.get("crossref") or {}) if c else {}
    oa = (c.get("openalex") or {}) if c else {}
    s2 = (c.get("s2") or {}) if c else {}
    ld = (c.get("landing") or {}) if c else {}
    ax = (c.get("arxiv") or {}) if c else {}

    cands = [(src, re.sub(r"\s+", " ", (v or "")).strip())
             for src, v in [("crossref", cr.get("abstract", "")),
                             ("openalex", oa.get("abstract", "")),
                             ("semanticscholar", s2.get("abstract", "")),
                             ("landing_page", ld.get("abstract", "")),
                             ("arxiv", ax.get("abstract", ""))]]
    good  = [(k, v) for k, v in cands if len(v) >= MIN_ABSTRACT]
    d["abstract_candidates"] = {k: len(v) for k, v in cands if v}
    if manual and manual.get("abstract"):
        d["abstract"], d["abstract_source"] = manual["abstract"], manual.get("source", "manual_web")
    elif good:
        best = good[0]
        for k, v in good[1:]:
            if len(v) > 1.35 * len(best[1]):
                best = (k, v)
        d["abstract"], d["abstract_source"] = best[1], best[0]
    else:
        short = [(k, v) for k, v in cands if v]
        d["abstract"], d["abstract_source"] = ("", "none") if not short else (short[0][1], short[0][0])
        if short:
            d["abstract_source"]  = "none"
            d["abstract_snippet"] = short[0][1]
    kws  = list(dict.fromkeys((oa.get("keywords") or []) +
                               (ld.get("keywords") or []) +
                               (cr.get("subjects") or [])))
    d["keywords"]  = [k for k in kws if k][:20]
    d["topics"]    = oa.get("topics") or []
    d["tldr"]      = s2.get("tldr", "")

    # ── arXiv ID ──────────────────────────────────────────────────────────────
    arx = ((manual.get("arxiv_id") if manual else "")
           or ax.get("arxiv_id")
           or oa.get("arxiv_id")
           or s2.get("arxiv_id") or "")
    if not arx:
        for pid_ in cr.get("preprint_ids") or []:
            m = (re.search(r"arxiv\.org/abs/(.+)$", pid_ or "")
                 or re.search(r"10\.48550/arxiv\.(.+)$", pid_ or "", re.I))
            if m:
                arx = m.group(1)
    d["arxiv_id"]  = arx
    d["arxiv_url"] = ("https://arxiv.org/abs/" + arx) if arx else ""
    pdfs = []
    for u in ([(manual.get("pdf") if manual else "")] + [s2.get("pdf")] +
              (oa.get("pdf_links") or []) + (cr.get("pdf_links") or []) +
              (ld.get("pdf_links") or []) + (ax.get("pdf_links") or [])):
        if u and u not in pdfs:
            pdfs.append(u)
    d["pdf_urls"]      = pdfs
    d["crossref_type"] = cr.get("type") or oa.get("type") or ""
    d["container"]     = cr.get("container") or oa.get("container") or s2.get("venue") or ""
    d["published_date"]= cr.get("date") or ""
    d["cited_by"]      = (cr.get("cited_by") if cr.get("cited_by") is not None
                          else (oa.get("cited_by") if oa.get("cited_by") is not None
                                else s2.get("cited_by")))
    d["retracted"]     = bool(cr.get("retracted") or oa.get("retracted"))
    hint = p.get("pub_hint", "unknown")
    k = p.get("dblp_key", "")
    if k.startswith("conf/"):
        hint = "conference"
    elif k.startswith("journals/") and not k.startswith("journals/corr/"):
        hint = "journal"
    elif (p.get("doi") or "").startswith("10.48550") or \
            d["crossref_type"] in ("posted-content", "preprint", "repository"):
        hint = "preprint_only"
    elif d["crossref_type"] == "dissertation":
        hint = "thesis"
    elif hint == "unknown":
        if d["crossref_type"] == "journal-article":
            hint = "journal"
        elif d["crossref_type"] in ("proceedings-article", "book-chapter"):
            hint = "conference"
    d["pub_hint"]      = hint
    d["manual_notes"]  = manual.get("notes", "") if manual else ""
    return d


def build_dossiers(run, embed="auto"):
    rows = []
    for p in run.papers():
        c = p.get("enrichment") or run.get_enrichment(p["id"]) or {}
        m = p.get("manual") or run.get_manual_override(p["id"]) or {}
        rows.append(_merge_one(p, c, m))
    sig = compute_signals(rows, embed=embed)
    for r, s in zip(rows, sig):
        r.update(s)
    run.save_papers()
    return rows


def coverage_report(run):
    d    = run.dossiers()
    rows = [d[i] for i in run.eligible()]
    src  = Counter(r.get("abstract_source", "none") for r in rows)
    print("abstract coverage (eligible papers = %d): %s" % (len(rows), dict(src)))
    miss = [r["id"] for r in rows if r.get("abstract_source", "none") == "none"]
    print("papers WITHOUT abstract: %d" % len(miss))
    if miss:
        print("  -> you must look these up yourself (web search / fetch the DOI page) "
              "and store with `ph.py fill`.")
        print("  ids: " + " ".join(miss[:400]) + (" ..." if len(miss) > 400 else ""))


# ── batch enrichment ──────────────────────────────────────────────────────────

_PROBE_N = 6  # papers to try before committing to the full batch


def run_enrich(run, workers=8, limit=0, all_years=False, skip_s2=False, embed="auto", deep=True):
    """
    deep=True  — fetch arXiv for every paper that lacks an abstract (default)
    deep=False — fetch arXiv only for lane A/B papers (faster for large sets)
    """
    papers = [p for p in run.papers() if p.get("in_range") or all_years]
    if limit:
        papers = papers[:limit]

    todo = [p for p in papers if not (p.get("enrichment") or {}).get("done")]
    print("enrich: %d papers, %d still to fetch" % (len(papers), len(todo)))

    dois = sorted({p["doi"] for p in todo if p.get("doi")})
    oamap = {}
    if dois:
        print("openalex batch pre-pass for %d DOIs ..." % len(dois))
        oamap = openalex_batch(dois)
        print("  matched %d" % len(oamap))
    s2map = {}
    if dois and not skip_s2:
        print("semantic scholar batch pre-pass for %d DOIs ..." % len(dois))
        s2map = s2_batch(dois)
        print("  matched %d" % len(s2map))

    # ── Early network probe ─────────────────────────────────────────────────
    if todo and not (s2map or oamap):
        probe_ok = 0
        for p in todo[:_PROBE_N]:
            try:
                _, status = enrich_one(run, p, None, None, deep=deep)
                if status == "fetched":
                    probe_ok += 1
            except Exception:
                pass
        if probe_ok == 0 and len(todo) > _PROBE_N:
            print("ERROR: Early network probe failed (%d attempts, 0 succeeded). "
                  "Check internet connectivity and try again. "
                  "Aborting to avoid %d silent failures." % (_PROBE_N, len(todo)))
            return
        print("Network probe: %d/%d succeeded, proceeding with full batch."
              % (probe_ok, min(_PROBE_N, len(todo))))

    done, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(enrich_one, run, p, s2map.get(p.get("doi")),
                          oamap.get(p.get("doi")), deep) for p in todo]
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 200 == 0 or done == len(todo):
                # Periodic checkpoint save for resume protection
                run.save_papers()
                print("  %d/%d  (%.0fs)" % (done, len(todo), time.time() - t0), flush=True)

    build_dossiers(run, embed=embed)
    coverage_report(run)

    try:
        run.update_state(
            current_stage="enrichment_complete",
            stages={**run.state()["stages"], "enrichment": {
                "status": "completed",
                "completed_at": now(),
                "papers_enriched": len(papers)
            }}
        )
    except Exception:
        pass
