"""
ph/enrich.py — per-paper enrichment (Crossref, OpenAlex, S2, landing page, arXiv)
and dossier/signal building.

KEY FIX (vs original ph.py): arXiv is now fetched in Stage 0 (here), not deferred
to Stage 6 (export). Every paper in the screening pass therefore has arXiv access.
"""
import math, re, os, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .io      import MIN_ABSTRACT, read_json, write_json, read_jsonl, write_jsonl, now
from .network import (p_crossref, p_openalex, p_landing, p_arxiv,
                      s2_batch, BROWSER_UA)
from .signals import compute_signals


# ── single-paper fetch ────────────────────────────────────────────────────────

def enrich_one(run, p, s2rec):
    cpath = run.cache_dir / ("%s.json" % p["id"])
    c = read_json(cpath, {})
    if c.get("done"):
        return p["id"], "cached"
    c   = {"id": p["id"], "errors": []}
    doi, title, year = p.get("doi"), p["title"], p.get("year")
    try:
        c["crossref"] = p_crossref(doi=doi) if doi else p_crossref(title=title, year=year)
        c["openalex"] = p_openalex(doi=doi) if doi else p_openalex(title=title, year=year)
        if doi and not c["openalex"]:
            c["openalex"] = p_openalex(title=title, year=year)
        c["s2"] = s2rec
        have   = any(len((c.get(k) or {}).get("abstract", "")) >= MIN_ABSTRACT
                     for k in ("crossref", "openalex", "s2"))
        landing_url = p.get("ee") or p.get("url") or ("https://doi.org/" + doi if doi else "")
        if landing_url and not landing_url.startswith("http"):
            landing_url = ""
        if landing_url and (not have or not doi):
            c["landing"] = p_landing(landing_url)
            have = have or len((c.get("landing") or {}).get("abstract", "")) >= MIN_ABSTRACT

        # ── arXiv: always fetch at Stage 0 (not deferred to export) ──────
        # This gives the agent arXiv full-text access during screening.
        if not (c.get("openalex") or {}).get("arxiv_id") \
                and not (c.get("s2") or {}).get("arxiv_id"):
            c["arxiv"] = p_arxiv(title)
        else:
            # We already have the arXiv ID from OpenAlex/S2; no need to search by title.
            c["arxiv"] = None

    except Exception as e:
        c["errors"].append("%s: %s" % (type(e).__name__, e))
    c["done"]       = True
    c["fetched_at"] = now()
    write_json(cpath, c)
    return p["id"], "fetched"


# ── dossier merge ─────────────────────────────────────────────────────────────

def _merge_one(p, c, manual):
    d = dict(p)
    cr, oa, s2, ld, ax = (c.get(k) or {} for k in ("crossref", "openalex", "s2", "landing", "arxiv"))
    cands = [(src, re.sub(r"\s+", " ", (v or "")).strip())
             for src, v in [("crossref", cr.get("abstract", "")),
                             ("openalex", oa.get("abstract", "")),
                             ("semanticscholar", s2.get("abstract", "")),
                             ("landing_page", ld.get("abstract", "")),
                             ("arxiv", (ax or {}).get("abstract", ""))]]
    good  = [(k, v) for k, v in cands if len(v) >= MIN_ABSTRACT]
    d["abstract_candidates"] = {k: len(v) for k, v in cands if v}
    if manual.get("abstract"):
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
    # ── arXiv ID: collected from all providers (Stage 0, not Stage 6) ─────
    arx = (manual.get("arxiv_id")
           or (ax or {}).get("arxiv_id")
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
    for u in ([manual.get("pdf")] + [s2.get("pdf")] +
              (oa.get("pdf_links") or []) + (cr.get("pdf_links") or []) +
              (ld.get("pdf_links") or []) + ((ax or {}).get("pdf_links") or [])):
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
    if (p.get("doi") or "").startswith("10.48550") or \
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
    d["manual_notes"]  = manual.get("notes", "")
    return d


def build_dossiers(run, embed="auto"):
    rows = []
    for p in run.papers():
        c = read_json(run.cache_dir / ("%s.json" % p["id"]), {}) or {}
        m = read_json(run.manual_dir / ("%s.json" % p["id"]), {}) or {}
        rows.append(_merge_one(p, c, m))
    sig = compute_signals(rows, embed=embed)
    for r, s in zip(rows, sig):
        r.update(s)
    write_jsonl(run.dossier_path, rows)
    run._dos = None
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
        (run.dir / "missing_abstracts.txt").write_text("\n".join(miss))
    else:
        (run.dir / "missing_abstracts.txt").write_text("")


# ── batch enrichment ──────────────────────────────────────────────────────────

def run_enrich(run, workers=6, limit=0, all_years=False, skip_s2=False, embed="auto"):
    papers = [p for p in run.papers() if p.get("in_range") or all_years]
    if limit:
        papers = papers[:limit]
    todo = [p for p in papers
            if not (read_json(run.cache_dir / ("%s.json" % p["id"]), {}) or {}).get("done")]
    print("enrich: %d papers, %d still to fetch" % (len(papers), len(todo)))
    s2map = {}
    dois  = sorted({p["doi"] for p in todo if p.get("doi")})
    if dois and not skip_s2:
        print("semantic scholar batch pre-pass for %d DOIs ..." % len(dois))
        s2map = s2_batch(dois)
        print("  matched %d" % len(s2map))
    done, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(enrich_one, run, p, s2map.get(p.get("doi"))) for p in todo]
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 50 == 0 or done == len(todo):
                print("  %d/%d  (%.0fs)" % (done, len(todo), time.time() - t0), flush=True)
    build_dossiers(run, embed=embed)
    coverage_report(run)
