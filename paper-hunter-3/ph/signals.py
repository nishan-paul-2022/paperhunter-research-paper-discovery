"""Lexical / BM25 / fuzzy SIGNALS. They order work and assign review depth (lane A/B/C).
They NEVER reject a paper: every paper still receives an agent verdict."""
import re, math, difflib
from collections import Counter

GROUPS = {
  "agent": ["llm", "llms", "large language model", "language model", "gpt", "chatgpt", "claude", "gemini", "llama",
            "agent", "agents", "agentic", "multi agent", "autonomous", "copilot", "react", "tool use",
            "tool augmented", "tool learning", "planner", "reasoning model", "foundation model", "generative ai",
            "genai", "mcp", "model context protocol", "reinforcement learning", "mcts", "monte carlo tree search",
            "retrieval augmented", "rag", "reflection", "self correction"],
  "offense": ["penetration test", "penetration testing", "pentest", "pentesting", "pen test", "pen testing",
              "red team", "red teaming", "offensive security", "offensive", "ethical hacking", "hacking", "hacker",
              "exploit", "exploits", "exploitation", "exploit generation", "vapt", "vulnerability assessment",
              "adversary emulation", "adversarial emulation", "privilege escalation", "lateral movement",
              "post exploitation", "ctf", "capture the flag", "kill chain", "attack chain", "attack path",
              "attack graph", "attack planning", "mitre att ck", "att ck", "cyber range", "cyber operations",
              "reconnaissance", "command and control", "c2", "sql injection", "xss", "web attack",
              "cve", "n day", "one day", "zero day", "vulnerability reproduction", "binary exploitation", "pwn",
              "attack automation", "automated attack", "intrusion"],
  "strong_offense": ["penetration test", "penetration testing", "pentest", "pentesting", "pen test", "pen testing",
                     "red team", "red teaming", "offensive security", "ethical hacking", "exploit generation",
                     "adversary emulation", "capture the flag", "ctf", "privilege escalation", "lateral movement",
                     "post exploitation", "kill chain", "vapt", "cyber range", "attack chain", "attack graph"],
  "structure": ["multi step", "multistep", "multi stage", "attack chain", "kill chain", "attack path", "attack graph",
                "planning", "long horizon", "task tree", "backtracking", "replanning", "state tracking",
                "context management", "memory", "hierarchical", "workflow"],
  "setting": ["black box", "blackbox", "gray box", "grey box", "white box", "zero knowledge", "unknown target"],
  "survey": ["survey", "systematic review", "systematic literature review", "literature review", "sok",
             "systematization of knowledge", "taxonomy", "roadmap", "state of the art", "overview", "review of",
             "position paper", "perspective", "challenges and opportunities", "landscape", "mapping study"],
  "benchmark": ["benchmark", "dataset", "data set", "testbed", "test bed", "corpus", "leaderboard", "environment",
                "gym", "cyber range", "simulator", "simulation environment", "evaluation framework", "suite"],
  "evaluation": ["evaluation", "evaluating", "empirical", "empirical study", "comparative", "comparison",
                 "case study", "assessment", "assessing", "how well", "can llms", "can large language models",
                 "capabilities", "uplift", "user study", "replication", "reproducibility", "measuring", "study of"],
  "security": ["security", "cyber", "cybersecurity", "vulnerability", "vulnerabilities", "threat", "malware",
               "attack", "attacks", "attacker", "adversarial", "adversary", "defense", "defence", "intrusion",
               "forensics", "incident", "phishing", "ransomware", "botnet", "exfiltration", "soc", "siem", "network security", "web security", "iot security"],
}
DEMOTE = ["intrusion detection", "anomaly detection", "malware detection", "phishing detection", "spam",
          "fraud detection", "firewall", "steganography", "watermark", "blockchain consensus"]

SEED_TITLES = [  # agent systems / reference works used ONLY as fuzzy anchors (not as truth)
  "PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing",
  "AutoAttacker: A Large Language Model Guided System to Implement Automatic Cyber-attacks",
  "VulnBot: Autonomous Penetration Testing for A Multi-Agent Collaborative Framework",
  "HackSynth: LLM Agent and Evaluation Framework for Autonomous Penetration Testing",
  "Getting pwned by AI: Penetration Testing with Large Language Models",
  "LLM Agents can Autonomously Exploit One-day Vulnerabilities",
  "LLM Agents can Autonomously Hack Websites",
  "Teams of LLM Agents can Exploit Zero-Day Vulnerabilities",
  "Cybench: A Framework for Evaluating Cybersecurity Capabilities and Risks of Language Models",
  "EnIGMA: Interactive Tools Substantially Assist LM Agents in Finding Security Vulnerabilities",
  "AutoPenBench: Benchmarking Generative Agents for Penetration Testing",
  "Large Language Models for Cyber Security: A Systematic Literature Review",
  "Hacking, the Lazy Way: LLM Augmented Pentesting",
  "Red teaming agent for autonomous cyber operations",
  "Large language model based multi-agent penetration testing attack chain planning",
]
QUERIES = [  # BM25 query descriptors (semantic-ish anchors)
  "LLM agent autonomous penetration testing",
  "multi-agent framework red team offensive security exploitation",
  "large language model automated attack chain planning kill chain",
  "CTF capture the flag solving agent exploit",
  "survey of large language models for penetration testing offensive cybersecurity",
  "benchmark for evaluating LLM agents on penetration testing and CTF tasks",
  "empirical evaluation of large language models hacking capabilities cyber operations",
  "black-box gray-box vulnerability exploitation tool-augmented language model",
  "autonomous agents cyber range adversary emulation MITRE ATT&CK",
  "web application API vulnerability exploitation LLM agent",
]

