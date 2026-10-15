#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a_cli.py — paper-hunter CLI dispatcher.  Python 3.8+, standard library only.

The AI agent driving the pipeline (see prompt.md) calls this; humans never need to.
All logic is in the co-located Python files (b_io.py, c_schema.py, etc.).
Every sub-command is resumable: all state lives in runs/<csv-stem>/ as plain files.

  init          load + normalise the CSV, assign stable ids
  doctor        check network access to metadata providers
  enrich        fetch abstracts + metadata for EVERY paper (Crossref, OpenAlex, S2,
                publisher landing page, arXiv) — arXiv included at Stage 0
  signals       recompute signals only (after manual abstracts were filled in)
  fill          store an abstract/metadata the agent found by hand for one paper
  show          print one full dossier
  search        local fuzzy + TF-IDF search over the dossiers
  status        progress of every pass
  batch         print the next batch of papers to review for a pass (A, B, C, F)
  record        validate + store decisions (JSON on stdin or --file)
  fulltext      download + extract open-access full text of one paper
  expand-audit  widen the blind audit pass (recall safety nets)
  sweep         add high-signal papers to blind pass C
  reconcile     merge passes, find disagreements, build the adjudication queue
  validate      consistency checks on the final verdicts
  export        write the filtered CSV (+ borderline CSV, audit CSV, short report)
