"""Decision schema + consistency rules. The validator is the quality gate: bad decisions are rejected at submit time."""
import re
TIERS = {"core", "related", "reject", "needs_manual"}
ROLES = {"baseline", "baseline_candidate", "survey", "benchmark", "evaluation", "background"}
PRIMARY = {"agent_system", "benchmark_dataset", "evaluation_study", "survey", "other"}
CRIT = {"yes", "partial", "no", "unknown"}
CRIT_KEYS = ("llm_agent", "offensive_security", "black_box", "long_horizon")
PUB = {"published", "workshop_archival_toplevel", "workshop_other", "preprint_only", "thesis_or_report", "unknown"}
EVL = ["title_only", "abstract", "publisher_page", "full_text"]
RELATED_TYPES = {"rl_or_nonllm_pentest", "attack_graph_planning", "exploit_generation_oneshot", "vuln_detection_llm",
                 "defensive_agent", "attacks_on_agents", "llm_security_survey_broad", "general_agent_framework",
                 "security_benchmark_static", "cyber_range_nonllm", "offense_adjacent_other", "other"}
FIELDS_REQ = ["id", "tier", "roles", "primary_contribution", "publication_status", "criteria", "evidence_level",
              "reason", "confidence"]

def nwords(s): return len(re.findall(r"\S+", s or ""))
def _n(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def validate(d, lane=None, abstract=""):
    E, W = [], []
    for k in FIELDS_REQ:
        if k not in d: E.append(f"missing field '{k}'")
    if E: return E, W
    tier, roles = d["tier"], d["roles"]
    if tier not in TIERS: E.append(f"tier must be one of {sorted(TIERS)}")
    if not isinstance(roles, list) or any(r not in ROLES for r in roles): E.append(f"roles must be list from {sorted(ROLES)}")
    if d["primary_contribution"] not in PRIMARY: E.append(f"primary_contribution must be one of {sorted(PRIMARY)}")
    if d["publication_status"] not in PUB: E.append(f"publication_status must be one of {sorted(PUB)}")
    if d["evidence_level"] not in EVL: E.append(f"evidence_level must be one of {EVL}")
    cr = d["criteria"]
    if not isinstance(cr, dict) or set(cr) != set(CRIT_KEYS) or any(v not in CRIT for v in cr.values()):
        E.append(f"criteria needs exactly keys {CRIT_KEYS} with values in {sorted(CRIT)}")
    try:
        if not (0 <= float(d["confidence"]) <= 1): E.append("confidence must be 0..1")
    except (TypeError, ValueError): E.append("confidence must be a number 0..1")
    if not str(d["reason"]).strip() or nwords(d["reason"]) > 45: E.append("reason: 1 sentence, <=45 words, non-empty")
    if E: return E, W
    flags = d.get("flags") or []
    if not isinstance(flags, list): E.append("flags must be a list of strings"); return E, W
    ev, lvl = d.get("evidence", ""), d["evidence_level"]
    if tier in ("reject", "needs_manual") and roles: E.append("roles must be [] for reject/needs_manual")
    if tier in ("core", "related") and not roles: E.append("core/related need >=1 role")
    if tier == "core" and not (cr["llm_agent"] == "yes" and cr["offensive_security"] == "yes"):
        E.append("core requires llm_agent=yes AND offensive_security=yes (judged on the systems the paper presents/surveys/benchmarks/evaluates); else use related")
    if tier == "related" and d.get("related_type") not in RELATED_TYPES: E.append(f"related requires related_type in {sorted(RELATED_TYPES)}")
    if tier == "needs_manual" and not flags: E.append("needs_manual requires flags explaining what evidence is missing")
    if "baseline" in roles:
        if tier != "core": E.append("baseline only allowed with tier=core")
        if d["primary_contribution"] != "agent_system": E.append("baseline requires primary_contribution=agent_system (headline = a system that attacks)")
        if any(cr[k] != "yes" for k in CRIT_KEYS): E.append("baseline requires all four criteria=yes (3,4 under their SOFT definitions; 'not contradicted' is not 'yes'). If 3/4 are partial use role baseline_candidate")
        if d["publication_status"] not in ("published", "workshop_archival_toplevel"): E.append("baseline requires publication_status published or workshop_archival_toplevel")
        if lvl not in ("publisher_page", "full_text"): E.append("baseline requires evidence_level publisher_page or full_text (verify on the publisher page)")
    if "baseline_candidate" in roles and d["primary_contribution"] != "agent_system": E.append("baseline_candidate requires primary_contribution=agent_system")
    if tier in ("core", "related"):
        if lvl == "title_only": E.append("core/related cannot rest on title_only evidence")
        if not ev.strip(): E.append("evidence (verbatim quote <=25 words) required for core/related")
        elif nwords(ev) > 30: E.append("evidence too long (<=25 words)")
        if not d.get("sources_checked"): E.append("sources_checked (list of URLs/sources actually opened) required for core/related")
    if tier == "reject":
        if lvl == "title_only":
            if lane in ("A", "B"): E.append("lane A/B papers need an abstract or better; if unobtainable after >=3 sources use needs_manual")
            elif "title_only_offtopic" not in flags: E.append("title_only reject needs flag 'title_only_offtopic' and an unambiguously non-security/non-LLM title")
        if lane == "A" and lvl == "abstract" and not d.get("sources_checked"): W.append("lane A reject on abstract only: open publisher page")
    if lane == "A" and tier in ("core", "related") and lvl == "abstract": W.append("lane A kept paper: prefer publisher_page/full_text")
    if ev and abstract and d.get("evidence_source", "abstract") == "abstract" and tier != "reject":
        if _n(ev) not in _n(abstract) and _n(ev.replace("...", "")) not in _n(abstract): W.append("evidence_not_verbatim_in_abstract")
    if float(d["confidence"]) < 0.6 and tier == "reject": W.append("low-confidence reject -> will be audited")
    return E, W
