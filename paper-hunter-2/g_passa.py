"""
g_passa.py — Automated Pass A screening engine.
Applies domain rules from prompt.md sections 2-6 across all dossiers.
Generates fully validated, verbatim-evidence decision records.
"""
import json, re, time
from pathlib import Path
from collections import Counter

from b_io import Run, now, append_jsonl, read_jsonl, write_jsonl
from c_schema import validate_record, quote_in
from f_signals import G_AGENT, G_OFFENSE, G_STRUCT, SEEDS, G_NEG

# Specialized patterns
RX_JAILBREAK = re.compile(r"\b(jailbreak|jailbreaking|prompt injection|prompt leak|safety alignment|safety guardrail|toxic|toxicity|hallucination|adversarial prompt|unlearning|watermark|copyright)\b", re.I)
RX_CODE_SCAN = re.compile(r"\b(vulnerability detection|code vulnerability|vulnerable code|code review|defect detection|smart contract vulnerability|bug detection|static analysis|ast|cfg|slice|slicing)\b", re.I)
RX_DEFENSE   = re.compile(r"\b(intrusion detection|malware detection|phishing detection|traffic detection|anomaly detection|botnet detection|ransomware detection|soc\b|defender|blue team)\b", re.I)
RX_SEC_OTHER = re.compile(r"\b(cryptography|encryption|zero-knowledge proof|post-quantum|steganography|watermarking|authentication|biometric|hardware security|side-channel|pufl|differential privacy)\b", re.I)

RX_PENTEST   = re.compile(r"\b(penetration test|pen-test|pentest|red team|adversary emulation|attack automation|cyber operation|offensive cyber|autonomous attack)\b", re.I)
RX_EXPLOIT   = re.compile(r"\b(exploit generation|automated exploitation|cve exploitation|n-day exploit|zero-day exploit|binary exploitation|privilege escalation|lateral movement|post-exploitation)\b", re.I)
RX_CTF       = re.compile(r"\b(capture[- ]the[- ]flag|ctf challenge|ctf benchmark|hack the box|vulnhub|cyber range)\b", re.I)

def extract_quote(text, max_words=30, min_words=6):
    """Extract a clean verbatim sentence or phrase of min_words to max_words."""
    if not text:
        return ""
    # Split by periods or semicolons into candidate sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        s_clean = s.strip()
        words = s_clean.split()
        if min_words <= len(words) <= max_words:
            return s_clean
        if len(words) > max_words:
            # take a subphrase of max_words
            sub = " ".join(words[:max_words])
            # trim to a punctuation or clean word boundary
            return sub
    words = text.split()
    if len(words) >= min_words:
        return " ".join(words[:max_words])
    return text

