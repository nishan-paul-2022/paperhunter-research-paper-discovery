import difflib
import re
import unicodedata

ROLES = ["baseline", "baseline_candidate", "benchmark", "evaluation", "survey"]
ROLE_TO_CONTRIB = {
    "baseline": "agent_system",
    "baseline_candidate": "agent_system",
    "benchmark": "benchmark_dataset",
    "evaluation": "evaluation_study",
    "survey": "survey",
}
CONTRIBS = ["agent_system", "benchmark_dataset", "evaluation_study", "survey", "other"]
VERDICTS = ["accept", "reject"]
YNU = ["yes", "no", "unclear"]
YNPU = ["yes", "partial", "no", "unknown"]
SCOPES = ["core", "broad", "adjacent", "none"]
PUB_STATUS = [
    "published",
    "workshop_archival_toplevel",
    "workshop_other",
    "preprint_only",
    "thesis_or_report",
    "unknown",
]
PUB_OK_STRICT = {"published", "workshop_archival_toplevel"}
REJECT_CLASSES = [
    "off_domain",
    "security_other",
    "defensive_only",
    "vuln_detection_or_code_scan",
    "generic_llm_security",
    "static_qa_benchmark",
    "llm_no_offense",
    "out_of_range",
    "duplicate",
    "retracted",
    "other",
]
ABSTRACT_SOURCES = [
    "crossref",
    "openalex",
    "semanticscholar",
    "landing_page",
    "arxiv",
    "manual_web",
    "fulltext",
    "none",
]
EVIDENCE_SOURCES = ["abstract", "title", "fulltext", "web"]
EVIDENCE_LEVELS = ["title_only", "abstract", "publisher_page", "full_text"]
EVIDENCE_LEVELS_DEEP = {"publisher_page", "full_text"}


RX_JAILBREAK = re.compile(
    r"\b(jailbreak|jailbreaking|prompt injection|prompt leak|safety alignment|"
    r"safety guardrail|toxic|toxicity|hallucination|adversarial prompt|unlearning|"
    r"watermark|copyright|refusal|harmbench|advbench)\b",
    re.IGNORECASE,
)
RX_CODE_SCAN = re.compile(
    r"\b(vulnerability detection|code vulnerability|vulnerable code|code review|"
    r"defect detection|smart contract vulnerability|bug detection|static analysis|"
    r"ast|cfg|slice|slicing|fuzzing|fuzzer)\b",
    re.IGNORECASE,
)
RX_DEFENSE = re.compile(
    r"\b(intrusion detection|malware detection|phishing detection|traffic detection|"
    r"anomaly detection|botnet detection|ransomware detection|soc\b|defender|"
    r"blue team|firewall|ids\b|ips\b)\b",
    re.IGNORECASE,
)
RX_SEC_OTHER = re.compile(
    r"\b(cryptography|encryption|zero-knowledge proof|post-quantum|steganography|"
    r"watermarking|authentication|biometric|hardware security|side-channel|"
    r"pufl|differential privacy|rowhammer|kubernetes|firmware|supply chain)\b",
    re.IGNORECASE,
)
RX_PENTEST = re.compile(
    r"\b(penetration test(ing)?|pen-test(ing)?|pentest(ing)?|red team(ing)?|adversary emulation|"
    r"attack automation|cyber operation(s)?|offensive cyber|autonomous attack(s)?)\b",
    re.IGNORECASE,
)
RX_EXPLOIT = re.compile(
    r"\b(exploit generation|automated exploitation|cve exploitation|n-day exploit(s)?|"
    r"zero-day exploit(s)?|binary exploitation|privilege escalation|lateral movement|"
    r"post-exploitation|parallel exploitation)\b",
    re.IGNORECASE,
)
RX_CTF = re.compile(
    r"\b(capture[- ]the[- ]flag|ctf challenge|ctf benchmark|hack the box|"
    r"vulnhub|cyber range)\b",
    re.IGNORECASE,
)


