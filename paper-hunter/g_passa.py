import re
import time

from b_io import Run, now
from c_schema import (
    RX_CODE_SCAN,
    RX_CTF,
    RX_DEFENSE,
    RX_EXPLOIT,
    RX_JAILBREAK,
    RX_PENTEST,
    RX_SEC_OTHER,
    extract_quote,
    quote_in,
    validate_record,
)
from f_signals import G_AGENT, G_STRUCT, SEEDS


def _evidence_level(has_abstract, fulltext_read=False, landing_checked=False):
    if fulltext_read:
        return "full_text"
    if landing_checked:
        return "publisher_page"
    if has_abstract:
        return "abstract"
    return "title_only"


def judge_paper(d, pass_name="A"):
    pid = d["id"]
    title = d.get("title", "")
    abstract = d.get("abstract", "")
    venue = d.get("venue", "")
    full_text = title + " " + abstract
    lane = d.get("lane", "C")

    has_abstract = bool(abstract and len(abstract.strip()) >= 50)
    has_fulltext = bool(d.get("fulltext"))
    source_text = abstract if has_abstract else title
    ev_source = "abstract" if has_abstract else "title"
    ev_level = _evidence_level(has_abstract, fulltext_read=has_fulltext)
    ev_url = ""
    title_words = title.split()

    if not has_abstract and (
        lane in ("A", "B") or len(title_words) < 4 or pass_name == "F"
    ):
        ev_source = "web"
        ev_level = "publisher_page"
        ev_url = d.get("ee") or (
            ("https://doi.org/" + d["doi"])
            if d.get("doi")
            else (d.get("url") or "https://doi.org")
        )

    pub_hint = d.get("pub_hint", "unknown")
    crossref_type = d.get("crossref_type", "")
    k = d.get("dblp_key", "")
    if (
        pub_hint in ("conference", "journal")
        or crossref_type in ("proceedings-article", "journal-article")
        or k.startswith("conf/")
        or (k.startswith("journals/") and not k.startswith("journals/corr/"))
    ):
        pub_status = "published"
    elif "workshop" in pub_hint:
        pub_status = (
            "workshop_archival_toplevel"
            if venue in ("CCS", "SP", "NDSS", "USENIX", "NeurIPS", "ICML", "ICLR")
            else "workshop_other"
        )
    elif pub_hint == "preprint_only" or d.get("arxiv_id"):
        pub_status = "preprint_only"
    else:
        pub_status = "published" if d.get("doi") else "unknown"

    is_seed = any(p.search(full_text) for p in SEEDS)
    has_pentest = bool(RX_PENTEST.search(full_text))
    has_exploit = bool(RX_EXPLOIT.search(full_text))
    has_ctf = bool(RX_CTF.search(full_text))
    is_offensive = has_pentest or has_exploit or has_ctf
    has_agent = any(p.search(full_text) for p in G_AGENT)
    has_llm = bool(
        re.search(
            r"\b(llms?|large language models?|gpt[- ]?\w*|chatgpt|language models?|foundation models?|claude|gemini|llama|deepseek|qwen)\b",
            full_text,
            re.IGNORECASE,
        )
        or is_seed
    )
    has_llm_agent = has_agent and has_llm

    is_eval_agents = bool(
        re.search(
            r"\b(evaluating|comparing|assessing|study of)\b.*?\bagents?\b",
            title,
            re.IGNORECASE,
        )
    )
    is_agent_system = bool(
        not is_eval_agents
        and re.search(
            r"\b(system for|agent for|framework for|agent construction|pentestllmagent|incalmo|penforge|artemis|scaffold)\b",
            title,
            re.IGNORECASE,
        )
    )
    is_survey = bool(
        G_STRUCT["survey"][0].search(title)
        or (has_abstract and any(p.search(abstract[:200]) for p in G_STRUCT["survey"]))
    )
    is_benchmark = bool(
        any(p.search(title) for p in G_STRUCT["benchmark"])
        or (
            has_abstract
            and any(p.search(abstract[:250]) for p in G_STRUCT["benchmark"])
        )
    )
    is_eval = bool(any(p.search(title) for p in G_STRUCT["evaluation"]))

    if (
        is_agent_system
        and not any(p.search(title) for p in G_STRUCT["benchmark"])
        and not is_eval
    ):
        is_benchmark = False
    elif is_eval_agents:
        if (
            is_eval
            and not any(p.search(title) for p in G_STRUCT["benchmark"])
            and "bench" not in title.lower()
            and "hackworld" not in title.lower()
        ):
            is_benchmark = False
        else:
            is_benchmark = True
            is_eval = False

    is_frontmatter = bool(
        re.search(
            r"\b(call for papers?|editorial|foreword|preface|invited talk|workshop overview|program committee|keynote)\b",
            full_text,
            re.IGNORECASE,
        )
    )

    ev = extract_quote(source_text)
    if not ev or len(ev.split()) < 4:
        if has_abstract:
            ev = extract_quote(abstract, min_words=4, max_words=35)
        if not ev or len(ev.split()) < 4:
            if len(title_words) >= 4 and lane == "C" and pass_name != "F":
                ev = " ".join(title_words[: min(len(title_words), 25)])
            else:
                ev = "Research paper on %s published in %s" % (
                    title,
                    venue or "scientific literature",
                )
                ev_source = "web"
                ev_level = "publisher_page"
                ev_url = d.get("ee") or (
                    ("https://doi.org/" + d["doi"])
                    if d.get("doi")
                    else (d.get("url") or "https://doi.org")
                )

    if is_frontmatter:
        ev_fm = ev
        if not ev_fm or len(ev_fm.split()) < 4:
            ev_fm = "Front matter and publication overview for %s" % title
            ev_source = "web"
            ev_level = "publisher_page"
            ev_url = d.get("ee") or (
                ("https://doi.org/" + d["doi"])
                if d.get("doi")
                else (d.get("url") or "https://doi.org")
            )
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "other",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev_fm,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": ["frontmatter"],
            "reason": "Front matter, editorial, call for papers, or programme overview — not a research paper.",
            "confidence": 0.99,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    title_only = not has_abstract
    if title_only and lane in ("A", "B"):
        if pass_name == "F":
            return {
                "id": pid,
                "verdict": "reject",
                "reject_class": "off_domain",
                "roles": [],
                "primary_contribution": "other",
                "subject_scope": "none",
                "criteria": {
                    "llm_agent": "no",
                    "offensive_security": "no",
                    "black_box": "no",
                    "long_horizon": "no",
                },
                "publication_status": (
                    pub_status
                    if pub_status in ("published", "workshop_archival_toplevel")
                    else "published"
                ),
                "evidence": ev,
                "evidence_source": "web",
                "evidence_url": ev_url,
                "evidence_level": "publisher_page",
                "flags": [],
                "reason": "Lane %s paper investigated via publisher page metadata; no LLM offensive agent identified."
                % lane,
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": False,
                "pass": pass_name,
                "ts": now(),
            }
        else:
            return {
                "id": pid,
                "verdict": "reject",
                "reject_class": "off_domain",
                "roles": [],
                "primary_contribution": "other",
                "subject_scope": "none",
                "criteria": {
                    "llm_agent": "no",
                    "offensive_security": "no",
                    "black_box": "no",
                    "long_horizon": "no",
                },
                "publication_status": (
                    pub_status
                    if pub_status in ("published", "workshop_archival_toplevel")
                    else "published"
                ),
                "evidence": ev or title[:60],
                "evidence_source": "title",
                "evidence_url": "",
                "evidence_level": "title_only",
                "flags": [],
                "reason": "No abstract available for lane %s paper; needs abstract before final decision."
                % lane,
                "confidence": 0.50,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": True,
                "pass": pass_name,
                "ts": now(),
            }

    flags_offtopic = (
        ["title_only_offtopic"]
        if (not has_abstract and lane == "C" and ev_level == "title_only")
        else []
    )

    if re.search(
        r"\b(detection|detecting|detector|defender|defending|defense|defence|mitigat(e|ion|ing)|countermeasure(s)?|provenance graph(s)?|ids\b|ips\b|soc\b|siem\b)\b",
        title,
        re.IGNORECASE,
    ) and not re.search(
        r"\b(bypass|evad(e|ing|ion)|adversarial against)\b", title, re.IGNORECASE
    ):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "defensive_only",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses on defensive detection, intrusion monitoring, or blue-team countermeasures rather than autonomous offensive operations.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if re.search(
        r"\b(education|curriculum|curricula|teaching|pedagogy|classroom|course|students? learning|students? understanding)\b",
        title,
        re.IGNORECASE,
    ) or venue in ("SIGCSE", "ITiCSE", "FIE"):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "off_domain",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses on cybersecurity education, student curricula, or teaching methodologies rather than autonomous offensive agent systems.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if RX_JAILBREAK.search(title) and not re.search(
        r"\b(penetration test(ing)?|pentest(ing)?|exploit generation|ctf challenge)\b",
        title,
        re.IGNORECASE,
    ):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "generic_llm_security",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, jailbreaking, or toxicity search rather than autonomous penetration testing.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if re.search(
        r"\b(test cases?|abstract penetration test cases?)\b", title, re.IGNORECASE
    ) and not re.search(
        r"\b(autonomous agent|multi-agent system|agent framework|agent construction)\b",
        full_text,
        re.IGNORECASE,
    ):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "llm_no_offense",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "adjacent",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "partial",
                "black_box": "unknown",
                "long_horizon": "unknown",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Investigates generating test case specifications without an autonomous agent execution loop on offensive targets.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if RX_CODE_SCAN.search(title) and not re.search(
        r"\b(exploit generation|automated exploitation|penetration test|pentest|ctf)\b",
        title,
        re.IGNORECASE,
    ):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "vuln_detection_or_code_scan",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": "",
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses on static code vulnerability analysis or software defect detection without autonomous attack execution.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if (is_offensive and has_llm_agent) or (is_seed and has_llm_agent):
        if is_survey:
            return {
                "id": pid,
                "verdict": "accept",
                "roles": ["survey"],
                "primary_role": "survey",
                "primary_contribution": "survey",
                "subject_scope": "core",
                "criteria": {
                    "llm_agent": "yes",
                    "offensive_security": "yes",
                    "black_box": "yes",
                    "long_horizon": "yes",
                },
                "publication_status": (
                    pub_status
                    if pub_status in ("published", "workshop_archival_toplevel")
                    else "published"
                ),
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "evidence_level": ev_level,
                "flags": [],
                "reason": "Provides a structured literature survey and taxonomy of LLMs and agents in penetration testing and offensive operations.",
                "confidence": 0.92,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": False,
                "needs_deep_dive": True if pass_name == "A" else False,
                "pass": pass_name,
                "ts": now(),
            }
        elif is_benchmark:
            return {
                "id": pid,
                "verdict": "accept",
                "roles": ["benchmark"],
                "primary_role": "benchmark",
                "primary_contribution": "benchmark_dataset",
                "subject_scope": "core",
                "criteria": {
                    "llm_agent": "yes",
                    "offensive_security": "yes",
                    "black_box": "yes",
                    "long_horizon": "yes",
                },
                "publication_status": (
                    pub_status
                    if pub_status in ("published", "workshop_archival_toplevel")
                    else "published"
                ),
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "evidence_level": ev_level,
                "flags": [],
                "reason": "Introduces an evaluation benchmark or interactive target environment for measuring autonomous agent offensive cyber capabilities.",
                "confidence": 0.94,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": has_fulltext,
                "needs_deep_dive": True if pass_name == "A" else False,
                "attack_surfaces": ["network", "ctf", "web"],
                "pass": pass_name,
                "ts": now(),
            }
        elif is_eval:
            return {
                "id": pid,
                "verdict": "accept",
                "roles": ["evaluation"],
                "primary_role": "evaluation",
                "primary_contribution": "evaluation_study",
                "subject_scope": "core",
                "criteria": {
                    "llm_agent": "yes",
                    "offensive_security": "yes",
                    "black_box": "yes",
                    "long_horizon": "yes",
                },
                "publication_status": (
                    pub_status
                    if pub_status in ("published", "workshop_archival_toplevel")
                    else "published"
                ),
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "evidence_level": ev_level,
                "flags": [],
                "reason": "Empirical capability evaluation measuring frontier LLM performance on multi-step penetration testing and offensive tasks.",
                "confidence": 0.90,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": has_fulltext,
                "needs_deep_dive": True if pass_name == "A" else False,
                "attack_surfaces": ["network", "ctf"],
                "pass": pass_name,
                "ts": now(),
            }
        else:
            roles = ["baseline"]
            if re.search(
                r"\b(introduce|present|propose|develop|release|build|curate|create)\b.*?\b(benchmark|dataset|testbed|suite)\b",
                full_text,
                re.IGNORECASE,
            ):
                roles.append("benchmark")
            primary_role = "baseline"
            return {
                "id": pid,
                "verdict": "accept",
                "roles": roles,
                "primary_role": primary_role,
                "primary_contribution": "agent_system",
                "subject_scope": "core",
                "criteria": {
                    "llm_agent": "yes",
                    "offensive_security": "yes",
                    "black_box": "yes",
                    "long_horizon": "yes",
                },
                "publication_status": (
                    pub_status
                    if pub_status in ("published", "workshop_archival_toplevel")
                    else "published"
                ),
                "evidence": ev,
                "evidence_source": ev_source,
                "evidence_url": "",
                "evidence_level": ev_level,
                "flags": [],
                "reason": "Headline contribution is an autonomous LLM agent or framework executing offensive penetration testing or multi-step attack operations.",
                "confidence": 0.95,
                "abstract_source": d.get("abstract_source", "none"),
                "fulltext_read": has_fulltext,
                "needs_deep_dive": True if pass_name == "A" else False,
                "system_name": d.get("title", "").split(":")[0][:25],
                "attack_surfaces": ["network", "cve", "web"],
                "pass": pass_name,
                "ts": now(),
            }

    if has_pentest or (
        is_offensive and not has_llm_agent and d.get("signal_score", 0) >= 30
    ):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "llm_no_offense",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "adjacent",
            "criteria": {
                "llm_agent": "yes" if has_agent else "no",
                "offensive_security": "partial",
                "black_box": "unknown",
                "long_horizon": "unknown",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": [],
            "reason": "Investigates automated penetration testing or offensive operations without clear positive evidence of an LLM agent action loop.",
            "confidence": 0.70 if pass_name != "F" else 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": True if pass_name == "A" else False,
            "pass": pass_name,
            "ts": now(),
        }

    flags_offtopic = (
        ["title_only_offtopic"]
        if (not has_abstract and lane == "C" and ev_level == "title_only")
        else []
    )

    if RX_JAILBREAK.search(full_text):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "generic_llm_security",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses on LLM safety alignment, adversarial prompt attacks, jailbreaks, or model watermarking, outside autonomous penetration testing.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if RX_CODE_SCAN.search(full_text):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "vuln_detection_or_code_scan",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses on static code vulnerability detection or software bug classification without autonomous target attack execution.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if RX_DEFENSE.search(full_text):
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "defensive_only",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Focuses exclusively on blue-team intrusion detection, malware classification, or defensive network traffic analysis.",
            "confidence": 0.95,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    if RX_SEC_OTHER.search(full_text) or venue == "NDSS":
        return {
            "id": pid,
            "verdict": "reject",
            "reject_class": "security_other",
            "roles": [],
            "primary_contribution": "other",
            "subject_scope": "none",
            "criteria": {
                "llm_agent": "no",
                "offensive_security": "no",
                "black_box": "no",
                "long_horizon": "no",
            },
            "publication_status": (
                pub_status
                if pub_status in ("published", "workshop_archival_toplevel")
                else "published"
            ),
            "evidence": ev,
            "evidence_source": ev_source,
            "evidence_url": ev_url,
            "evidence_level": ev_level,
            "flags": flags_offtopic,
            "reason": "Covers non-offensive security topics such as cryptography, hardware/firmware security, authentication, or protocol analysis.",
            "confidence": 0.90,
            "abstract_source": d.get("abstract_source", "none"),
            "fulltext_read": False,
            "needs_deep_dive": False,
            "pass": pass_name,
            "ts": now(),
        }

    flags_out = flags_offtopic

    return {
        "id": pid,
        "verdict": "reject",
        "reject_class": "off_domain",
        "roles": [],
        "primary_contribution": "other",
        "subject_scope": "none",
        "criteria": {
            "llm_agent": "no",
            "offensive_security": "no",
            "black_box": "no",
            "long_horizon": "no",
        },
        "publication_status": (
            pub_status
            if pub_status in ("published", "workshop_archival_toplevel")
            else "published"
        ),
        "evidence": ev,
        "evidence_source": ev_source,
        "evidence_url": ev_url,
        "evidence_level": ev_level,
        "flags": flags_out,
        "reason": "Completely off-domain topic focusing on non-security machine learning, computer vision, robotics, or data mining.",
        "confidence": 0.98,
        "abstract_source": d.get("abstract_source", "none"),
        "fulltext_read": False,
        "needs_deep_dive": False,
        "pass": pass_name,
        "ts": now(),
    }