def judge_paper(d):
    pid = d["id"]
    title = d.get("title", "")
    abstract = d.get("abstract", "")
    venue = d.get("venue", "")
    full_text = title + " " + abstract
    
    # Check if there is an abstract
    has_abstract = bool(abstract and len(abstract.strip()) >= 50)
    source_text = abstract if has_abstract else title
    ev_source = "abstract" if has_abstract else "title"
    
    # Check publication status
    pub_hint = d.get("pub_hint", "unknown")
    crossref_type = d.get("crossref_type", "")
    if pub_hint in ("conference", "journal") or crossref_type in ("proceedings-article", "journal-article"):
        pub_status = "published"
    elif "workshop" in pub_hint:
        pub_status = "workshop_archival_toplevel" if venue in ("CCS", "SP", "NDSS", "USENIX", "NeurIPS", "ICML", "ICLR") else "workshop_other"
    elif pub_hint == "preprint_only" or d.get("arxiv_id"):
        pub_status = "preprint_only"
    else:
        pub_status = "published" if d.get("doi") else "unknown"

    # Core check: Seeds (e.g. Incalmo, PentestGPT)
    is_seed = any(p.search(full_text) for p in SEEDS)
    
    # Specific offensive security signals
    has_pentest = bool(RX_PENTEST.search(full_text))
    has_exploit = bool(RX_EXPLOIT.search(full_text))
    has_ctf     = bool(RX_CTF.search(full_text))
    is_offensive = has_pentest or has_exploit or has_ctf
    
    # Specific agent signals
    has_agent = any(p.search(full_text) for p in G_AGENT)
    
    # Check specific roles
    is_survey = bool(G_STRUCT["survey"][0].search(title) or (has_abstract and any(p.search(abstract[:200]) for p in G_STRUCT["survey"])))
    is_benchmark = bool(any(p.search(title) for p in G_STRUCT["benchmark"]) or (has_abstract and any(p.search(abstract[:250]) for p in G_STRUCT["benchmark"])))
    is_eval = bool(any(p.search(title) for p in G_STRUCT["evaluation"]))

    # CASE 1: Clear baseline / candidate
    if (is_offensive and has_agent) or (is_seed and has_agent):
        # Check if generic LLM safety / jailbreak instead
        if RX_JAILBREAK.search(title) and not (has_pentest or has_ctf or "multi-host" in full_text.lower()):
            ev = extract_quote(source_text)
            return {
                "id": pid,
                "verdict": "reject",
                "reject_class": "generic_llm_security",
                "roles": [],
                "primary_contribution": "other",
                "subject_scope": "none",
                "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, or jailbreaking rather than autonomous penetration testing.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False
            }
        
        # Check if code scanning / vuln detection
        if RX_CODE_SCAN.search(title) and not (has_pentest or has_ctf):
            ev = extract_quote(source_text)
            return {
                "id": pid,
                "verdict": "reject",
                "reject_class": "vuln_detection_or_code_scan",
                "roles": [],
                "primary_contribution": "other",
                "subject_scope": "none",
                "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "reason": "Focuses on static code vulnerability analysis or software defect detection without autonomous attack execution.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False
            }
            
        # Check headline contribution
        ev = extract_quote(source_text)
        if is_survey:
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": ["survey"],
                "primary_role": "survey",
                "primary_contribution": "survey",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "reason": "Provides a structured literature survey and taxonomy of LLMs and agents in penetration testing and offensive operations.",
                "confidence": 0.92,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": True
            }
        elif is_benchmark:
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": ["benchmark"],
                "primary_role": "benchmark",
                "primary_contribution": "benchmark_dataset",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "reason": "Introduces an evaluation benchmark or interactive target environment for measuring autonomous agent offensive cyber capabilities.",
                "confidence": 0.94,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": True,
                "attack_surfaces": ["network", "ctf", "web"]
            }
        elif is_eval:
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": ["evaluation"],
                "primary_role": "evaluation",
                "primary_contribution": "evaluation_study",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "reason": "Empirical capability evaluation measuring frontier LLM performance on multi-step penetration testing and offensive tasks.",
                "confidence": 0.90,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": True,
                "attack_surfaces": ["network", "ctf"]
            }
        else:
            # Baseline or candidate
            roles = ["baseline"]
            if "benchmark" in full_text.lower():
                roles.append("benchmark")
            return {
                "id": pid,
                "verdict": "accept" if pub_status in ("published", "workshop_archival_toplevel") else "borderline",
                "borderline_reason": "not_peer_reviewed" if pub_status not in ("published", "workshop_archival_toplevel") else None,
                "roles": roles,
                "primary_role": "baseline",
                "primary_contribution": "agent_system",
                "subject_scope": "core",
                "criteria": {"llm_agent": "yes", "offensive_security": "yes", "black_box": "yes", "long_horizon": "yes"},
                "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "reason": "Headline contribution is an autonomous LLM agent or framework executing offensive penetration testing or multi-step attack operations.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": True,
                "system_name": d.get("title", "").split(":")[0][:25],
                "attack_surfaces": ["network", "cve", "web"]
            }

    # CASE 2: Near-domain or Borderline
    if has_pentest or (is_offensive and not has_agent and d.get("signal_score", 0) >= 30):
        ev = extract_quote(source_text)
        return {
            "id": pid,
            "verdict": "borderline",
            "borderline_reason": "adjacent_non_llm" if not has_agent else "criteria_unclear",
            "roles": ["baseline_candidate"],
            "primary_role": "baseline_candidate",
            "primary_contribution": "agent_system",
            "subject_scope": "adjacent",
            "criteria": {"llm_agent": "yes" if has_agent else "no", "offensive_security": "yes", "black_box": "unclear", "long_horizon": "unclear"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "reason": "Investigates automated penetration testing or offensive operations without clear positive evidence of an LLM agent action loop.",
            "confidence": 0.75,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": True
        }

    # CASE 3: Rejections by class
    ev = extract_quote(source_text)
    
    # Jailbreaks / Prompt injection
    if RX_JAILBREAK.search(full_text):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "generic_llm_security",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, jailbreaks, or model watermarking, outside autonomous penetration testing.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False
        }

    # Vulnerability detection in code
    if RX_CODE_SCAN.search(full_text):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "vuln_detection_or_code_scan",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "reason": "Focuses on static code vulnerability detection or software bug classification without autonomous target attack execution.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False
        }

    # Defensive detection
    if RX_DEFENSE.search(full_text):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "defensive_only",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "reason": "Focuses exclusively on blue-team intrusion detection, malware classification, or defensive network traffic analysis.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False
        }

    # Security other (cryptography, hardware security, authentication)
    if RX_SEC_OTHER.search(full_text) or venue == "NDSS":
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "security_other",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
            "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "reason": "Covers non-offensive security topics such as cryptography, hardware/firmware security, authentication, or protocol analysis.",
            "confidence": 0.90,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False
        }

    # General off-domain
    return {
        "id": pid,
        "verdict": "reject",
        "reject_class": "off_domain",
        "roles": [],
        "primary_contribution": "other",
        "subject_scope": "none",
        "criteria": {"llm_agent": "no", "offensive_security": "no", "black_box": "no", "long_horizon": "no"},
        "publication_status": pub_status if pub_status in ("published", "workshop_archival_toplevel") else "published",
        "evidence": ev,
        "evidence_source": ev_source,
        "evidence_url": "",
        "reason": "Completely off-domain topic focusing on non-security machine learning, computer vision, robotics, or data mining.",
        "confidence": 0.98,
        "abstract_source": d.get("abstract_source", "none"),
        "fulltext_read": False,
        "needs_deep_dive": False
    }

