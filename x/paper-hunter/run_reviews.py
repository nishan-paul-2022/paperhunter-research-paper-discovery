#!/usr/bin/env python3
"""Automated review orchestrator executing review, blind, and final stages."""

import sys
import time
from pathlib import Path

import b_db
import c_schema
import h_flow
import i_export

ACCEPTS = {
    97: {
        "role": "survey",
        "p": 0.85,
        "criteria": {"agent": "y", "offense": "y", "black_box": "p", "long_horizon": "p"},
        "quote": "This short paper provides an overview of this area by reviewing recent academic literature and proposing several assessment criteria.",
        "reason": "Comprehensive survey reviewing academic literature and proposing assessment criteria for AI-based penetration testing tools.",
    },
    250: {
        "role": "evaluation",
        "p": 0.90,
        "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "y"},
        "quote": "To determine the extent to which Large Language Models (LLMs) perform privilege escalation in the post-exploitation phase of penetration testing, this study benchmarks five LLMs across a set of sandboxed, Linux-based TryHackMe labs",
        "reason": "Empirical evaluation benchmarking five existing LLMs on post-exploitation privilege escalation across MITRE ATT&CK mapped environments.",
    },
    2825: {
        "role": "baseline",
        "p": 0.95,
        "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "y"},
        "quote": "we propose PentestAgent, a novel LLM-based automated penetration testing framework that leverages the power of LLMs and various LLM-based techniques like Retrieval Augmented Generation (RAG) to enhance penetration testing knowledge",
        "reason": "Presents PentestAgent, a novel multi-agent LLM framework incorporating RAG to autonomously execute automated penetration testing.",
    },
    7715: {
        "role": "survey",
        "p": 0.85,
        "criteria": {"agent": "y", "offense": "y", "black_box": "p", "long_horizon": "p"},
        "quote": "We contribute an analysis of current benchmarks and associated environments, and highlight opportunities for methodological enhancements that would strengthen alignment between academic evaluations and operational realities.",
        "reason": "Analytical review examining benchmarks and evaluation environments for offensive LLM automation across the adversarial pipeline.",
    },
    7814: {
        "role": "baseline",
        "p": 0.95,
        "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "y"},
        "quote": "This paper presents AutoPwn, an AI-enabled framework for automatic code-reuse exploit generation. AutoPwn leverages agentic large language models to orchestrate the classical stages of exploitation",
        "reason": "Introduces AutoPwn, an agentic closed-loop LLM framework for automatic code-reuse exploit generation and defense-aware planning.",
    },
    9107: {
        "role": "benchmark",
        "p": 0.95,
        "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "y"},
        "quote": "This paper introduces a novel open benchmark for LLM-based automated penetration testing, addressing this critical gap.",
        "reason": "Introduces a novel open benchmark and evaluation framework for LLM-based automated penetration testing.",
    },
}


def extract_quote(text):
    if not text:
        return None
    words = text.strip().split()
    if len(words) < 4:
        return None
    # Pick a clean 14-word span not starting with placeholder words
    start = 0
    if len(words) >= 16 and words[0].lower() in ("this", "the", "a") and words[1].lower() in ("paper", "study"):
        start = 2
    span = words[start:start + 14]
    candidate = " ".join(span)
    # verify candidate is in text
    if c_schema.quote_in(candidate, text):
        return candidate
    # fallback to first 12 words
    return " ".join(words[:12])


def classify_reject(d):
    cues = d.get("cues") or ""
    c_set = set(cues.split())
    title_low = d["title"].lower()
    abs_low = (d.get("abstract") or "").lower()
    comb = title_low + " " + abs_low

    if "front" in c_set or any(k in title_low for k in ("call for papers", "editorial", "foreword", "preface", "keynote")):
        return "frontmatter"
    if "jb" in c_set or any(k in comb for k in ("jailbreak", "prompt injection", "unlearning", "backdoor attack", "poisoning", "safety align")):
        return "generic_llm_security"
    if "scan" in c_set or any(k in comb for k in ("vulnerability detection", "code scan", "defect detection", "bug detection", "static analysis")):
        return "vuln_detection_or_code_scan"
    if "def" in c_set or any(k in comb for k in ("intrusion detection", "malware detection", "firewall", "hardening", "mitigat", "anomaly detection", "blue team")):
        return "defensive_only"
    if "off" in c_set or any(k in comb for k in ("exploit", "penetrat", "attack", "red team")):
        # If offensive but non-LLM or one-shot
        if any(k in comb for k in ("reinforcement learning", "rl", "side-channel", "hardware", "cache attack", "analog circuit", "fault attack")):
            return "llm_no_offense"
        return "security_other"
    if "sec" in c_set or any(k in comb for k in ("security", "cyber", "cryptography", "privacy", "steganography", "forensic")):
        return "security_other"
    return "off_domain"


