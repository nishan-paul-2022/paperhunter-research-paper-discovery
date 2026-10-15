"""
f_signals.py — TF-IDF / term / embedding signals for paper-hunter.

Signals ORDER and PRIORITIZE work only; they NEVER reject a paper.
"""
import math, os, re
from collections import Counter


# ── regex groups ──────────────────────────────────────────────────────────────

def rx(*pats):
    return [re.compile(p, re.I) for p in pats]


G_AGENT   = rx(r"\bllms?\b", r"large language model", r"\bgpt[- ]?\w*", r"chatgpt",
                r"language model", r"\bagent(s|ic)?\b", r"agentic", r"multi-?agent",
                r"\bautonomous", r"\breact\b", r"\bplanner\b|planning agent",
                r"tool[- ](use|using|augmented|calling)", r"copilot",
                r"foundation model", r"\bmcp\b|model context protocol")
G_OFFENSE = rx(r"penetration[- ]test|pen[- ]?test|pentest", r"red[- ]?team",
                r"adversary emulation|attack emulation", r"ethical hack", r"\bexploit",
                r"privilege escalation", r"lateral movement", r"post[- ]?exploitation",
                r"reconnaissance|\brecon\b",
                r"attack (chain|path|graph|plan|tree|automation)", r"kill[- ]chain",
                r"offensive (security|cyber|operation)", r"cyber ?(attack|operation)s?",
                r"\bcve-\d|\bcves?\b", r"capture[- ]the[- ]flag|\bctfs?\b",
                r"metasploit|\bnmap\b|kali linux", r"hack ?the ?box|vulnhub|\bhtb\b",
                r"mitre att&ck|att&ck", r"cyber range", r"command and control|\bc2\b",
                r"zero-day|one-day|n-day",
                r"vulnerability (assessment|exploitation|scanning)|\bvapt\b",
                r"sql injection|\bxss\b|\brce\b|remote code execution",
                r"hacking|hacker", r"jailbreak(ing)? .* (shell|root)")
G_STRUCT  = {
    "survey":     rx(r"\bsurvey\b", r"systematic (literature )?review",
                     r"\bsok\b|systemati[sz]ation of knowledge", r"taxonomy",
                     r"literature review", r"roadmap|state of the art",
                     r"mapping study", r"overview of"),
    "benchmark":  rx(r"benchmark", r"\bdatasets?\b", r"test ?bed", r"cyber range",
                     r"leaderboard", r"evaluation framework|evaluation harness|environment for"),
    "evaluation": rx(r"empirical (study|evaluation|analysis)",
                     r"evaluat(e|ing|ion) (of )?(the )?(capabilit|performance)",
                     r"how (well|good|capable)", r"capabilit(y|ies)",
                     r"compar(ative|ing|ison) (study|analysis|evaluation)?", r"case study",
                     r"assess(ing|ment)? .* (llm|gpt|model)",
                     r"risk (assessment|evaluation)"),
}
G_SETTING = rx(r"black[- ]?box", r"gr[ae]y[- ]?box", r"zero[- ]knowledge",
               r"unknown (target|network|system)", r"no prior (knowledge|information)",
               r"partial(ly)? (known|knowledge)")
G_NEG     = rx(r"intrusion detection", r"malware detection", r"anomaly detection",
               r"phishing detection", r"blue[- ]team", r"\bdefen[cs]e\b",
               r"spam", r"fraud detection", r"forensic", r"honeypot")
G_RL      = rx(r"reinforcement learning", r"\brl\b",
               r"q-learning|deep q|\bdqn\b|\bppo\b",
               r"monte[- ]carlo tree|\bmcts\b|\bucb\b")
SEEDS     = rx(r"pentestgpt", r"autoattacker", r"hacksynth", r"vulnbot", r"incalmo",
               r"\bmhbench\b", r"\bcybench\b", r"cve-bench", r"bountybench",
               r"autopenbench", r"nyu ctf", r"intercode-ctf", r"enigma", r"d-cipher",
               r"pentesteval", r"\bhptsa\b", r"\bxbow\b", r"\bmapta\b", r"hackworld",
               r"prediql", r"\bcai\b.*cyber", r"pentestagent", r"penheal",
               r"pentest-?r1|pentestr1", r"\bpenligent|\bshellgpt|\bpwnagent")