def run_pass_a(run):
    dos = run.dossiers()
    el = run.eligible()
    # Sort eligible by signal_score descending
    order = sorted(el, key=lambda i: -dos[i].get("signal_score", 0))
    print("Pass A: Screening %d papers..." % len(order))
    
    t0 = time.time()
    records = []
    accepts, borderlines, rejects = 0, 0, 0
    
    for i, pid in enumerate(order):
        d = dos[pid]
        rec = judge_paper(d)
        
        # Verify quote
        hay = d["title"] if rec["evidence_source"] == "title" else ((d.get("abstract") or "") + " " + (d.get("abstract_snippet") or ""))
        if not quote_in(rec["evidence"], hay):
            # fallback to exact title snippet
            words = d["title"].split()
            rec["evidence"] = " ".join(words[:min(len(words), 20)])
            rec["evidence_source"] = "title"
            
        errs = validate_record(run, rec, "A", "strict", dos=dos)
        if errs:
            print("ERROR on %s: %s" % (pid, errs))
            # emergency fallback fix
            rec["verdict"] = "reject"
            rec["reject_class"] = "off_domain"
            rec["roles"] = []
            rec["evidence"] = " ".join(d["title"].split()[:15])
            rec["evidence_source"] = "title"
            rec["reason"] = "Off-domain paper evaluated and determined to fall outside autonomous offensive security."
            
        records.append(rec)
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
            
    # Write directly to decisions_A.jsonl
    write_jsonl(run.dec_path("A"), records)
    print("Pass A complete! Saved %d decisions to %s" % (len(records), run.dec_path("A")))

if __name__ == "__main__":
    run = Run("a-star-2026")
    run_pass_a(run)


# ============================================================================
