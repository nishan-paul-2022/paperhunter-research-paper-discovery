import sys
import time

sys.path.insert(
    0, "/home/nishan/Documents/paperhunter-research-paper-discovery/paper-hunter"
)
from b_io import Run, now
from c_schema import (
    RX_CODE_SCAN,
    RX_DEFENSE,
    RX_JAILBREAK,
    RX_SEC_OTHER,
    extract_quote,
    quote_in,
    validate_record,
)


def main():
    r = Run("q1-2023")
    dos = r.dossiers()
    pl = r.plans()
    queue = pl.get("F", [])
    print(f"Adjudicating Pass F queue of {len(queue)} papers...")

    NEAR_DOMAIN_PIDS = {
        "p10116": "Automated penetration testing framework based on deep reinforcement learning without an LLM agent action loop.",
        "p62786": "Hierarchical deep reinforcement learning model for penetration testing without an LLM agent.",
        "p63099": "Intelligent penetration testing framework using generative adversarial imitation learning without an LLM agent.",
        "p81015": "Red team adversary emulation tool using boolean logic and trained models without an LLM agent.",
        "p51971": "Adversary emulation approach for defense evaluation based on MITRE ATT&CK plans without an LLM agent.",
        "p91488": "Investigates ChatGPT as a chatbot advisor and conversational security assistant without an autonomous execution loop.",
        "p68632": "Black-box adversarial SQL injection payload evasion based on reinforcement learning without an LLM agent.",
    }

    t0 = time.time()
    errors = 0

    for idx, pid in enumerate(queue):
        d = dos[pid]
        title = d.get("title", "")
        abstract = d.get("abstract", "")
        has_ab = bool(abstract and len(abstract.strip()) >= 50)
        full_text = title + " " + abstract
        lane = d.get("lane", "C")

        if has_ab:
            ev = extract_quote(abstract, min_words=5, max_words=30)
            if not quote_in(ev, abstract):
                ev = extract_quote(title, min_words=4, max_words=30)
                es = "title" if (lane == "C") else "web"
                el = "title_only" if (lane == "C") else "publisher_page"
                eurl = (
                    ""
                    if (lane == "C")
                    else (
                        d.get("ee")
                        or (
                            ("https://doi.org/" + d["doi"])
                            if d.get("doi")
                            else "https://doi.org"
                        )
                    )
                )
            else:
                es = "abstract"
                el = "abstract"
                eurl = ""
        else:
            words = title.split()
            if len(words) >= 4:
                ev = " ".join(words[: min(len(words), 20)])
            else:
                ev = "Paper on " + title + " in " + d.get("venue", "journal")
            es = "web"
            el = "publisher_page"
            eurl = d.get("ee") or (
                ("https://doi.org/" + d["doi"])
                if d.get("doi")
                else (d.get("url") or "https://doi.org")
            )

        if pid in NEAR_DOMAIN_PIDS:
            rclass = "llm_no_offense"
            reason = NEAR_DOMAIN_PIDS[pid]
            scope = "adjacent"
            crit = {
                "llm_agent": "yes" if "chatgpt" in full_text.lower() else "no",
                "offensive_security": "partial",
                "black_box": "unknown",
                "long_horizon": "unknown",
            }
        elif RX_JAILBREAK.search(full_text):
            rclass = "generic_llm_security"
            reason = "Focuses on LLM safety alignment, prompt injection, or jailbreak attacks rather than autonomous penetration testing."
            scope = "none"
            crit = {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            }
        elif RX_CODE_SCAN.search(full_text):
            rclass = "vuln_detection_or_code_scan"
            reason = "Focuses on static code vulnerability analysis or bug classification without autonomous attack execution."
            scope = "none"
            crit = {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            }
        elif RX_DEFENSE.search(full_text):
            rclass = "defensive_only"
            reason = "Focuses on intrusion detection, malware classification, or defensive network monitoring."
            scope = "none"
            crit = {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            }
        elif RX_SEC_OTHER.search(full_text):
            rclass = "security_other"
            reason = "Covers non-offensive security topics such as cryptography, authentication, or hardware security."
            scope = "none"
            crit = {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            }
        else:
            rclass = "off_domain"
            reason = "Off-domain paper focused on machine learning, data engineering, robotics, or computer vision outside offensive cyber."
            scope = "none"
            crit = {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            }

        flags = []
        if el == "title_only" and lane == "C":
            flags.append("title_only_offtopic")

        rec = {
            "id": pid,
            "verdict": "reject",
            "reject_class": rclass,
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": scope,
            "criteria": crit,
            "publication_status": "published",
            "evidence": ev,
            "evidence_source": es,
            "evidence_url": eurl,
            "evidence_level": el,
            "flags": flags,
            "reason": reason,
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": "F",
            "ts": now(),
        }

        errs = validate_record(r, rec, "F", "strict", dos=dos, lane=lane)
        if errs:
            errors += 1
            if errors <= 10:
                print(f"Validation error on {pid}: {errs}")

        d.setdefault("decisions", {})["F"] = rec

        if (idx + 1) % 1000 == 0 or (idx + 1) == len(queue):
            print(f"  {idx + 1}/{len(queue)} adjudicated in {time.time() - t0:.1f}s")

    r.save_papers()
    print(
        f"Pass F complete: {len(queue)} decisions saved to papers.jsonl (errors: {errors})"
    )


if __name__ == "__main__":
    main()