PROFILES = {
    "domain":
        "LLM-based autonomous agent for penetration testing, red teaming and offensive "
        "security. A large language model agent plans and executes reconnaissance, scanning, "
        "exploitation, privilege escalation, lateral movement and post-exploitation against "
        "black-box or gray-box targets using tools, shell, nmap, metasploit. Multi-step attack "
        "chain, attack path, attack graph, planning, memory, context management, long-horizon. "
        "Capture the flag CTF challenge solving agent, CVE exploitation agent, web application "
        "vulnerability exploitation, Active Directory enterprise network, cyber range, "
        "vulnerable machines Hack The Box.",
    "baseline":
        "We propose a novel LLM-powered agent framework that autonomously performs penetration "
        "testing. Planner, executor, memory, reflection and multi-agent architecture. Evaluated "
        "on CTF challenges, Hack The Box, VulnHub machines and CVE exploitation, success rate "
        "outperforms baselines such as PentestGPT and AutoAttacker.",
    "survey":
        "A survey and systematic literature review, SoK, taxonomy of large language models and "
        "AI agents for penetration testing, offensive security and cyber attacks. Challenges, "
        "open problems, future directions, roadmap.",
    "benchmark":
        "We introduce a benchmark, dataset and testbed for evaluating LLM agents on penetration "
        "testing, capture the flag, cyber range tasks. Vulnerable machines and environments with "
        "automatic grading, flags, oracle.",
    "evaluation":
        "An empirical study evaluating the capabilities of large language models and agents on "
        "offensive security and penetration testing tasks. How well can GPT-4 hack. Comparison "
        "of models, success rate, cost, failure analysis, risk assessment of AI offensive "
        "cyber capabilities.",
}

STOP = set(
    "a an the of for and or in on to with by from is are be this that these those "
    "we our it its as at using use based via into over under between across than "
    "their can which also such not has have been more most new novel paper "
    "study approach method results show shown propose proposed present presented".split())

SWEEP_QUERIES = [
    "LLM agent autonomous penetration testing",
    "survey large language models penetration testing offensive security",
    "systematic literature review AI agents cyber attacks",
    "benchmark LLM agents capture the flag",
    "benchmark penetration testing agents vulnerable environments",
    "evaluating GPT capability exploit vulnerabilities",
    "empirical study LLM hacking capabilities",
    "multi-agent red team framework LLM",
    "attack graph planning LLM agent",
    "privilege escalation LLM agent",
    "Active Directory lateral movement agent",
    "web application exploitation LLM agent",
    "CVE exploit generation agent reproduce vulnerability",
    "CTF solving agent LLM",
    "reinforcement learning penetration testing agent",
    "MCP tool server penetration testing",
    "fine-tuned LLM for penetration testing",
    "cyber range LLM agent attack",
    "adversary emulation LLM MITRE ATT&CK",
    "AI risk evaluation offensive cyber capabilities",
    "retrieval augmented generation pentest knowledge agent",
    "smart contract exploit agent LLM",
    "IoT ICS cloud penetration testing automation LLM",
    "taxonomy of AI-powered cyberattacks",
    "vulnerability scanner LLM automation security assessment",
    "autonomous cyber operations agent network",
    "kill chain red team agent capture the flag",
    "LLM generated exploits security testing framework",
]


# ── tokeniser / TF-IDF ───────────────────────────────────────────────────────

def toks(text):
    words = [w.strip(".-+")
             for w in re.findall(r"[a-z0-9][a-z0-9\-\+\.]*", (text or "").lower())]
    uni   = [w for w in words if w and w not in STOP and len(w) > 1]
    return uni + [a + "_" + b for a, b in zip(uni, uni[1:])]


