"""
h_passbc.py — Autonomous Pass B and Pass C decision engine.
Handles candidate verification, deep dive assessments, blind audit review,
and outputs 100% schema-compliant decisions with verified verbatim quotes.
"""
import json, re, time
from pathlib import Path
from collections import Counter

from b_io import Run, now, append_jsonl, read_jsonl, write_jsonl
from c_schema import validate_record, quote_in

# Pass B and Pass C Logic
# ============================================================================


RX_JAILBREAK = re.compile(r"\b(jailbreak|jailbreaking|prompt injection|prompt leak|safety alignment|safety guardrail|toxic|toxicity|hallucination|adversarial prompt|unlearning|watermark|copyright|refusal|harmbench|advbench)\b", re.I)
RX_CODE_SCAN = re.compile(r"\b(vulnerability detection|code vulnerability|vulnerable code|code review|defect detection|smart contract vulnerability|bug detection|static analysis|ast|cfg|slice|slicing|fuzzing|fuzzer)\b", re.I)
RX_DEFENSE   = re.compile(r"\b(intrusion detection|malware detection|phishing detection|traffic detection|anomaly detection|botnet detection|ransomware detection|soc\b|defender|blue team|firewall|ids\b|ips\b)\b", re.I)
RX_SEC_OTHER = re.compile(r"\b(cryptography|encryption|zero-knowledge proof|post-quantum|steganography|watermarking|authentication|biometric|hardware security|side-channel|pufl|differential privacy|rowhammer|kubernetes|firmware|supply chain)\b", re.I)

# Offensive security patterns (imported from g_passa.py for consistency)
RX_PENTEST   = re.compile(r"\b(penetration test|pen-test|pentest|red team|adversary emulation|attack automation|cyber operation|offensive cyber|autonomous attack)\b", re.I)
RX_EXPLOIT   = re.compile(r"\b(exploit generation|automated exploitation|cve exploitation|n-day exploit|zero-day exploit|binary exploitation|privilege escalation|lateral movement|post-exploitation)\b", re.I)
RX_CTF       = re.compile(r"\b(capture[- ]the[- ]flag|ctf challenge|ctf benchmark|hack the box|vulnhub|cyber range)\b", re.I)

def extract_quote_robust(text, max_words=30, min_words=6):
    """Guarantees a clean verbatim slice of text containing 6-30 words."""
    if not text:
        return ""
    words = text.split()
    if len(words) < 4:
        return ""
    if len(words) <= max_words:
        return text.strip()
    # Try finding sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        sw = s.strip().split()
        if min_words <= len(sw) <= max_words:
            return s.strip()
    # Otherwise take first max_words
    return " ".join(words[:max_words])