def tok_text(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _compile(terms):
    return [(t, re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])")) for t in terms]
_RX = {g: _compile(v) for g, v in GROUPS.items()}
_DEM = _compile(DEMOTE)

def group_hits(text):
    return {g: sorted({t for t, rx in rxs if rx.search(text)}) for g, rxs in _RX.items()}

STOP = set("the a an of for and or in on to with from by is are using based via towards toward into its their this that as at be can".split())
def words(text): return [w for w in text.split() if w not in STOP and len(w) > 1]

class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.docs = docs; self.k1 = k1; self.b = b
        self.N = len(docs); self.avg = sum(len(d) for d in docs) / max(1, self.N)
        df = Counter(w for d in docs for w in set(d))
        self.idf = {w: math.log(1 + (self.N - n + .5) / (n + .5)) for w, n in df.items()}
        self.tf = [Counter(d) for d in docs]
    def score(self, q):
        out = []
        for i, d in enumerate(self.docs):
            L = len(d) or 1; s = 0.0; tf = self.tf[i]
            for w in set(q):
                f = tf.get(w, 0)
                if f: s += self.idf.get(w, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / self.avg))
            out.append(s)
        return out

def fuzzy_seed(title_tok):
    best, which = 0.0, ""
    for s in SEED_TITLES:
        r = difflib.SequenceMatcher(None, title_tok, tok_text(s)).ratio()
        if r > best: best, which = r, s
    return best, which

KEY_VOCAB = sorted({w for g in ("strong_offense", "offense") for t in GROUPS[g] for w in t.split() if len(w) >= 8} | {"agentic", "autonomous", "pentesting"})
def typo_hits(text):
    """Catches misspellings/variants (pentesting, penetration-testing, exploitaton) that exact matching misses."""
    hits = set()
    for w in set(text.split()):
        if len(w) >= 8 and w not in KEY_VOCAB:
            m = difflib.get_close_matches(w, KEY_VOCAB, n=1, cutoff=0.9)
            if m: hits.add(f"{w}~{m[0]}")
    return sorted(hits)

def percentile_ranks(vals):
    order = sorted(vals); n = len(vals)
    import bisect
    return [bisect.bisect_left(order, v) / max(1, n - 1) * 100 for v in vals]

def compute(papers, enrich_by_id=None):
    enrich_by_id = enrich_by_id or {}
    texts, basis = [], []
    for p in papers:
        ab = (enrich_by_id.get(p["id"]) or {}).get("abstract") or ""
        texts.append(tok_text(p["title"] + " . " + ab)); basis.append("title+abstract" if ab else "title")
    bm = BM25([words(t) for t in texts])
    raw = [0.0] * len(papers)
    for q in QUERIES:
        sc = bm.score(words(tok_text(q))); mx = max(sc) or 1
        raw = [r + s / mx for r, s in zip(raw, sc)]
    pct = percentile_ranks(raw)
    out = []
    for p, t, bs, rw, pc in zip(papers, texts, basis, raw, pct):
        h = group_hits(t); ty = typo_hits(t)
        fz, fz_t = fuzzy_seed(tok_text(p["title"]))
        dem = [d for d, rx in _DEM if rx.search(t)]
        A, O, SO, S, E, G = (len(h[k]) for k in ("agent", "offense", "strong_offense", "structure", "survey", "security"))
        ev = len(h["benchmark"]) + len(h["evaluation"]) + E
        score = (min(A, 3) * 8 + min(O, 4) * 9 + min(SO, 2) * 12 + min(S, 2) * 3 + len(h["setting"]) * 3 +
                 min(G, 3) * 3 + (10 if ev and G else 0) + len(ty) * 3 + pc * 0.15 + fz * 30 - len(dem) * 8)
        score = max(0.0, min(100.0, score))
        if SO or (A and O) or fz >= 0.6 or pc >= 98 or (ty and (A or G)):
            lane = "A"
        elif A or O or G or ty or pc >= 85 or (ev and G):
            lane = "B"
        else:
            lane = "C"
        out.append({"id": p["id"], "lane": lane, "score": round(score, 1), "bm25_pct": round(pc, 1),
                    "seed_fuzzy": round(fz, 2), "seed_match": fz_t if fz >= 0.45 else "",
                    "hits": {k: v for k, v in h.items() if v}, "typo_hits": ty, "demote_hits": dem,
                    "role_hints": [r for r in ("survey", "benchmark", "evaluation") if h[r]],
                    "text_basis": bs})
    return out
