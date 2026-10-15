"""
c_schema.py — constants, validator, and shared enumerations for paper-hunter.
"""
import re, unicodedata, difflib
from collections import defaultdict

ROLES               = ["baseline", "baseline_candidate", "benchmark", "evaluation", "survey"]
ROLE_TO_CONTRIB     = {"baseline": "agent_system", "baseline_candidate": "agent_system",
                       "benchmark": "benchmark_dataset", "evaluation": "evaluation_study",
                       "survey": "survey"}
CONTRIBS            = ["agent_system", "benchmark_dataset", "evaluation_study", "survey", "other"]
VERDICTS            = ["accept", "borderline", "reject"]
YNU                 = ["yes", "no", "unclear"]
SCOPES              = ["core", "broad", "adjacent", "none"]
PUB_STATUS          = ["published", "workshop_archival_toplevel", "workshop_other",
                       "preprint_only", "thesis_or_report", "unknown"]
PUB_OK_STRICT       = {"published", "workshop_archival_toplevel"}
REJECT_CLASSES      = ["off_domain", "security_other", "defensive_only",
                       "vuln_detection_or_code_scan", "generic_llm_security",
                       "static_qa_benchmark", "llm_no_offense",
                       "out_of_range", "duplicate", "retracted", "other"]
BORDER_REASONS      = ["adjacent_non_llm", "adjacent_single_shot", "adjacent_advisory_only",
                       "adjacent_defensive", "scope_broad", "not_peer_reviewed",
                       "workshop_unverified", "evidence_insufficient",
                       "criteria_unclear", "role_unclear", "other"]
ABSTRACT_SOURCES    = ["crossref", "openalex", "semanticscholar", "landing_page", "arxiv",
                       "manual_web", "fulltext", "none"]
EVIDENCE_SOURCES    = ["abstract", "title", "fulltext", "web"]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normtext(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    for ch, rep in [("\u2010", "-"), ("\u2011", "-"), ("\u2012", "-"),
                    ("\u2013", "-"), ("\u2014", "-")]:
        s = s.replace(ch, rep)
    s = re.sub(r'["\'`\u2018\u2019\u201c\u201d]', "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s\-]", " ", s)).strip()


def quote_in(quote, hay):
    q, h = normtext(quote), normtext(hay)
    if not q or not h:
        return False
    if q in h:
        return True
    sm = difflib.SequenceMatcher(None, q, h, autojunk=False)
    m = sm.find_longest_match(0, len(q), 0, len(h))
    return m.size >= 0.92 * len(q)