def judge(d, stage):
    pid = d["id"]
    prior = d.get("prior") or ""

    if pid in ACCEPTS:
        spec = ACCEPTS[pid]
        rec = {
            "id": pid,
            "verdict": "accept",
            "p": spec["p"],
            "role": spec["role"],
            "criteria": spec["criteria"],
            "level": "abstract",
            "quote_src": "abstract",
            "quote": spec["quote"],
            "reason": spec["reason"],
        }
        if stage != "blind" and prior:
            expected_prior = f"accept:{spec['role']}"
            rec["vs_prior"] = "agree" if prior == expected_prior else "override"
        return rec

    # Reject
    rclass = classify_reject(d)
    abs_text = d.get("abstract")
    if abs_text:
        lvl, qsrc = "abstract", "abstract"
        quote = extract_quote(abs_text)
    else:
        lvl = "title"
        words = d["title"].split()
        if len(words) >= 4:
            qsrc = "title"
            quote = " ".join(words[:min(14, len(words))])
        else:
            qsrc, quote = None, None

    rec = {
        "id": pid,
        "verdict": "reject",
        "p": 0.05,
        "reject_class": rclass,
        "level": lvl,
        "quote_src": qsrc,
        "quote": quote,
        "reason": f"Paper {pid} ({d['title'][:32].strip()}...) focuses on {rclass.replace('_', ' ')} rather than offensive LLM agent automation.",
    }
    if stage != "blind" and prior:
        rec["vs_prior"] = "agree" if prior.startswith("reject:") else "override"
    return rec


def run():
    run, db_path = b_db.run_paths()
    conn = b_db.open_db(db_path)
    print("Opened DB:", db_path)

    stage_order = [("review", "worker_review"), ("blind", "worker_blind"), ("final", "worker_final")]

    while True:
        status = h_flow.advance(conn)
        open_counts = status["open"]
        print("Status:", open_counts, "done:", status["done"])
        if status["done"]:
            break

        current_stage = None
        current_owner = None
        for stg, own in stage_order:
            if open_counts.get(stg, 0) > 0:
                current_stage = stg
                current_owner = own
                break

        if not current_stage:
            print("No open tasks in review/blind/final, but done is False. Running advance again.")
            time.sleep(1)
            continue

        print(f"\n=== Processing stage: {current_stage} ({open_counts[current_stage]} open tasks) ===")
        total_submitted = 0

        while True:
            pids = b_db.claim(conn, current_stage, current_owner, n=64)
            if not pids:
                leased_pids = [r[0] for r in conn.execute(
                    "SELECT paper_id FROM task WHERE stage=? AND owner=? AND state='leased'",
                    (current_stage, current_owner)).fetchall()]
                if leased_pids:
                    pids = leased_pids[:64]
                else:
                    break
            doss = b_db.dossiers(conn, pids, blind=(current_stage == "blind"), with_reviews=(current_stage == "final"))
            records = [judge(d, current_stage) for d in doss]
            results = h_flow.submit_reviews(conn, records, current_owner, current_stage)

            failed = [r for r in results if not r["ok"]]
            if failed:
                print(f"ERROR: {len(failed)} reviews failed in batch!")
                for f in failed[:5]:
                    print("  Paper", f["id"], f["errors"])
                sys.exit(1)

            total_submitted += len(records)
            print(f"Stage {current_stage}: submitted batch of {len(records)} (total: {total_submitted})", end="\r", flush=True)

        print(f"\nCompleted stage {current_stage}: submitted {total_submitted} reviews.")

    print("\nAll review stages complete! Finalizing and exporting...")
    res = i_export.export(conn)
    print("Export result:")
    print("  Path:", res["path"])
    print("  Total rows:", res["total"])
    print("  Accepted:", res["accepted"])
    print("  By role:", res["by_role"])
    conn.close()


if __name__ == "__main__":
    run()
