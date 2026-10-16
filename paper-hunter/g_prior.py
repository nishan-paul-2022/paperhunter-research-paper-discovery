"""Advisory prior generation from cues and title.

Prior is a hint for reviewers, never a verdict. Only tier >= 2 papers get priors.
Bare word "defense" never counts as evidence for reject:defensive_only.
"""

from f_cues import BENCH_RE, DEF_CORE_RE, EVAL_RE, JB_RE, OFF_STRONG_RE, SCAN_RE, normalize


def prior(title, abstract, cue_set, tier):
    if tier < 2:
        return None
    t = normalize(title)
    if OFF_STRONG_RE.search(t):
        topic = None
    elif JB_RE.search(t):
        topic = "reject:generic_llm_security"
    elif SCAN_RE.search(t):
        topic = "reject:vuln_detection_or_code_scan"
    elif DEF_CORE_RE.search(t):
        topic = "reject:defensive_only"
    else:
        topic = None

    if tier < 3 or topic:
        return topic
    if "surv" in cue_set:
        return "accept:survey"
    if BENCH_RE.search(t):
        return "accept:benchmark"
    if EVAL_RE.search(t):
        return "accept:evaluation"
    return "accept:baseline"