def validate_record(run, r, pass_, policy, dos=None):
    """Validate a single decision dict.  Returns list of error strings (empty = ok)."""
    errs = []
    if dos is None:
        dos = run.dossiers()
    pid = r.get("id")
    if pid not in dos:
        return ["unknown id %r" % pid]
    d = dos[pid]
    v = r.get("verdict")
    if v not in VERDICTS:
        errs.append("verdict must be one of %s" % VERDICTS)
    roles = r.get("roles", [])
    if not isinstance(roles, list) or any(x not in ROLES for x in roles):
        errs.append("roles must be a list drawn from %s" % ROLES)
        roles = []
    pc = r.get("primary_contribution")
    if pc not in CONTRIBS:
        errs.append("primary_contribution must be one of %s" % CONTRIBS)
    if r.get("subject_scope") not in SCOPES:
        errs.append("subject_scope must be one of %s" % SCOPES)
    crit = r.get("criteria") or {}
    off_domain = (v == "reject" and r.get("reject_class") in
                  ("off_domain", "security_other", "defensive_only",
                   "vuln_detection_or_code_scan", "generic_llm_security",
                   "static_qa_benchmark"))
    for k in ("llm_agent", "offensive_security", "black_box", "long_horizon"):
        if crit.get(k) not in YNU:
            if off_domain and k not in crit:
                crit[k] = "no"
                r["criteria"] = crit
            else:
                errs.append("criteria.%s must be yes|no|unclear" % k)
    if not isinstance(r.get("confidence"), (int, float)) or not (0 <= r["confidence"] <= 1):
        errs.append("confidence must be a number 0..1")
    reason = (r.get("reason") or "").strip()
    if len(reason) < 10:
        errs.append("reason is required (one or two sentences, <= 60 words)")
    elif len(reason.split()) > 80:
        errs.append("reason too long (<= 60 words)")
    if r.get("abstract_source") not in ABSTRACT_SOURCES + [None]:
        errs.append("abstract_source must be one of %s" % ABSTRACT_SOURCES)
    if r.get("publication_status") not in PUB_STATUS:
        errs.append("publication_status must be one of %s" % PUB_STATUS)
    if not isinstance(r.get("fulltext_read"), bool):
        errs.append("fulltext_read must be true/false")
    # evidence
    ev, es = (r.get("evidence") or "").strip(), r.get("evidence_source")
    if es not in EVIDENCE_SOURCES:
        errs.append("evidence_source must be one of %s" % EVIDENCE_SOURCES)
    elif len(ev.split()) < 4 or len(ev.split()) > 45:
        errs.append("evidence must be a verbatim quote of 4-45 words")
    elif es == "web":
        if not r.get("evidence_url"):
            errs.append("evidence_source=web requires evidence_url (URL where the quote was found)")
    elif not quote_in(ev, _hay_for(run, d, es)):
        errs.append(
            "evidence quote NOT found verbatim in the %s text of %s "
            "- copy it exactly (or use ph.py fulltext first)" % (es, pid))
    if es == "fulltext" and r.get("fulltext_read") is False:
        errs.append("evidence_source=fulltext but fulltext_read=false")
    # verdict logic
    if v == "reject":
        if r.get("reject_class") not in REJECT_CLASSES:
            errs.append("reject needs reject_class in %s" % REJECT_CLASSES)
        if roles:
            errs.append("reject must have roles=[]")
    if v == "borderline":
        if r.get("borderline_reason") not in BORDER_REASONS:
            errs.append("borderline needs borderline_reason in %s" % BORDER_REASONS)
    if v == "accept":
        if not roles:
            errs.append("accept needs at least one role")
        pr = r.get("primary_role")
        if pr not in roles:
            errs.append("accept needs primary_role contained in roles")
        elif pc != ROLE_TO_CONTRIB.get(pr):
            errs.append("primary_role=%s requires primary_contribution=%s"
                        % (pr, ROLE_TO_CONTRIB.get(pr)))
        if crit.get("llm_agent") != "yes" or crit.get("offensive_security") != "yes":
            errs.append(
                "accept requires criteria llm_agent=yes AND offensive_security=yes "
                "(judged on the system(s) the paper presents/surveys/benchmarks/evaluates)")
        # ── baseline (strict) ─────────────────────────────────────────────
        if "baseline" in roles:
            if r.get("primary_role") != "baseline":
                errs.append(
                    "baseline may only be the PRIMARY role. "
                    "It may also appear alongside secondary roles (e.g. [\"baseline\", \"benchmark\"]) "
                    "only when those are explicit secondary contributions; primary_role must still be 'baseline'.")
            if any(crit.get(k) != "yes"
                   for k in ("llm_agent", "offensive_security", "black_box", "long_horizon")):
                errs.append(
                    "baseline requires ALL FOUR criteria = yes "
                    "(3 and 4 under their soft definitions, on positive evidence)")
            if r.get("publication_status") not in PUB_OK_STRICT:
                errs.append(
                    "baseline requires publication_status published or workshop_archival_toplevel")
        # ── baseline_candidate (partial criteria) ─────────────────────────
        if "baseline_candidate" in roles:
            if r.get("primary_role") not in ("baseline_candidate", "baseline"):
                errs.append(
                    "baseline_candidate as a role requires primary_role=baseline_candidate "
                    "(use this when criteria 3 or 4 are only 'partial' or 'unclear')")
            if pc != "agent_system":
                errs.append(
                    "baseline_candidate requires primary_contribution=agent_system "
                    "(it IS an agent system, just with uncertain setting/horizon evidence)")
            if crit.get("llm_agent") != "yes" or crit.get("offensive_security") != "yes":
                errs.append(
                    "baseline_candidate requires llm_agent=yes AND offensive_security=yes; "
                    "criteria 3/4 may be partial/unclear")
        if r.get("subject_scope") not in ("core", "broad"):
            errs.append("accept requires subject_scope core or broad")
        if (r.get("publication_status") not in PUB_OK_STRICT and policy == "strict"
                and "baseline" in roles):
            errs.append(
                "publication_policy=strict: status %r cannot be a baseline - "
                "use verdict=borderline, borderline_reason=not_peer_reviewed/workshop_unverified"
                % r.get("publication_status"))
        if d.get("retracted"):
            errs.append("paper looks retracted")
    if pass_ == "F" and r.get("needs_deep_dive"):
        errs.append("pass F must be definitive (needs_deep_dive=false)")
    if (r.get("abstract_source") == "none"
            and d.get("abstract_source") != "none"):
        errs.append("abstract_source=none but the dossier has an abstract (%s)"
                    % d.get("abstract_source"))
    return errs


def _hay_for(run, d, src):
    """Return the text corpus for evidence-quote verification."""
    if src == "title":
        return d["title"]
    if src == "abstract":
        return (d.get("abstract") or "") + " " + (d.get("abstract_snippet") or "")
    if src == "fulltext":
        from pathlib import Path
        f = run.fulltext_dir / ("%s.txt" % d["id"])
        return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
    return ""
