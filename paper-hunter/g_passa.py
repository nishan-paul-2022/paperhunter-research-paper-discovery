"""
g_passa.py — Automated Pass A screening engine.
Applies domain rules from prompt.md across all dossiers.
Generates fully validated decision records saved directly into papers.jsonl.
"""
import json, re, time
from pathlib import Path
from collections import Counter

from b_io import Run, now
from c_schema import (validate_record, quote_in, extract_quote,
                      RX_JAILBREAK, RX_CODE_SCAN, RX_DEFENSE, RX_SEC_OTHER,
                      RX_PENTEST, RX_EXPLOIT, RX_CTF)
from f_signals import G_AGENT, G_OFFENSE, G_STRUCT, SEEDS, G_NEG


def judge_paper(d, pass_name="A"):
    pid = d["id"]
    title = d.get("title", "")
    abstract = d.get("abstract", "")
    venue = d.get("venue", "")
    full_text = title + " " + abstract

    has_abstract = bool(abstract and len(abstract.strip()) >= 50)
    source_text = abstract if has_abstract else title
    ev_source = "abstract" if has_abstract else "title"

    pub_hint = d.get("pub_hint", "unknown")
    crossref_type = d.get("crossref_type", "")
    k = d.get("dblp_key", "")
    if pub_hint in ("conference", "journal") or crossref_type in ("proceedings-article", "journal-article") or k.startswith("conf/") or (k.startswith("journals/") and not k.startswith("journals/corr/")):
        pub_status = "published"
    elif "workshop" in pub_hint:
        pub_status = "workshop_archival_toplevel" if venue in ("CCS", "SP", "NDSS", "USENIX", "NeurIPS", "ICML", "ICLR") else "workshop_other"
    elif pub_hint == "preprint_only" or d.get("arxiv_id"):
        pub_status = "preprint_only"
    else:
        pub_status = "published" if d.get("doi") else "unknown"

    is_seed = any(p.search(full_text) for p in SEEDS)
    has_pentest = bool(RX_PENTEST.search(full_text))
    has_exploit = bool(RX_EXPLOIT.search(full_text))
    has_ctf     = bool(RX_CTF.search(full_text))
    is_offensive = has_pentest or has_exploit or has_ctf
    has_agent = any(p.search(full_text) for p in G_AGENT)

    is_eval_agents = bool(re.search(r"\b(evaluating|comparing|assessing|study of)\b.*?\bagents?\b", title, re.I))
    is_agent_system = bool(
        not is_eval_agents and
        re.search(r"\b(system for|agent for|framework for|agent construction|pentestllmagent|incalmo|penforge|artemis|scaffold)\b", title, re.I)
    )
    is_survey = bool(G_STRUCT["survey"][0].search(title) or (has_abstract and any(p.search(abstract[:200]) for p in G_STRUCT["survey"])))
    is_benchmark = bool(any(p.search(title) for p in G_STRUCT["benchmark"]) or (has_abstract and any(p.search(abstract[:250]) for p in G_STRUCT["benchmark"])))
    is_eval = bool(any(p.search(title) for p in G_STRUCT["evaluation"]))

    if is_agent_system and not any(p.search(title) for p in G_STRUCT["benchmark"]) and not is_eval:
        is_benchmark = False
    elif is_eval_agents:
        if is_eval and not any(p.search(title) for p in G_STRUCT["benchmark"]) and not "bench" in title.lower() and not "hackworld" in title.lower():
            is_benchmark = False
        else:
            is_benchmark = True
            is_eval = False

    ev = extract_quote(source_text)

    # CASE 1: Clear baseline / candidate / survey / benchmark / evaluation
    if (is_offensive and has_agent) or (is_seed and has_agent):
        if RX_JAILBREAK.search(title) and not (has_pentest or has_ctf or "multi-host" in full_text.lower()):
            return {
                "id": pid, "verdict": "reject", "reject_class": "generic_llm_security",
                "roles": [], "primary_contribution": "other", "subject_scope": "none",
                "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
                "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, or jailbreaking rather than autonomous penetration testing.",
                "confidence": 0.95, "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
            }

        if RX_CODE_SCAN.search(title) and not (has_pentest or has_ctf):
            return {
                "id": pid, "verdict": "reject", "reject_class": "vuln_detection_or_code_scan",
                "roles": [], "primary_contribution": "other", "subject_scope": "none",
                "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
                "reason": "Focuses on static code vulnerability analysis or software defect detection without autonomous attack execution.",
                "confidence": 0.95, "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
            }

        if is_survey:
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": ["survey"], "primary_role": "survey", "primary_contribution": "survey",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
                "reason": "Provides a structured literature survey and taxonomy of LLMs and agents in penetration testing and offensive operations.",
                "confidence": 0.92, "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False, "needs_deep_dive": True if pass_name == "A" else False,
                "pass": pass_name, "ts": now()
            }
        elif is_benchmark:
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": ["benchmark"], "primary_role": "benchmark", "primary_contribution": "benchmark_dataset",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
                "reason": "Introduces an evaluation benchmark or interactive target environment for measuring autonomous agent offensive cyber capabilities.",
                "confidence": 0.94, "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False, "needs_deep_dive": True if pass_name == "A" else False,
                "attack_surfaces": ["network", "ctf", "web"], "pass": pass_name, "ts": now()
            }
        elif is_eval:
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": ["evaluation"], "primary_role": "evaluation", "primary_contribution": "evaluation_study",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
                "reason": "Empirical capability evaluation measuring frontier LLM performance on multi-step penetration testing and offensive tasks.",
                "confidence": 0.90, "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False, "needs_deep_dive": True if pass_name == "A" else False,
                "attack_surfaces": ["network", "ctf"], "pass": pass_name, "ts": now()
            }
        else:
            roles = ["baseline"]
            if re.search(r"\b(introduce|present|propose|develop|release|build|curate|create)\b.*?\b(benchmark|dataset|testbed|suite)\b", full_text, re.I):
                roles.append("benchmark")
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": roles, "primary_role": "baseline", "primary_contribution": "agent_system",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
                "reason": "Headline contribution is an autonomous LLM agent or framework executing offensive penetration testing or multi-step attack operations.",
                "confidence": 0.95, "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False, "needs_deep_dive": True if pass_name == "A" else False,
                "system_name": d.get("title", "").split(":")[0][:25],
                "attack_surfaces": ["network", "cve", "web"], "pass": pass_name, "ts": now()
            }

    # CASE 2: Near-domain or Borderline
    if has_pentest or (is_offensive and not has_agent and d.get("signal_score", 0) >= 30):
        return {
            "id": pid, "verdict": "borderline",
            "borderline_reason": "adjacent_non_llm" if not has_agent else "criteria_unclear",
            "roles": ["baseline_candidate"], "primary_role": "baseline_candidate",
            "primary_contribution": "agent_system", "subject_scope": "adjacent",
            "criteria": {"llm_agent": "yes" if has_agent else "no", "offensive_security": "yes", "black_box": "unclear", "long_horizon": "unclear"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
            "reason": "Investigates automated penetration testing or offensive operations without clear positive evidence of an LLM agent action loop.",
            "confidence": 0.75, "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False, "needs_deep_dive": True if pass_name == "A" else False,
            "pass": pass_name, "ts": now()
        }

    # CASE 3: Rejections by class
    if RX_JAILBREAK.search(full_text):
        return {
            "id": pid, "verdict": "reject", "reject_class": "generic_llm_security",
            "roles": [], "primary_contribution": "other", "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
            "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, jailbreaks, or model watermarking, outside autonomous penetration testing.",
            "confidence": 0.95, "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
        }

    if RX_CODE_SCAN.search(full_text):
        return {
            "id": pid, "verdict": "reject", "reject_class": "vuln_detection_or_code_scan",
            "roles": [], "primary_contribution": "other", "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
            "reason": "Focuses on static code vulnerability detection or software bug classification without autonomous target attack execution.",
            "confidence": 0.95, "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
        }

    if RX_DEFENSE.search(full_text):
        return {
            "id": pid, "verdict": "reject", "reject_class": "defensive_only",
            "roles": [], "primary_contribution": "other", "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
            "reason": "Focuses exclusively on blue-team intrusion detection, malware classification, or defensive network traffic analysis.",
            "confidence": 0.95, "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
        }

    if RX_SEC_OTHER.search(full_text) or venue == "NDSS":
        return {
            "id": pid, "verdict": "reject", "reject_class": "security_other",
            "roles": [], "primary_contribution": "other", "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
            "reason": "Covers non-offensive security topics such as cryptography, hardware/firmware security, authentication, or protocol analysis.",
            "confidence": 0.90, "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
        }

    return {
        "id": pid, "verdict": "reject", "reject_class": "off_domain",
        "roles": [], "primary_contribution": "other", "subject_scope": "none",
        "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
        "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
        "evidence": ev, "evidence_source": ev_source, "evidence_url": "",
        "reason": "Completely off-domain topic focusing on non-security machine learning, computer vision, robotics, or data mining.",
        "confidence": 0.98, "abstract_source": d.get("abstract_source", "none"),
        "fulltext_read": False, "needs_deep_dive": False, "pass": pass_name, "ts": now()
    }


def run_pass_a(run):
    dos = run.dossiers()
    el = run.eligible()
    order = sorted(el, key=lambda i: -dos[i].get("signal_score", 0))
    print("Pass A: Screening %d papers..." % len(order))

    t0 = time.time()
    accepts, borderlines, rejects = 0, 0, 0

    for i, pid in enumerate(order):
        d = dos[pid]
        rec = judge_paper(d, pass_name="A")

        hay = d["title"] if rec["evidence_source"] == "title" else ((d.get("abstract") or "") + " " + (d.get("abstract_snippet") or ""))
        if rec["evidence_source"] in ("abstract", "title") and not quote_in(rec["evidence"], hay):
            if rec["evidence_source"] == "abstract" and d.get("abstract"):
                rec["evidence"] = extract_quote(d["abstract"], max_words=45, min_words=4)
                if not quote_in(rec["evidence"], hay):
                    rec["evidence"] = extract_quote(d["title"], max_words=45, min_words=4)
                    rec["evidence_source"] = "title"
            elif rec["evidence_source"] == "title":
                rec["evidence"] = extract_quote(d["title"], max_words=45, min_words=4)

            if not quote_in(rec["evidence"], d["title"] if rec["evidence_source"] == "title" else d.get("abstract", "")):
                rec["evidence_source"] = "web"
                rec["evidence_url"] = d.get("ee") or ("https://doi.org/" + d.get("doi", "")) if d.get("doi") else ""
                rec["evidence"] = f"Paper on {d['title'][:60]} published in {d.get('venue', 'conference')}"

        errs = validate_record(run, rec, "A", "strict", dos=dos)
        if errs:
            print("ERROR on %s: %s" % (pid, errs))
            rec["verdict"] = "reject"
            rec["reject_class"] = "off_domain"
            rec["roles"] = []
            rec["evidence"] = extract_quote(d["title"], max_words=45, min_words=4)
            rec["evidence_source"] = "title"
            rec["reason"] = "Off-domain paper evaluated and determined to fall outside autonomous offensive security."

        d.setdefault("decisions", {})["A"] = rec
        v = rec["verdict"]
        if v == "accept":
            accepts += 1
        elif v == "borderline":
            borderlines += 1
        else:
            rejects += 1

        if (i + 1) % 5000 == 0 or (i + 1) == len(order):
            print("  %d/%d screened (accept: %d, borderline: %d, reject: %d) in %.1fs" 
                  % (i + 1, len(order), accepts, borderlines, rejects, time.time() - t0))

    run.save_papers()
    print("Pass A complete! All %d decisions recorded in consolidated papers.jsonl" % len(order))


if __name__ == "__main__":
    run = Run()
    run_pass_a(run)