"""
import argparse, csv as _csv, json, math, random, re, shutil, sys, textwrap, time
import unicodedata
from collections import Counter
from pathlib import Path

# ── package imports (relative path removed, direct imports) ───────────────────
from b_io      import (Run, TOOLS_DIR, RUNS_DIR, DEFAULT_OUTPUT, now, read_jsonl, write_jsonl,
                         append_jsonl, read_json, write_json, write_csv, norm, clean_title)
from c_schema  import (ROLES, ROLE_TO_CONTRIB, CONTRIBS, VERDICTS, YNU, SCOPES,
                         PUB_STATUS, PUB_OK_STRICT, REJECT_CLASSES, BORDER_REASONS,
                         ABSTRACT_SOURCES, EVIDENCE_SOURCES, validate_record)
from d_network import (http, p_crossref, p_openalex, p_landing, p_arxiv,
                         s2_batch, BROWSER_UA, title_sim, URL_S2, URL_CROSSREF,
                         URL_OPENALEX, URL_ARXIV)
from e_enrich  import (run_enrich, build_dossiers, coverage_report, enrich_one)
from f_signals import compute_signals, SWEEP_QUERIES

import urllib.parse

SEED = 20261014


def die(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


# ── init ──────────────────────────────────────────────────────────────────────
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)

def pub_hint_from_key(dblp_key, venue, title):
    k    = (dblp_key or "")
    text = " ".join([venue or "", title or ""]).lower()
    if k.startswith("journals/corr/"):  return "preprint_only"
    if re.search(r"\bworkshops?\b|\bwksp\b|\bws\b", text): return "workshop"
    if k.startswith("conf/"):           return "conference"
    if k.startswith("journals/"):       return "journal"
    if k.startswith("phd/") or "thesis" in k: return "thesis"
    return "unknown"

def cmd_init(a):
    import csv, io as _io
    src = Path(a.csv).expanduser()
    if not src.exists():
        die("CSV not found: %s" % src)
    raw  = src.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    rdr  = csv.DictReader(_io.StringIO(text))
    if not rdr.fieldnames:
        die("CSV has no header row")
    cols = {c.strip().lower(): c for c in rdr.fieldnames}
    if "title" not in cols:
        die("CSV needs a 'title' column; found %s" % list(cols))
    import hashlib
    g       = lambda row, k: (row.get(cols[k], "") or "").strip() if k in cols else ""
    name    = re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem)
    run_dir = RUNS_DIR / name
    if run_dir.exists() and not a.fresh:
        print("run %s already exists - resuming it (use --fresh to wipe)" % name)
        (TOOLS_DIR / ".current").write_text(str(src.resolve()))
        return cmd_status(argparse.Namespace(run=name))
    if run_dir.exists() and a.fresh:
        shutil.rmtree(run_dir)
    for sub in ("cache", "manual", "fulltext"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, run_dir / "input.csv")
    papers, seen_doi, seen_title = [], {}, {}
    for i, row in enumerate(rdr, 1):
        title = clean_title(g(row, "title"))
        if not title:
            continue
        ee, url = g(row, "ee"), g(row, "url")
        m   = (DOI_RE.search(ee) or DOI_RE.search(url)
               or (DOI_RE.search(g(row, "doi")) if "doi" in cols else None))
        doi = m.group(0).rstrip(".,;)").lower() if m else ""
        try:
            year = int(re.search(r"\d{4}", g(row, "year")).group(0))
        except Exception:
            year = 0
        pid = "p%04d" % (len(papers) + 1)
        p   = {"id": pid, "row": i, "dblp_key": g(row, "dblp_key"), "title": title,
               "authors": g(row, "authors"), "year": year, "venue": g(row, "venue"),
               "ee": ee, "url": url, "doi": doi,
               "in_range": (a.min_year <= year <= a.max_year) if year else True,
               "pub_hint": pub_hint_from_key(g(row, "dblp_key"), g(row, "venue"), title)}
        extra = {c: (row.get(cols[c], "") or "") for c in cols
                 if c not in ("dblp_key", "title", "authors", "year", "venue", "ee", "url", "doi")}
        if extra:
            p["extra_columns"] = extra
        nt = norm(title)
        if doi and doi in seen_doi:
            p["dup_of"] = seen_doi[doi]
        elif nt in seen_title:
            p["dup_of"] = seen_title[nt]
        else:
            if doi:
                seen_doi[doi] = pid
            seen_title[nt] = pid
        papers.append(p)
    write_jsonl(run_dir / "papers.jsonl", papers)
    meta = {"csv": str(src.resolve()), "sha1": hashlib.sha1(raw).hexdigest(),
            "created": now(), "n_rows": len(papers),
            "min_year": a.min_year, "max_year": a.max_year,
            "output_dir": str(Path(a.output_dir).expanduser().resolve()) if a.output_dir else str(DEFAULT_OUTPUT),
            "publication_policy": a.publication_policy}
    write_json(run_dir / "meta.json", meta)
    (TOOLS_DIR / ".current").write_text(str(src.resolve()))
    n_dup = sum(1 for p in papers if p.get("dup_of"))
    n_oor = sum(1 for p in papers if not p["in_range"])
    n_doi = sum(1 for p in papers if p["doi"])
    print("run '%s' created: %d papers | %d with DOI | %d duplicates | %d out of %d-%d range"
          % (name, len(papers), n_doi, n_dup, n_oor, a.min_year, a.max_year))


# ── doctor ────────────────────────────────────────────────────────────────────

def cmd_doctor(a):
    tests = {"crossref":        "%s/works/10.1145/3372297.3417268" % URL_CROSSREF,
             "openalex":        "%s/works/doi:10.1145/3372297.3417268?select=id" % URL_OPENALEX,
             "semanticscholar": "%s/graph/v1/paper/DOI:10.1145/3372297.3417268?fields=title" % URL_S2,
             "arxiv":           "%s?search_query=all:test&max_results=1" % URL_ARXIV,
             "doi.org":         "https://doi.org/10.1145/3372297.3417268"}
    res = {}
    for k, u in tests.items():
        st, _, _, _ = http(u, "doctor" + k, retries=1, timeout=12, browser=(k == "doi.org"))
        res[k] = "ok" if st in (200, 301, 302, 303) else ("HTTP %s" % st if st else "unreachable")
    res["pdftotext"] = bool(shutil.which("pdftotext"))
    for pkg in ("pypdf", "sentence_transformers"):
        try:
            __import__(pkg)
            res[pkg] = True
        except Exception:
            res[pkg] = False
    res["python"] = sys.version.split()[0]
    print(json.dumps(res, indent=1))
    reachable = [k for k in ("crossref", "openalex", "semanticscholar") if res[k] == "ok"]
    if not reachable:
        print("\nNO metadata API reachable. Use your own web-search / web-fetch tools to find "
              "abstracts (store each with `ph.py fill`). Do NOT skip papers.")


# ── enrich / signals / fill ───────────────────────────────────────────────────

def cmd_enrich(a):
    run = Run(a.run)
    run_enrich(run, workers=a.workers, limit=a.limit, all_years=a.all_years,
               skip_s2=a.skip_s2, embed=a.embed)

def cmd_signals(a):
    run = Run(a.run)
    build_dossiers(run, embed=a.embed)
    coverage_report(run)

def cmd_fill(a):
    run = Run(a.run)
    if a.id not in {p["id"] for p in run.papers()}:
        die("unknown id " + a.id)
    m  = run.get_manual_override(a.id)
    ab = a.abstract
    if a.abstract_file:
        ab = Path(a.abstract_file).read_text(encoding="utf-8")
    if ab:
        m["abstract"] = re.sub(r"\s+", " ", ab).strip()
        m["source"]   = a.source
    if a.arxiv:
        m["arxiv_id"] = re.sub(r"^.*arxiv\.org/(abs|pdf)/", "", a.arxiv).replace(".pdf", "")
    if a.pdf:
        m["pdf"] = a.pdf
    if a.notes:
        m["notes"] = a.notes
    if a.url:
        m["url"] = a.url
    run.save_manual_override(a.id, m)
    build_dossiers(run, embed="off")
    print("stored for %s (abstract_source=%s)" % (a.id, run.dossiers()[a.id]["abstract_source"]))


# ── show / search ─────────────────────────────────────────────────────────────

KWIC_TERMS = [r"black[- ]?box", r"gr[ae]y[- ]?box|white[- ]?box",
              r"attack chain|kill chain|multi-?step|long[- ]horizon",
              r"autonomous(ly)?", r"human (intervention|in the loop|operator)|manual",
              r"we (propose|present|introduce|develop)", r"contributions?",
              r"benchmark", r"limitations?", r"nmap|metasploit|shell|tool"]

def wrap(text, width=110, indent=""):
    return textwrap.fill(text or "", width=width,
                         initial_indent=indent, subsequent_indent=indent)

def render_dossier(d, run=None, with_prior=None):
    flags = ",".join(d.get("signal_flags", [])) or "-"
    L = ["[%s]  signal=%s  safety_net=%s  flags=%s  sim_domain_pct=%s"
         % (d["id"], d.get("signal_score"), "YES" if d.get("safety_net") else "no",
            flags, d.get("sim_domain_pct")),
         "TITLE:   " + d["title"],
         "AUTHORS: " + (d.get("authors") or "?"),
         "VENUE:   %s | %s | dblp=%s | pub_hint=%s | crossref_type=%s"
         % (d.get("venue"), d.get("year"), d.get("dblp_key"),
            d.get("pub_hint"), d.get("crossref_type") or "?"),
         "DOI:     %s | arXiv: %s | link: %s | cited_by=%s%s"
         % (d.get("doi") or "none", d.get("arxiv_id") or "none",
            d.get("ee") or d.get("url"), d.get("cited_by"),
            " | RETRACTED?!" if d.get("retracted") else "")]
    ab = d.get("abstract") or ""
    if ab:
        L += ["ABSTRACT [%s, %d chars]:" % (d.get("abstract_source"), len(ab)),
              wrap(ab, 118, "  ")]
    else:
        L.append("ABSTRACT: *** NONE FOUND *** (web search / fetch DOI page, then `ph.py fill`)")
        if d.get("abstract_snippet"):
            L.append("  short snippet only: " + d["abstract_snippet"][:400])
    for k, lbl in (("keywords", "KEYWORDS"), ("topics", "TOPICS")):
        if d.get(k):
            L.append("%s: %s" % (lbl, "; ".join(d[k])))
    if d.get("tldr"):
        L.append("S2 TLDR:  " + d["tldr"])
    if d.get("manual_notes"):
        L.append("NOTES:    " + d["manual_notes"])
    if with_prior:
        for tag, rec in with_prior:
            if rec:
                L.append("PRIOR %s: verdict=%s roles=%s conf=%s | %s"
                         % (tag, rec.get("verdict"), rec.get("roles"),
                            rec.get("confidence"), rec.get("reason")))
    return "\n".join(L)

def cmd_show(a):
    run = Run(a.run)
    d   = run.dossiers().get(a.id)
    if not d:
        die("unknown id")
    print(render_dossier(d, with_prior=(
        [(p, run.decisions(p).get(a.id)) for p in ("A", "B", "C", "F")]
        if not a.blind else None)))

def _toks(text):
    import math
    words = [w.strip(".-+") for w in re.findall(r"[a-z0-9][a-z0-9\-\+\.]*", (text or "").lower())]
    STOP  = set("a an the of for and or in on to with by from is are be this that these those "
                "we our it its as at using use based via into over under between across".split())
    uni   = [w for w in words if w and w not in STOP and len(w) > 1]
    return uni + [a + "_" + b for a, b in zip(uni, uni[1:])]

def search_docs(run, query, top=20):
    import math, difflib
    dos  = run.dossiers()
    ids  = [i for i in run.eligible() if i in dos]
    texts = [dos[i]["title"] + " . " + dos[i]["title"] + " . "
             + (dos[i].get("abstract") or "") + " " + " ".join(dos[i].get("keywords") or [])
             for i in ids]
    tl   = [_toks(t) for t in texts]
    df   = Counter()
    for t in tl:
        df.update(set(t))
    N   = len(tl)
    idf = {t: math.log((N + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    vocab = [t for t in df if "_" not in t and len(t) >= 5]
    qt  = _toks(query)
    expanded = list(qt)
    for t in qt:
        if "_" in t or t in df or len(t) < 5:
            continue
        expanded += difflib.get_close_matches(t, vocab, n=2, cutoff=0.84)
    def vec(tokens):
        tf = Counter(tokens)
        v  = {t: (1 + math.log(c)) * idf.get(t, 0.0) for t, c in tf.items()}
        n  = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / n for t, x in v.items()}
    def cosine(a, b):
        if len(a) > len(b): a, b = b, a
        return sum(x * b.get(t, 0.0) for t, x in a.items())
    qv  = vec(expanded)
    res = [(0.85 * cosine(vec(t), qv) + 0.15 * title_sim(query, dos[i]["title"]), i)
           for i, t in zip(ids, tl)]
    res.sort(reverse=True)
    return res[:top]

def cmd_search(a):
    run = Run(a.run)
    dos = run.dossiers()
    for s, i in search_docs(run, a.query, a.top):
        print("%.3f  %s  sig=%-3s  %s"
              % (s, i, dos[i].get("signal_score"), dos[i]["title"][:110]))


# ── status / plans / batch ────────────────────────────────────────────────────

def pass_plan(run, p):
    if p == "A":
        return run.eligible()
    pl = run.plans()
    return pl.get(p)

def cmd_status(a):
    run = Run(a.run if hasattr(a, "run") else None)
    el  = run.eligible()
    print("run: %s | csv: %s | papers: %d | eligible: %d"
          % (run.name, run.meta.get("csv"), len(run.papers()), len(el)))
    if run.dossier_path.exists():
        coverage_report(run)
    else:
        print("dossiers not built yet -> run `ph.py enrich`")
    pl = run.plans()
    for p in ("A", "B", "C", "F"):
        plan = pass_plan(run, p)
        dec  = run.decisions(p)
        if plan is None:
            print("pass %s: not planned yet" % p)
        else:
            done = sum(1 for i in plan if i in dec)
            print("pass %s: %d / %d done, %d remaining"
                  % (p, done, len(plan), len(plan) - done))
    A = run.decisions("A")
    if A:
        print("pass A verdicts: %s" % dict(Counter(r["verdict"] for r in A.values())))
    fin = read_jsonl(run.dir / "final.jsonl")
    if fin:
        print("final verdicts: %s" % dict(Counter(r["verdict"] for r in fin)))

def ensure_plan_B(run, force=False):
    pl = run.plans()
    if "B" in pl:
        return pl["B"]
    A, dos, el = run.decisions("A"), run.dossiers(), run.eligible()
    missing = [i for i in el if i not in A]
    if missing and not force:
        die("pass A incomplete (%d papers left) - finish it first" % len(missing))
    B = [i for i in el if (a := A.get(i)) and
         (a["verdict"] in ("accept", "borderline") or a.get("needs_deep_dive")
          or dos[i].get("safety_net")
          or (dos[i].get("abstract_source") == "none" and a.get("confidence", 1) < 0.9)
          or a.get("confidence", 1) < 0.6 or dos[i].get("retracted"))]
    B.sort(key=lambda i: -dos[i].get("signal_score", 0))
    pl["B"] = B
    run.save_plans(pl)
    print("plan B frozen: %d papers" % len(B))
    return B

def ensure_plan_C(run):
    pl = run.plans()
    if "C" in pl:
        return pl["C"]
    if "B" not in pl:
        die("plan B not frozen yet (finish pass A, then `batch --pass B`)")
    dos, el = run.dossiers(), run.eligible()
    C    = set(pl["B"])
    rest = sorted([i for i in el if i not in C], key=lambda i: -dos[i].get("signal_score", 0))
    top, tail = rest[:40], rest[40:]
    rnd  = random.Random(SEED)
    k    = min(len(tail), max(60, math.ceil(0.12 * len(tail))))
    samp = rnd.sample(tail, k) if tail else []
    C   |= set(top) | set(samp)
    lst  = sorted(C)
    random.Random(SEED + 1).shuffle(lst)
    pl["C"] = lst
    pl["C_audit_sample"] = top + samp
    run.save_plans(pl)
    print("plan C frozen: %d papers (blind audit)" % len(lst))
    return lst

def cmd_batch(a):
    run = Run(a.run)
    p   = a.pass_.upper()
    dos = run.dossiers()
    if p == "A":
        if not run.dossier_path.exists():
            die("run `ph.py enrich` first")
        ids = sorted(run.eligible(), key=lambda i: -dos[i].get("signal_score", 0))
    elif p == "B":
        ids = ensure_plan_B(run, a.force)
    elif p == "C":
        ids = ensure_plan_C(run)
    elif p == "F":
        ids = run.plans().get("F")
        if ids is None:
            die("no adjudication queue yet - run `ph.py reconcile` after pass C")
    else:
        die("pass must be A, B, C or F")
    dec  = run.decisions(p)
    todo = [i for i in ids if i not in dec]
    if a.ids:
        todo = [i for i in a.ids.split(",") if i in ids and i not in dec] or a.ids.split(",")
    if not todo:
        print("PASS %s COMPLETE (%d/%d)." % (p, len(ids), len(ids)))
        return
    chunk = todo[:a.n]
    print("=" * 100)
    print("PASS %s | batch of %d | %d remaining | %d total in pass"
          % (p, len(chunk), len(todo), len(ids)))
    if p == "C":
        print("BLIND PASS: do NOT open decisions_A/B files or reuse earlier verdicts. "
              "Re-read prompt.md sections 2-4 before judging.")
    print("=" * 100)
    for i in chunk:
        prior = None
        if p == "B":
            prior = [("A", run.decisions("A").get(i))]
        elif p == "F":
            prior = [(x, run.decisions(x).get(i)) for x in "ABCF"]
        print(render_dossier(dos[i], with_prior=prior))
        print("-" * 100)
    print("Next: judge every paper above (follow prompt.md), "
          "then `python3 tools/ph.py record --pass %s` with a JSON array." % p)


# ── record ────────────────────────────────────────────────────────────────────

def cmd_record(a):
    run = Run(a.run)
    p   = a.pass_.upper()
    if p not in ("A", "B", "C", "F"):
        die("pass must be A, B, C or F")
    raw = Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    if isinstance(data, dict):
        data = [data]
    policy = run.meta.get("publication_policy", "strict")
    plan   = pass_plan(run, p)
    dos    = run.dossiers()
    ok, bad = 0, 0
    for r in data:
        errs = validate_record(run, r, p, policy, dos=dos)
        if plan is not None and r.get("id") not in plan and p not in ("A", "F"):
            errs.append("id %s is not part of pass %s" % (r.get("id"), p))
        if errs:
            bad += 1
            print("REJECTED %s: %s" % (r.get("id"), " | ".join(errs)))
            continue
        r["pass"], r["ts"] = p, now()
        if r["verdict"] == "reject":
            r["roles"] = []
        
        # Save to unified decision log
        run.save_decision(r)
        ok += 1
    
    print("recorded %d, rejected %d (fix the rejected ones and resend only those)"
          % (ok, bad))
    if plan is not None:
        dec = run.decisions(p)
        print("pass %s: %d/%d done" % (p, sum(1 for i in plan if i in dec), len(plan)))
    
    # Update state tracking
    try:
        passes = run.state()["passes"]
        passes[p]["completed"] = sum(1 for d in run.decisions(p).values())
        run.update_state(passes=passes)
    except Exception:
        pass  # State tracking is optional


# ── fulltext ──────────────────────────────────────────────────────────────────

def cmd_fulltext(a):
    import subprocess, tempfile
    from html.parser import HTMLParser

    class _TextEx(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out, self.skip = [], 0
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav", "footer", "header", "noscript"):
                self.skip += 1
            if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "li", "section"):
                self.out.append("\n")
        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "footer", "header", "noscript") and self.skip:
                self.skip -= 1
        def handle_data(self, data):
            if not self.skip:
                self.out.append(data)

    def pdf_to_text(data):
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "x.pdf"
            fp.write_bytes(data)
            if shutil.which("pdftotext"):
                try:
                    subprocess.run(["pdftotext", "-layout", str(fp), str(Path(td) / "x.txt")],
                                   check=True, timeout=180, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    t = (Path(td) / "x.txt").read_text(encoding="utf-8", errors="replace")
                    if len(t) > 2000:
                        return t
                except Exception:
                    pass
            for pkg in ("pypdf", "PyPDF2"):
                try:
                    mod = __import__(pkg)
                    rd  = mod.PdfReader(str(fp))
                    return "\n".join((pg.extract_text() or "") for pg in rd.pages)
                except Exception:
                    pass
        return ""

    run = Run(a.run)
    d   = run.dossiers().get(a.id)
    if not d:
        die("unknown id")
    dst  = run.fulltext_dir / ("%s.txt" % a.id)
    text = dst.read_text(encoding="utf-8", errors="replace") if dst.exists() and not a.refresh else ""
    src  = "cache"
    if not text:
        urls = list(a.url or []) + d.get("pdf_urls", [])
        if d.get("arxiv_id"):
            urls.append("https://arxiv.org/pdf/" + d["arxiv_id"])
        if d.get("ee"):
            urls.append(d["ee"])
        if d.get("doi"):
            urls.append("https://doi.org/" + d["doi"])
        from d_network import _MetaParser
        for u in dict.fromkeys(urls):
            st, body, final, _ = http(u, urllib.parse.urlparse(u).netloc,
                                       browser=True, retries=2, timeout=45)
            if st != 200 or not body:
                continue
            if body[:5] == b"%PDF-":
                t = pdf_to_text(body)
            else:
                ex = _TextEx()
                try:
                    ex.feed(body.decode("utf-8", errors="replace"))
                except Exception:
                    pass
                t = re.sub(r"\n\s*\n+", "\n\n", "".join(ex.out))
            if len(t) >= 3000:
                text, src = t, final
                dst.write_text(text, encoding="utf-8")
                break
    if not text:
        print("NO FULL TEXT RETRIEVABLE for %s. "
              "Use your own web tools; quote with evidence_source=web + evidence_url." % a.id)
        return
    print("FULL TEXT for %s saved at %s  (%d chars, source: %s)"
          % (a.id, dst, len(text), src))
    heads = [(m.start(), m.group(0).strip()) for m in re.finditer(
        r"(?im)^\s*(\d+\.?\s*)?(abstract|introduction|background|related work|threat model|"
        r"design|approach|methodology|method|system overview|architecture|implementation|"
        r"evaluation|experiments?|results|discussion|limitations?|conclusions?|references)\b.{0,60}$",
        text)]
    print("OUTLINE: " + " | ".join("%s@%d" % (h[:40], pos) for pos, h in heads[:30]))
    m  = re.search(r"(?i)\bintroduction\b", text)
    s  = m.start() if m else 0
    print("\n--- INTRODUCTION / OPENING (first %d chars) ---" % a.chars)
    print(text[s:s + a.chars])
    print("\n--- KEY-TERM CONTEXTS ---")
    for t in KWIC_TERMS:
        for h in list(re.finditer(t, text, re.I))[:3]:
            print("[%s] ...%s..." % (t[:18], re.sub(r"\s+", " ",
                                                     text[max(0, h.start()-170):h.end()+170])))


# ── expand-audit / sweep ──────────────────────────────────────────────────────

def cmd_expand_audit(a):
    run = Run(a.run)
    pl  = run.plans()
    if "C" not in pl:
        die("plan C does not exist yet")
    dos  = run.dossiers()
    cur  = set(pl["C"])
    cand = [i for i in run.eligible()
            if i not in cur and (a.all
                                  or dos[i].get("signal_score", 0) >= a.min_signal
                                  or dos[i].get("sim_domain_pct", 0) >= a.min_pct)]
    pl["C"] = pl["C"] + cand
    run.save_plans(pl)
    print("added %d papers to blind pass C (now %d)" % (len(cand), len(pl["C"])))

def cmd_sweep(a):
    run = Run(a.run)
    pl  = run.plans()
    if "C" not in pl:
        die("plan C does not exist yet")
    cur, add = set(pl["C"]), []
    for q in SWEEP_QUERIES:
        for s, i in search_docs(run, q, a.top):
            if i not in cur and s >= a.min_score:
                cur.add(i)
                add.append(i)
    pl["C"] = pl["C"] + add
    run.save_plans(pl)
    print("recall sweep: %d queries -> %d new papers added to blind pass C (now %d)"
          % (len(SWEEP_QUERIES), len(add), len(pl["C"])))


# ── reconcile ─────────────────────────────────────────────────────────────────

def cmd_reconcile(a):
    run = Run(a.run)
    pl  = run.plans()
    A, B, C, F = (run.decisions(x) for x in "ABCF")
    el = run.eligible()
    if any(i not in A for i in el):
        die("pass A incomplete")
    if "B" in pl and any(i not in B for i in pl["B"]):
        die("pass B incomplete (%d left)" % sum(1 for i in pl["B"] if i not in B))
    if "C" not in pl:
        die("pass C not planned - run `batch --pass C` first")
    if any(i not in C for i in pl["C"]):
        die("pass C incomplete (%d left)" % sum(1 for i in pl["C"] if i not in C))

    def primary_of(i):
        return B.get(i) or A.get(i)

    def disagreement(p, c):
        if p["verdict"] != c["verdict"]:
            return "verdict %s vs %s" % (p["verdict"], c["verdict"])
        if set(p.get("roles", [])) != set(c.get("roles", [])):
            return "roles %s vs %s" % (p.get("roles"), c.get("roles"))
        if p["verdict"] != "reject" and p.get("primary_role") != c.get("primary_role"):
            return "primary_role differs"
        if min(float(p.get("confidence", 0)), float(c.get("confidence", 0))) < 0.6:
            return "low confidence"
        return ""

    queue, why, fn = list(pl.get("F", [])), {}, []
    for i in pl["C"]:
        p, c = primary_of(i), C[i]
        dis  = disagreement(p, c)
        if dis:
            why[i] = dis
            if i not in queue:
                queue.append(i)
        if (A[i]["verdict"] == "reject" and c["verdict"] in ("accept", "borderline")
                and i in set(pl.get("C_audit_sample", []))):
            fn.append(i)
    pl["F"] = queue
    pl["F_why"] = {**pl.get("F_why", {}), **why}
    run.save_plans(pl)
    sample = pl.get("C_audit_sample", [])
    print("blind audit sample: %d | false negatives rescued by blind pass: %d %s"
          % (len(sample), len(fn), fn[:20]))
    print("adjudication queue (pass F): %d papers, %d still undecided"
          % (len(queue), sum(1 for i in queue if i not in F)))
    if any(i not in F for i in queue):
        print("Run `batch --pass F` and settle them with full-text evidence.")
        return
    if fn and not pl.get("expanded_once"):
        print("!! The audit found false negatives. Widen the net: "
              "`ph.py expand-audit --min-signal 1 --min-pct 0.5` "
              "(or --all), finish pass C on the new papers, then reconcile again.")
    final = []
    for p in run.papers():
        i = p["id"]
        if not p.get("in_range"):
            row = {"id": i, "verdict": "reject", "reject_class": "out_of_range",
                   "roles": [], "reason": "year outside configured range",
                   "confidence": 1.0, "passes": ["auto"]}
        elif p.get("dup_of"):
            row = {"id": i, "verdict": "reject", "reject_class": "duplicate",
                   "roles": [], "dup_of": p["dup_of"],
                   "reason": "duplicate of %s" % p["dup_of"],
                   "confidence": 1.0, "passes": ["auto"]}
        else:
            base = F.get(i) or primary_of(i)
            row  = dict(base)
            row["passes"] = [x for x, dd in (("A", A), ("B", B), ("C", C), ("F", F)) if i in dd]
            if i in C and i not in F:
                c = C[i]
                row["confidence"]   = round((float(base.get("confidence", 0))
                                             + float(c.get("confidence", 0))) / 2, 2)
                row["fulltext_read"] = bool(base.get("fulltext_read") or c.get("fulltext_read"))
        final.append(row)
    write_jsonl(run.dir / "final.jsonl", final)
    print("final.jsonl written: %s" % dict(Counter(r["verdict"] for r in final)))


# ── validate ──────────────────────────────────────────────────────────────────

def cmd_validate(a):
    run    = Run(a.run)
    fin    = read_jsonl(run.dir / "final.jsonl")
    if not fin:
        die("no final.jsonl - run reconcile")
    policy = run.meta.get("publication_policy", "strict")
    errs, ids = [], {r["id"] for r in fin}
    for p in run.papers():
        if p["id"] not in ids:
            errs.append("%s missing from final" % p["id"])
    dos = run.dossiers()
    for r in fin:
        if r.get("passes") == ["auto"]:
            continue
        e = validate_record(run, dict(r), "F", policy, dos=dos)
        e = [x for x in e if "not part of pass" not in x]
        if e:
            errs.append("%s: %s" % (r["id"], " | ".join(e)))
    if errs:
        print("VALIDATION FAILED (%d):" % len(errs))
        print("\n".join(errs[:60]))
        sys.exit(1)
    print("validation OK: %d papers, every eligible paper has a verified final verdict"
          % len(fin))


# ── export ────────────────────────────────────────────────────────────────────

CSV_COLS = ["paper_id", "title", "authors", "year", "venue", "doi", "arxiv_url",
            "ee", "dblp_key", "url", "tier", "primary_role", "roles",
            "primary_contribution", "subject_scope",
            "llm_agent", "offensive_security", "black_box", "long_horizon",
            "publication_status", "system_or_artifact_name", "attack_surfaces",
            "confidence", "review_passes", "full_text_read",
            "abstract_source", "evidence_quote", "reason", "abstract"]

def _row_out(d, r):
    return {"paper_id": d["id"], "title": d["title"], "authors": d.get("authors"),
            "year": d.get("year"), "venue": d.get("venue"),
            "doi": ("https://doi.org/" + d["doi"]) if d.get("doi") else "",
            "arxiv_url": d.get("arxiv_url", ""),
            "ee": d.get("ee"), "dblp_key": d.get("dblp_key"), "url": d.get("url"),
            "tier": r["verdict"], "primary_role": r.get("primary_role", ""),
            "roles": ";".join(r.get("roles", [])),
            "primary_contribution": r.get("primary_contribution", ""),
            "subject_scope": r.get("subject_scope", ""),
            "llm_agent":          (r.get("criteria") or {}).get("llm_agent", ""),
            "offensive_security": (r.get("criteria") or {}).get("offensive_security", ""),
            "black_box":          (r.get("criteria") or {}).get("black_box", ""),
            "long_horizon":       (r.get("criteria") or {}).get("long_horizon", ""),
            "publication_status": r.get("publication_status", ""),
            "system_or_artifact_name": r.get("system_name", ""),
            "attack_surfaces": ";".join(r.get("attack_surfaces", []) or []),
            "confidence": r.get("confidence", ""),
            "review_passes": ("".join(r.get("passes", []))
                              if r.get("passes") != ["auto"] else "auto"),
            "full_text_read": "yes" if r.get("fulltext_read") else "no",
            "abstract_source": d.get("abstract_source"),
            "evidence_quote": r.get("evidence", ""),
            "reason": r.get("reason", ""), "abstract": d.get("abstract", "")}

def cmd_export(a):
    run = Run(a.run)
    try:
        cmd_validate(argparse.Namespace(run=a.run))
    except SystemExit:
        if not a.force:
            die("fix validation errors first (or use --force)")
    fin = {r["id"]: r for r in read_jsonl(run.dir / "final.jsonl")}
    dos = run.dossiers()
    
    # Get original CSV filename
    csv_path = Path(run.meta.get("csv", ""))
    output_filename = csv_path.stem if csv_path.exists() else run.name
    
    # Prepare accepted papers for paper-reference
    order = {r: n for n, r in enumerate(ROLES)}
    acc = [_row_out(dos[i], r) for i, r in fin.items() if r["verdict"] == "accept"]
    acc.sort(key=lambda x: (order.get(x["primary_role"], 9),
                             -float(x["confidence"] or 0), x["title"]))
    
    # Output filtered CSV to paper-reference/
    out_dir = DEFAULT_OUTPUT
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{output_filename}.csv"
    
    output_rows = []
    for row in acc:
        output_row = {
            "dblp_key": row["dblp_key"],
            "title": row["title"],
            "authors": row["authors"],
            "year": row["year"],
            "venue": row["venue"],
            "ee": row["ee"],
            "url": row["url"],
            "Role": row["primary_role"]
        }
        output_rows.append(output_row)
    
    cols = ["dblp_key", "title", "authors", "year", "venue", "ee", "url", "Role"]
    write_csv(output_path, cols, output_rows)
    
    # Save borderline and audit CSV to paper-dump (run directory)
    bor = []
    for i, r in fin.items():
        if r["verdict"] == "borderline":
            row = _row_out(dos[i], r)
            row["reason"] = "[%s] %s" % (r.get("borderline_reason"), r.get("reason"))
            bor.append(row)
    bor.sort(key=lambda x: -float(x["confidence"] or 0))
    
    audit = []
    for p in run.papers():
        r = fin[p["id"]]
        d = dos[p["id"]]
        audit.append({"paper_id": p["id"], "dblp_key": p["dblp_key"],
                      "title": p["title"], "year": p["year"], "venue": p["venue"],
                      "tier": r["verdict"], "roles": ";".join(r.get("roles", [])),
                      "reject_class": r.get("reject_class", ""),
                      "borderline_reason": r.get("borderline_reason", ""),
                      "confidence": r.get("confidence", ""),
                      "review_passes": "".join(r.get("passes", [])),
                      "signal_score": d.get("signal_score"),
                      "abstract_source": d.get("abstract_source"),
                      "reason": r.get("reason", "")})
    
    # Write borderline and audit to paper-dump
    borderline_cols = ["paper_id", "dblp_key", "title", "authors", "year", "venue", "doi", 
                       "arxiv_url", "ee", "url", "tier", "primary_role", "roles", 
                       "primary_contribution", "subject_scope", "llm_agent", "offensive_security",
                       "black_box", "long_horizon", "publication_status", "system_or_artifact_name",
                       "attack_surfaces", "confidence", "review_passes", "full_text_read",
                       "abstract_source", "evidence_quote", "reason", "abstract"]
    
    write_csv(run.dir / f"{output_filename}-borderline.csv", borderline_cols, bor)
    write_csv(run.dir / f"{output_filename}-audit-all.csv", 
              ["paper_id", "dblp_key", "title", "year", "venue", "tier",
               "roles", "reject_class", "borderline_reason", "confidence",
               "review_passes", "signal_score", "abstract_source", "reason"], audit)
    
    print("Wrote filtered CSV with %d papers to: %s" % (len(output_rows), output_path))
    print("Wrote borderline (%d) and audit (%d) CSVs to: %s" % (len(bor), len(audit), run.dir))


# ── selftest (Change 2) ───────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(prog="ph.py", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=None, help="run name (default: current)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--csv", required=True)
    s.add_argument("--min-year", type=int, default=2023)
    s.add_argument("--max-year", type=int, default=2026)
    s.add_argument("--output-dir", default=None)
    s.add_argument("--publication-policy", choices=["strict", "relaxed"], default="strict")
    s.add_argument("--fresh", action="store_true")
    s.set_defaults(f=cmd_init)

    s = sub.add_parser("doctor"); s.set_defaults(f=cmd_doctor)

    s = sub.add_parser("enrich")
    s.add_argument("--workers", type=int, default=6)
    s.add_argument("--limit",   type=int, default=0)
    s.add_argument("--skip-s2", action="store_true")
    s.add_argument("--all-years", action="store_true")
    s.add_argument("--embed", choices=["auto", "on", "off"], default="auto")
    s.set_defaults(f=cmd_enrich)

    s = sub.add_parser("signals")
    s.add_argument("--embed", choices=["auto", "on", "off"], default="auto")
    s.set_defaults(f=cmd_signals)

    s = sub.add_parser("fill")
    s.add_argument("id")
    s.add_argument("--abstract"); s.add_argument("--abstract-file")
    s.add_argument("--source", default="manual_web", choices=["manual_web", "fulltext"])
    s.add_argument("--arxiv"); s.add_argument("--pdf"); s.add_argument("--notes")
    s.add_argument("--url")
    s.set_defaults(f=cmd_fill)

    s = sub.add_parser("show")
    s.add_argument("id"); s.add_argument("--blind", action="store_true")
    s.set_defaults(f=cmd_show)

    s = sub.add_parser("search")
    s.add_argument("query"); s.add_argument("--top", type=int, default=20)
    s.set_defaults(f=cmd_search)

    s = sub.add_parser("status"); s.set_defaults(f=cmd_status)

    s = sub.add_parser("batch")
    s.add_argument("--pass", dest="pass_", required=True)
    s.add_argument("--n",    type=int, default=10)
    s.add_argument("--ids"); s.add_argument("--force", action="store_true")
    s.set_defaults(f=cmd_batch)

    s = sub.add_parser("record")
    s.add_argument("--pass", dest="pass_", required=True)
    s.add_argument("--file")
    s.set_defaults(f=cmd_record)

    s = sub.add_parser("fulltext")
    s.add_argument("id"); s.add_argument("--url", action="append")
    s.add_argument("--chars",   type=int, default=5000)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(f=cmd_fulltext)

    s = sub.add_parser("expand-audit")
    s.add_argument("--all",        action="store_true")
    s.add_argument("--min-signal", type=int,   default=1)
    s.add_argument("--min-pct",    type=float, default=0.5)
    s.set_defaults(f=cmd_expand_audit)

    s = sub.add_parser("sweep")
    s.add_argument("--top",       type=int,   default=10)
    s.add_argument("--min-score", type=float, default=0.02)
    s.set_defaults(f=cmd_sweep)

    s = sub.add_parser("reconcile"); s.set_defaults(f=cmd_reconcile)
    s = sub.add_parser("validate");  s.set_defaults(f=cmd_validate)

    s = sub.add_parser("export")
    s.add_argument("--out-dir"); s.add_argument("--force", action="store_true")
    s.add_argument("--no-network", action="store_true",
                   help="skip arXiv link lookup (not needed; arXiv is fetched at Stage 0)")
    s.set_defaults(f=cmd_export)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