def vec(tokens, idf):
    tf = Counter(tokens)
    v  = {t: (1 + math.log(c)) * idf.get(t, 0.0) for t, c in tf.items()}
    n  = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / n for t, x in v.items()}


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def group_score(patterns, title, body):
    s, hits = 0, []
    for p in patterns:
        t, b = bool(p.search(title)), bool(p.search(body))
        if t or b:
            hits.append(p.pattern[:24])
        s += (1 if b else 0) + (1 if t else 0)
    return min(1.0, s / 3.0), hits


def try_embeddings(texts):
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(os.environ.get("PH_EMBED_MODEL",
                                                    "sentence-transformers/all-MiniLM-L6-v2"),
                                    device="cpu")
        docs  = model.encode(texts, batch_size=128, normalize_embeddings=True, show_progress_bar=False)
        prof  = model.encode([PROFILES["domain"]], normalize_embeddings=True,
                              show_progress_bar=False)[0]
        return [float(sum(x * y for x, y in zip(d, prof))) for d in docs]
    except Exception:
        return None


def compute_signals(rows, embed="auto"):
    texts     = [(r["title"] + " . " + r["title"] + " . " + (r.get("abstract") or "")
                  + " . " + " ".join(r.get("keywords") or [])
                  + " " + " ".join(r.get("topics") or [])) for r in rows]
    toklists  = [toks(t) for t in texts]
    prof_toks = {k: toks(v) for k, v in PROFILES.items()}
    df        = Counter()
    for tl in toklists + list(prof_toks.values()):
        df.update(set(tl))
    N         = len(toklists) + len(prof_toks)
    idf       = {t: math.log((N + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    pvec      = {k: vec(v, idf) for k, v in prof_toks.items()}
    sims      = []
    for tl in toklists:
        v = vec(tl, idf)
        sims.append({k: cosine(v, pv) for k, pv in pvec.items()})
    order     = sorted(range(len(rows)), key=lambda i: sims[i]["domain"])
    pct       = [0.0] * len(rows)
    for rank, i in enumerate(order):
        pct[i] = rank / max(1, len(rows) - 1)
    emb       = try_embeddings(texts) if embed in ("auto", "on") else None
    emb_pct   = [None] * len(rows)
    if emb:
        eo = sorted(range(len(rows)), key=lambda i: emb[i])
        for rank, i in enumerate(eo):
            emb_pct[i] = rank / max(1, len(rows) - 1)
    out = []
    for i, r in enumerate(rows):
        title, body = r["title"], ((r.get("abstract") or "")
                                   + " " + " ".join(r.get("keywords") or [])
                                   + " " + " ".join(r.get("topics") or []))
        a,  a_h  = group_score(G_AGENT,   title, body)
        o,  o_h  = group_score(G_OFFENSE, title, body)
        st, st_h = group_score(G_SETTING, title, body)
        ng, _    = group_score(G_NEG,     title, body)
        rl, _    = group_score(G_RL,      title, body)
        sd, _    = group_score(SEEDS,     title, body)
        struct_flags = [k for k, pats in G_STRUCT.items()
                        if group_score(pats, title, body)[0] > 0]
        score  = (100 * min(a, o) + 30 * max(a, o) * (1 if o > 0 else 0.5)
                  + (10 if st > 0 else 0) + (6 if struct_flags else 0))
        if ng > 0 and o < 0.34:
            score -= 15
        score  = max(0, min(100, round(score)))
        flags  = ([n for n, v in (("agent", a), ("offense", o), ("setting", st), ("rl", rl)) if v > 0]
                  + ["struct:" + f for f in struct_flags]
                  + (["seed"] if sd > 0 else [])
                  + (["defensive_terms"] if ng > 0 else []))
        safety = (min(a, o) >= 0.34 or o >= 0.67 or sd > 0 or (rl > 0 and o >= 0.34)
                  or pct[i] >= 0.93
                  or (emb_pct[i] is not None and emb_pct[i] >= 0.94)
                  or (o >= 0.34 and bool(struct_flags)))
        out.append({"signal_score": score, "signal_flags": flags,
                    "safety_net": bool(safety),
                    "sim": {k: round(v, 4) for k, v in sims[i].items()},
                    "sim_domain_pct": round(pct[i], 3),
                    "emb_domain": round(emb[i], 4) if emb else None})
    return out