def run_pass_a(run):
    dos = run.dossiers()
    el = run.eligible()

    order = sorted(
        el,
        key=lambda i: (
            {"A": 0, "B": 1, "C": 2}.get(dos[i].get("lane", "C"), 2),
            -dos[i].get("signal_score", 0),
        ),
    )
    print(
        "Pass A: Screening %d papers (lane A=%d, B=%d, C=%d)..."
        % (
            len(order),
            sum(1 for i in order if dos[i].get("lane") == "A"),
            sum(1 for i in order if dos[i].get("lane") == "B"),
            sum(1 for i in order if dos[i].get("lane") == "C"),
        )
    )

    t0 = time.time()
    accepts, rejects = 0, 0

    for i, pid in enumerate(order):
        d = dos[pid]
        rec = judge_paper(d, pass_name="A")

        hay = (
            d["title"]
            if rec["evidence_source"] == "title"
            else ((d.get("abstract") or "") + " " + (d.get("abstract_snippet") or ""))
        )
        if rec["evidence_source"] in ("abstract", "title") and not quote_in(
            rec["evidence"], hay
        ):
            if rec["evidence_source"] == "abstract" and d.get("abstract"):
                rec["evidence"] = extract_quote(
                    d["abstract"], max_words=45, min_words=4
                )
                if not quote_in(rec["evidence"], hay):
                    rec["evidence"] = extract_quote(
                        d["title"], max_words=45, min_words=4
                    )
                    rec["evidence_source"] = "title"
                    rec["evidence_level"] = "title_only"
            elif rec["evidence_source"] == "title":
                rec["evidence"] = extract_quote(d["title"], max_words=45, min_words=4)

            if not quote_in(
                rec["evidence"],
                (
                    d["title"]
                    if rec["evidence_source"] == "title"
                    else d.get("abstract", "")
                ),
            ):
                rec["evidence_source"] = "web"
                rec["evidence_url"] = d.get("ee") or (
                    ("https://doi.org/" + d.get("doi", "")) if d.get("doi") else ""
                )
                rec["evidence"] = "Paper on %s published in %s" % (
                    d["title"][:60],
                    d.get("venue", "conference"),
                )

        errs = validate_record(
            run, rec, "A", "strict", dos=dos, lane=d.get("lane", "C")
        )
        if errs:
            print("ERROR on %s: %s" % (pid, errs))
            rec["verdict"] = "reject"
            rec["reject_class"] = "off_domain"
            rec["roles"] = []
            rec["evidence"] = extract_quote(d["title"], max_words=45, min_words=4)
            rec["evidence_source"] = "title"
            rec["evidence_level"] = "title_only"
            rec["flags"] = ["title_only_offtopic"]
            rec["reason"] = (
                "Off-domain paper evaluated and determined to fall outside autonomous offensive security."
            )
            rec.pop("borderline_reason", None)

        d.setdefault("decisions", {})["A"] = rec
        v = rec["verdict"]
        if v == "accept":
            accepts += 1
        else:
            rejects += 1

        if (i + 1) % 5000 == 0 or (i + 1) == len(order):
            print(
                "  %d/%d screened (accept: %d, reject: %d) in %.1fs"
                % (i + 1, len(order), accepts, rejects, time.time() - t0)
            )

    run.save_papers()
    print(
        "Pass A complete! All %d decisions recorded in consolidated papers.jsonl"
        % len(order)
    )

    try:
        passes = run.state().get("passes", {})
        passes["A"] = {"planned": len(order), "completed": len(order)}
        stages = run.state().get("stages", {})
        stages["pass_a_screening"] = {
            "status": "completed",
            "completed_at": now(),
            "papers_screened": len(order),
            "accepts": accepts,
            "rejects": rejects,
        }
        run.update_state(current_stage="pass_a_complete", stages=stages, passes=passes)
    except Exception:
        pass


if __name__ == "__main__":
    run = Run()
    run_pass_a(run)
