"""g_prior.py: advisory prior (spec section 6.3).

A prior is a hint for the reviewer, never a verdict. It is computed after the tier, from the
tier, the cues and the normalised title, and never feeds back into the tier. Unmatched papers
get None: "no opinion".
"""

from f_cues import BENCH_RE, DEF_CORE_RE, EVAL_RE, JB_RE, OFF_STRONG_RE, SCAN_RE, normalize


def prior(title, abstract, cue_set, tier):
    """'accept:<role>' | 'reject:<class>' | None.

    Only tier >= 2 papers get a prior. The abstract is accepted for API symmetry: its content
    is already summarised by `cue_set`, and title-level evidence decides the competing topics.
    The bare word "defense" never counts as defensive evidence.
    """
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