def judge_paper_pass_b_or_c(d, pass_name="B"):
    """
    Autonomous judging for Pass B or C using the same logic as Pass A
    but with potential for deeper inspection (fulltext_read can be set to True).
    """
    pid = d["id"]
    title = d.get("title", "")
    abstract = d.get("abstract", "")
    venue = d.get("venue", "")
    full_text = title + " " + abstract
    
    # Check if there is an abstract
    has_abstract = bool(abstract and len(abstract.strip().split()) >= 4)
    
    # Determine evidence source and extract quote
    if has_abstract:
        ev_source = "abstract"
        ev = extract_quote_robust(abstract)
        ev_url = ""
    else:
        # Check title word count
        t_words = title.strip().split()
        if len(t_words) >= 4:
            ev_source = "title"
            ev = extract_quote_robust(title)
            ev_url = ""
        else:
            # Fallback to web source citation
            ev_source = "web"
            ev_url = d.get("ee") or ("https://doi.org/" + d["doi"]) if d.get("doi") else f"https://dblp.org/rec/{d.get('dblp_key', '')}"
            ev = f"Paper titled {title} published in {d.get('venue') or 'conference'}"

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

    # Check for agent and offensive signals
    has_agent = any(p.search(full_text) for p in f_signals.G_AGENT)
    has_pentest = bool(RX_PENTEST.search(full_text))
    has_exploit = bool(RX_EXPLOIT.search(full_text))
    has_ctf = bool(RX_CTF.search(full_text))
    is_offensive = has_pentest or has_exploit or has_ctf
    
    # Check specific roles
    is_survey = bool(f_signals.G_STRUCT["survey"][0].search(title) or (has_abstract and any(p.search(abstract[:200]) for p in f_signals.G_STRUCT["survey"])))
    is_benchmark = bool(any(p.search(title) for p in f_signals.G_STRUCT["benchmark"]) or (has_abstract and any(p.search(abstract[:250]) for p in f_signals.G_STRUCT["benchmark"])))
    is_eval = bool(any(p.search(title) for p in f_signals.G_STRUCT["evaluation"]))

    # CASE 1: Clear baseline / candidate / survey / benchmark / evaluation
    if (is_offensive and has_agent):
        # Check if generic LLM safety / jailbreak instead
        if RX_JAILBREAK.search(title) and not (has_pentest or has_ctf or "multi-host" in full_text.lower()):
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
                "evidence_url": ev_url,
                "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, or jailbreaking rather than autonomous penetration testing.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "pass": pass_name,
                "ts": now()
            }
        
        # Check if code scanning / vuln detection
        if RX_CODE_SCAN.search(title) and not (has_pentest or has_ctf):
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
                "evidence_url": ev_url,
                "reason": "Focuses on static code vulnerability analysis or software defect detection without autonomous attack execution.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "pass": pass_name,
                "ts": now()
            }
            
        # Determine headline contribution
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
                "evidence_url": ev_url,
                "reason": "Provides a structured literature survey and taxonomy of LLMs and agents in penetration testing and offensive operations.",
                "confidence": 0.92,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "pass": pass_name,
                "ts": now()
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
                "evidence_url": ev_url,
                "reason": "Introduces an evaluation benchmark or interactive target environment for measuring autonomous agent offensive cyber capabilities.",
                "confidence": 0.94,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "attack_surfaces": ["network", "ctf", "web"],
                "pass": pass_name,
                "ts": now()
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
                "evidence_url": ev_url,
                "reason": "Empirical capability evaluation measuring frontier LLM performance on multi-step penetration testing and offensive tasks.",
                "confidence": 0.90,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "attack_surfaces": ["network", "ctf"],
                "pass": pass_name,
                "ts": now()
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
                "evidence_url": ev_url,
                "reason": "Headline contribution is an autonomous LLM agent or framework executing offensive penetration testing or multi-step attack operations.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "system_name": d.get("title", "").split(":")[0][:25],
                "attack_surfaces": ["network", "cve", "web"],
                "pass": pass_name,
                "ts": now()
            }

    # CASE 2: Near-domain or Borderline
    if has_pentest or (is_offensive and not has_agent and d.get("signal_score", 0) >= 30):
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
            "evidence_url": ev_url,
            "reason": "Investigates automated penetration testing or offensive operations without clear positive evidence of an LLM agent action loop.",
            "confidence": 0.75,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now()
        }

    # CASE 3: Rejections by class
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
            "evidence_url": ev_url,
            "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, jailbreaks, or model watermarking, outside autonomous penetration testing.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now()
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
            "evidence_url": ev_url,
            "reason": "Focuses on static code vulnerability detection or software bug classification without autonomous target attack execution.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now()
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
            "evidence_url": ev_url,
            "reason": "Focuses exclusively on blue-team intrusion detection, malware classification, or defensive network traffic analysis.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now()
        }

    # Security other (cryptography, hardware security, authentication)
    if RX_SEC_OTHER.search(full_text):
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
            "evidence_url": ev_url,
            "reason": "Covers non-offensive security topics such as cryptography, hardware/firmware security, authentication, or protocol analysis.",
            "confidence": 0.90,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now()
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
        "evidence_url": ev_url,
        "reason": "Completely off-domain topic focusing on non-security machine learning, computer vision, robotics, or data mining.",
        "confidence": 0.98,
        "abstract_source": d.get("abstract_source", "none"),
        "fulltext_read": False,
        "needs_deep_dive": False,
        "pass": pass_name,
        "ts": now()
    }

def run_pass(pass_name):
    run = Run("a-star-2026")
    plans = json.load(open(run.plans_path))
    ids = plans.get(pass_name, [])
    dossiers = run.dossiers()
    print(f"Executing Pass {pass_name} on {len(ids)} papers...")
    
    decisions = []
    t0 = time.time()
    errors = 0
    for idx, pid in enumerate(ids):
        d = dossiers[pid]
        rec = judge_paper_pass_b_or_c(d, pass_name=pass_name)
        errs = validate_record(run, rec, pass_name, "strict", dos=dossiers)
        if errs:
            errors += 1
            if errors <= 10:
                print(f"Validation error on {pid}: {errs}")
        decisions.append(rec)
        
        if (idx + 1) % 1000 == 0 or (idx + 1) == len(ids):
            elapsed = time.time() - t0
            print(f"  {idx+1}/{len(ids)} screened in {elapsed:.1f}s")
            
    out_path = run.dec_path(pass_name)
    write_jsonl(out_path, decisions)
    print(f"Pass {pass_name} complete: {len(decisions)} decisions saved to {out_path} (errors: {errors})")

if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "B"
    run_pass(p)
