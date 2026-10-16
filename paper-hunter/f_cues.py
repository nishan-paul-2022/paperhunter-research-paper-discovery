"""f_cues.py: pure, corpus-independent content cues and triage tier (spec section 6).

Cues are booleans over the normalised title and abstract only. The same paper gets the
same cues and tier in any file: no percentiles, no corpus statistics, no global state.
Rules route and advise; they never judge.

Tokens: ai agent sec off off_strong def jb scan surv bench eval front seed
"""

import os
import re
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from multiprocessing import get_context

import b_db

# --------------------------------------------------------------------------- vocabularies

AI_RE = re.compile(
    r"\b(?:llms?|large language models?|language models?|lm agents?|\w*gpt\w*|claude|gemini|llama|"
    r"deepseek|qwen|foundation models?|generative ai|agentic|ai[- ]agents?|ai-driven|ai-powered|"
    r"autonomous agents?|computer[- ]use agents?|copilots?|mcp|model context protocol)\b"
    r"|\bllm|\b(?:pentest|hack|vuln|exploit|attack|cyber|sec|ctf|auto)\w*llm"
)
AGENT_RE = re.compile(
    r"\b(?:agents?|agentic|autonomously|react|tool[- ](?:use|using|calling|augmented)|planner|"
    r"reflection|iterative|action loop)\b"
)
SEC_RE = re.compile(
    r"vulnerab|attack|threat|malware|security|cyber|adversar|injection|payload|intrusion|"
    r"\bcves?\b|malicious"
)
EXPLOIT_RE = re.compile(r"\bexploit")
SHELL_RE = re.compile(r"\bshell\b")
OFF_RE = re.compile(r"\bhack|red[- ]?team|offensive|penetration|intrusion|pen-?test")
OFF_STRONG_RE = re.compile(
    r"penetration[- ]test|pen-?test|red[- ]?team|offensive security|exploit generation|"
    r"privilege escalation|lateral movement|post-?exploitation|\bctfs?\b|capture[- ]the[- ]flag|"
    r"cyber range|attack (?:chain|path|graph)|kill chain|(?:reverse|root) shell|metasploit|\bnmap\b|"
    r"sql injection|\bxss\b|\brce\b|cve-\d|zero[- ]day|\bn-day|one[- ]day|"
    r"compromis\w* (?:\w+ ){0,2}(?:hosts?|servers?|machines?|networks?)|"
    r"\bhack(?:ing|ers?|s)?\b|"
    r"exploit(?:s|ed|ing|ation)? (?:of )?(?:web|vulnerab|zero|n-day|one-day)"
)
# Defensive vocabulary. The bare word "defense" is a cue but never a reason to reject (see g_prior).
DEF_CORE = (
    r"intrusion detection|malware detection|anomaly detection|\bsoc\b|\bsiem\b|blue[- ]team|"
    r"incident response|\bharden|\bmitigat|\bpatch"
)
DEF_RE = re.compile(DEF_CORE + r"|\bdefen[cs](?:e|es|ive)\b")
DEF_CORE_RE = re.compile(DEF_CORE)
JB_RE = re.compile(
    r"jailbreak|prompt[- ]injection|safety alignment|\brefusals?\b|toxic|backdoor|poisoning"
)
SCAN_RE = re.compile(
    r"vulnerabilit(?:y|ies) (?:detection|identification)|code review|static analysis|"
    r"defect detection|bug detection"
)
SURV_RE = re.compile(
    r"\bsurvey|systematic(?:ally)? (?:literature )?review|\bsok\b|systemati[sz]ation of knowledge|"
    r"literature review|taxonomy|roadmap|mapping study|a review of|comprehensive review"
)
BENCH_RE = re.compile(r"benchmark|\bdatasets?\b|test-?beds?|leaderboard|evaluation harness|\bgym\b")
EVAL_RE = re.compile(
    r"empirical (?:study|evaluation|analysis)|how (?:well|good|capable)|\bcapabilit(?:y|ies)\b|"
    r"compar(?:ing|ison|ative)|case study|assess(?:ing|ment)|measuring|"
    r"evaluat(?:ing|ion) (?:of )?(?:the )?(?:capabilit|performance|effectiveness)|\b(?:can|do) llms?\b"
)
FRONT_RE = re.compile(
    r"call for papers|editorial|foreword|preface|keynote|program committee|workshop overview"
)

# Known in-scope titles: a recall hint only (never used in role logic).
SEED_TITLES = (
    "PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing",
    "AutoAttacker: A Large Language Model Guided System to Implement Automatic Cyber-attacks",
    "VulnBot: Autonomous Penetration Testing for A Multi-Agent Collaborative Framework",
    "HackSynth: LLM Agent and Evaluation Framework for Autonomous Penetration Testing",
    "Cybench: A Framework for Evaluating Cybersecurity Capabilities and Risks of Language Models",
    "AutoPenBench: Benchmarking Generative Agents for Penetration Testing",
    "HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities",
    "LLM Agents can Autonomously Hack Websites",
    "Teams of LLM Agents can Exploit Zero-Day Vulnerabilities",
    "LLM Agents can Autonomously Exploit One-day Vulnerabilities",
    "Incalmo: An Autonomous LLM-Assisted System for Red Teaming Multi-Host Networks",
    "Getting pwn'd by AI: Penetration Testing with Large Language Models",
    "Comparing AI Agents to Cybersecurity Professionals in Real-World Penetration Testing",
    "Towards Effective Offensive Security LLM Agents",
)
SEED_THRESHOLD = 0.6
_SEEDS = tuple(b_db.norm_title(t) for t in SEED_TITLES)