def extract_quote(text, max_words=45, min_words=4):
    if not text:
        return ""
    text = text.strip()
    words = text.split()
    if len(words) < min_words:
        return ""
    if len(words) <= max_words:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        sw = s.strip().split()
        if min_words <= len(sw) <= max_words:
            return s.strip()

    excerpt = " ".join(words[:max_words])
    for end_char in [".", "!", "?", ";", ","]:
        if end_char in excerpt:
            idx = excerpt.rfind(end_char)
            if idx > len(" ".join(words[:min_words])):
                return excerpt[: idx + 1].strip()
    return excerpt


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normtext(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    for ch, rep in [
        ("\u2010", "-"),
        ("\u2011", "-"),
        ("\u2012", "-"),
        ("\u2013", "-"),
        ("\u2014", "-"),
    ]:
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


def validate_record(run, r, pass_, policy, dos=None, lane=None):
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
    off_domain = v == "reject" and r.get("reject_class") in (
        "off_domain",
        "security_other",
        "defensive_only",
        "vuln_detection_or_code_scan",
        "generic_llm_security",
        "static_qa_benchmark",
    )
    for k in ("llm_agent", "offensive_security", "black_box", "long_horizon"):
        if crit.get(k) not in YNPU + ["unclear"]:
            if off_domain and k not in crit:
                crit[k] = "no"
                r["criteria"] = crit
            else:
                errs.append("criteria.%s must be one of %s" % (k, YNPU))
    if not isinstance(r.get("confidence"), (int, float)) or not (
        0 <= r["confidence"] <= 1
    ):
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

    ev_level = r.get("evidence_level")
    if ev_level is not None and ev_level not in EVIDENCE_LEVELS:
        errs.append("evidence_level must be one of %s" % EVIDENCE_LEVELS)

    if ev_level == "title_only" or r.get("evidence_source") == "title":
        _lane = lane or d.get("lane", "C")
        flags = r.get("flags") or []
        if "frontmatter" in flags:
            pass
        elif _lane in ("A", "B"):
            if not r.get("needs_deep_dive"):
                errs.append(
                    "lane %s papers cannot be rejected on title alone; "
                    "obtain an abstract via DOI or web before deciding" % _lane
                )
            elif (r.get("confidence") or 0) > 0.60:
                errs.append(
                    "lane %s title_only reject requires confidence <= 0.60 and needs_deep_dive=true"
                    % _lane
                )
        elif v == "reject":
            if "title_only_offtopic" not in flags:
                errs.append(
                    "title_only reject requires flag 'title_only_offtopic' "
                    "(only allowed for lane C papers whose title is unambiguously "
                    "non-security/non-LLM after >=2 source attempts)"
                )

    ev, es = (r.get("evidence") or "").strip(), r.get("evidence_source")
    if es not in EVIDENCE_SOURCES:
        errs.append("evidence_source must be one of %s" % EVIDENCE_SOURCES)
    elif len(ev.split()) < 4 or len(ev.split()) > 45:
        errs.append("evidence must be a verbatim quote of 4-45 words")
    elif es == "web":
        if not r.get("evidence_url"):
            errs.append(
                "evidence_source=web requires evidence_url (URL where the quote was found)"
            )
    elif not quote_in(ev, _hay_for(run, d, es)):
        errs.append(
            "evidence quote NOT found verbatim in the %s text of %s "
            "- copy it exactly (or use a_cli.py fulltext first)" % (es, pid)
        )
    if es == "fulltext" and r.get("fulltext_read") is False:
        errs.append("evidence_source=fulltext but fulltext_read=false")

    if v == "reject":
        if r.get("reject_class") not in REJECT_CLASSES:
            errs.append("reject needs reject_class in %s" % REJECT_CLASSES)
        if roles:
            errs.append("reject must have roles=[]")
    if v == "accept":
        if not roles:
            errs.append("accept needs at least one role")
        pr = r.get("primary_role")
        if pr not in roles:
            errs.append("accept needs primary_role contained in roles")
        elif pc != ROLE_TO_CONTRIB.get(pr):
            errs.append(
                "primary_role=%s requires primary_contribution=%s"
                % (pr, ROLE_TO_CONTRIB.get(pr))
            )
        if crit.get("llm_agent") != "yes" or crit.get("offensive_security") != "yes":
            errs.append(
                "accept requires criteria llm_agent=yes AND offensive_security=yes "
                "(judged on the system(s) the paper presents/surveys/benchmarks/evaluates)"
            )

        if "baseline" in roles:
            if r.get("primary_role") != "baseline":
                errs.append(
                    "baseline may only be the PRIMARY role. "
                    'It may also appear alongside secondary roles (e.g. ["baseline", "benchmark"]) '
                    "only when those are explicit secondary contributions; primary_role must still be 'baseline'."
                )
            if any(
                crit.get(k) != "yes"
                for k in (
                    "llm_agent",
                    "offensive_security",
                    "black_box",
                    "long_horizon",
                )
            ):
                errs.append(
                    "baseline requires ALL FOUR criteria = yes "
                    "(3 and 4 under their soft definitions, on positive evidence). "
                    "If 3/4 are only partial/unknown use role baseline_candidate."
                )
            if r.get("publication_status") not in PUB_OK_STRICT:
                errs.append(
                    "baseline requires publication_status published or workshop_archival_toplevel"
                )

            if ev_level is not None and ev_level not in EVIDENCE_LEVELS_DEEP:
                errs.append(
                    "baseline requires evidence_level publisher_page or full_text "
                    "(open the publisher page to confirm before accepting as baseline)"
                )

        if "baseline_candidate" in roles:
            if r.get("primary_role") not in ("baseline_candidate", "baseline"):
                errs.append(
                    "baseline_candidate as a role requires primary_role=baseline_candidate "
                    "(use this when criteria 3 or 4 are only 'partial' or 'unknown')"
                )
            if pc != "agent_system":
                errs.append(
                    "baseline_candidate requires primary_contribution=agent_system"
                )
            if (
                crit.get("llm_agent") != "yes"
                or crit.get("offensive_security") != "yes"
            ):
                errs.append(
                    "baseline_candidate requires llm_agent=yes AND offensive_security=yes; "
                    "criteria 3/4 may be partial/unknown"
                )
        if r.get("subject_scope") not in ("core", "broad"):
            errs.append("accept requires subject_scope core or broad")
        if d.get("retracted"):
            errs.append("paper looks retracted")
    if pass_ == "F" and r.get("needs_deep_dive"):
        errs.append("pass F must be definitive (needs_deep_dive=false)")
    if r.get("abstract_source") == "none" and d.get("abstract_source") != "none":
        errs.append(
            "abstract_source=none but the dossier has an abstract (%s)"
            % d.get("abstract_source")
        )
    return errs


def _hay_for(run, d, src):
    if src == "title":
        return d.get("title", "")
    if src == "abstract":
        return (d.get("abstract") or "") + " " + (d.get("abstract_snippet") or "")
    if src == "fulltext":
        if isinstance(d.get("fulltext"), str) and d["fulltext"]:
            return d["fulltext"]
        if hasattr(run, "get_fulltext"):
            return run.get_fulltext(d["id"]) or ""
        return ""
    return ""