_DASHES = {c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
_SPACES = re.compile(r"\s+")
_HEAD_CHARS = 300


def normalize(text):
    """Lower-case, NFKC, unify dashes and collapse whitespace."""
    if not text:
        return ""
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", text).lower().translate(_DASHES)).strip()


def _is_seed(title):
    s = b_db.norm_title(title)
    for known in _SEEDS:
        m = SequenceMatcher(None, s, known)
        if (m.real_quick_ratio() >= SEED_THRESHOLD and m.quick_ratio() >= SEED_THRESHOLD
                and m.ratio() >= SEED_THRESHOLD):
            return True
    return False


# --------------------------------------------------------------------------- cues and tier

def cues(title, abstract=""):
    """Cue tokens (frozenset of str) for one paper, from its title and abstract only."""
    ti = normalize(title)
    ab = normalize(abstract)
    t = ti + " . " + ab if ab else ti
    out = set()

    ai = AI_RE.search(t) is not None
    if ai:
        out.add("ai")
    if AGENT_RE.search(t):
        out.add("agent")
    off_strong = OFF_STRONG_RE.search(t) is not None
    exploit = EXPLOIT_RE.search(t) is not None
    sec = off_strong or SEC_RE.search(t) is not None or (exploit and SHELL_RE.search(t) is not None)
    if sec:
        out.add("sec")
    if off_strong:
        out.update(("off", "off_strong"))
    elif OFF_RE.search(t) or (exploit and sec):
        out.add("off")
    if DEF_RE.search(t):
        out.add("def")
    if JB_RE.search(t):
        out.add("jb")
    if SCAN_RE.search(t):
        out.add("scan")

    head = ti + " . " + ab[:_HEAD_CHARS] if ab else ti
    if SURV_RE.search(head):
        out.add("surv")
    if BENCH_RE.search(head):
        out.add("bench")
    if EVAL_RE.search(head):
        out.add("eval")
    if FRONT_RE.search(ti):
        out.add("front")
    if ai and sec and _is_seed(title or ""):
        out.add("seed")
    return frozenset(out)


def tier(cue_set, has_abstract=True):
    """Triage tier 0..3 from cues alone (spec 6.2). `has_abstract` is accepted for API symmetry."""
    ai, sec = "ai" in cue_set, "sec" in cue_set
    if ai and sec:
        return 3 if ("off_strong" in cue_set and "agent" in cue_set) or "seed" in cue_set else 2
    if "front" in cue_set:
        return 0
    return 1 if (ai or sec) else 0


# --------------------------------------------------------------------------- triage run

_PENDING = "t.paper_id IS NULL OR (t.ha = 0 AND a.paper_id IS NOT NULL) OR t.rv != ?"
_POOL_MIN = 2000
_TASK = 500


def _compute(papers, rv):
    """papers: list of (id, title, abstract|None) -> list of (id, tier, cues, prior, ha, rv)."""
    from g_prior import prior

    out = []
    for pid, title, abstract in papers:
        c = cues(title, abstract or "")
        t = tier(c, bool(abstract))
        out.append((pid, t, " ".join(sorted(c)), prior(title, abstract or "", c, t),
                    1 if abstract else 0, rv))
    return out


def run_triage(conn, workers=0, chunk=5000):
    """Incremental triage of canonical papers; the caller's connection is the only writer.

    Processes papers whose triage is missing, whose abstract arrived since (ha=0), or whose
    rules version differs. Safe to interrupt and re-run. Returns
    {"processed": n, "tiers": {0: .., 1: .., 2: .., 3: ..}} for the whole canonical corpus.
    """
    rv = b_db.RULES_VERSION
    workers = workers or os.cpu_count() or 1
    pending = conn.execute(
        "SELECT COUNT(*) FROM paper p LEFT JOIN abstract a ON a.paper_id=p.id "
        "LEFT JOIN triage t ON t.paper_id=p.id WHERE p.dup_of IS NULL AND (%s)" % _PENDING,
        (rv,)).fetchone()[0]
    use_pool = workers > 1 and pending >= _POOL_MIN
    processed = 0

    def save(rows):
        nonlocal processed
        b_db.upsert_triage(conn, rows)
        processed += len(rows)

    chunks = b_db.iter_papers(conn, chunk, where=_PENDING, params=(rv,))
    if use_pool:
        with get_context("fork").Pool(workers) as pool:
            inflight = deque()
            for batch in chunks:
                items = [(p["id"], p["title"], p["abstract"]) for p in batch]
                for i in range(0, len(items), _TASK):
                    inflight.append(pool.apply_async(_compute, (items[i:i + _TASK], rv)))
                while len(inflight) > 2 * workers:
                    save(inflight.popleft().get())
            while inflight:
                save(inflight.popleft().get())
    else:
        for batch in chunks:
            save(_compute([(p["id"], p["title"], p["abstract"]) for p in batch], rv))

    hist = b_db.tier_histogram(conn)
    return {"processed": processed, "tiers": {t: hist.get(t, 0) for t in range(4)}}
