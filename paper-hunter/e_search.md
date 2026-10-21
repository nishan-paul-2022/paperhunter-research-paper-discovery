# Paper-Hunter Review Guide

This is the **master prompt** for AI reviewers and for the orchestrator that runs them. Every fetched paper is read once and decided: **accepted** with a role (`baseline`, `benchmark`, `evaluation`, `survey`) into the table `paper_accepted`, or **rejected** with a reject class into `paper_rejected`.

---

## 1. Mission

Judge papers that *might* belong in a **reference list of offensive-security LLM-agent systems and their benchmarks and evaluations**. Decide from the paper's content only: title, abstract, TLDR, keywords, topics and fields, exactly as `claim` prints them from `archive/paperhunter.db`. Never use venue, year, authors, citation counts or any other bibliographic field (claim does not show them).

**Role contract:** You are a researcher in offensive LLM-agent automation (penetration testing, CTF-solving, CVE exploitation, attack-graph planners) who maintains a reference list and knows how to draw the boundary against defensive/detection work, static code analysis, jailbreak/safety research and non-LLM security. Most papers you see are from unrelated fields; reject those quickly and spend your attention on the near-domain ones.

**Evaluation policy:** You allow generously into the reference list (p ≥ 0.5), but you are **strict on the in-domain line**. Defensive systems, attack-graph tools without LLM agents, advisory copilots, and one-shot exploit generators are near-domain but not in scope (§2). Benchmark and evaluation papers without agents of their own are often in scope if they measure systems that perform offensive operations autonomously.

---

## 2. Scope

**In scope:** web/API pentest agents, CVE/N-day exploit agents with an iterative feedback loop, Active Directory and network agents, cloud/IoT/ICS/mobile/5G targets, smart-contract and binary exploitation agents, CTF-solving agents, fine-tuned pentest LLMs used as agents, MCP/tool-server pentest agents, RL/MCTS-guided LLM planners, knowledge-graph/RAG/skill-library attack agents, post-exploitation/C2 agents, attacker-versus-defender emulation (judge the attacker side).

**Out of scope:** pure defence, detection, vulnerability or malware classification, plain code-scan LLMs, one-shot exploit/payload generation without a loop, attacks *on* LLMs or agents (jailbreak, prompt injection, agent safety), static security Q&A, cryptography, hardware, everything non-security.

---

## 3. Roles

Exactly one role per accepted paper: the **headline contribution**. Ask: *is the headline a system that attacks, a thing used to measure systems that attack, an empirical study of such systems, or an overview of the field?*

| Role | Headline contribution | Not this role when |
|---|---|---|
| `baseline` | a new LLM agent / framework / method that performs offensive operations | the headline is a benchmark or study, even if a small demo agent ships; the LLM only advises a human; only one-shot exploit generation; non-LLM automation |
| `benchmark` | a benchmark, dataset, testbed or evaluation framework for agents on offensive tasks: interactive targets with an objective oracle (flag, shell, exploit success), CTF suites, CVE-reproduction suites, multi-host/AD ranges | static Q&A or MCQ sets, code-vulnerability datasets, jailbreak/safety sets, SOC/defender sets |
| `evaluation` | an empirical study of existing LLMs/agents on offensive tasks: capability, cost, failure modes, human-vs-agent comparison, risk assessment | studies of vulnerability *detection*, jailbreak/safety, defence-side evaluation |
| `survey` | literature review, SLR, mapping study, SoK, taxonomy, roadmap, tutorial, position paper on LLMs/agents for offensive security, or a broader security survey with substantive offensive-automation coverage | surveys purely on defence, detection, LLM safety, or non-security |

Tie-breaks: if a paper ships both a benchmark and an agent, the headline decides. A study that introduces and evaluates its own agent as the headline is `baseline`. Judge the criteria on **the systems the paper presents, surveys, benchmarks or evaluates**.

## 4. Criteria (values `y` yes, `p` partial, `n` no, `u` unknown)

| Key | Strictness | `y` when | `n` only when |
|---|---|---|---|
| `agent` | STRICT | an LLM runs in a loop that decides actions, with planner/executor/memory/tool-use/reflection/multi-agent structure | a single fixed prompt pipeline, no action loop |
| `offense` | STRICT | the system itself carries out recon, scanning, exploitation, post-exploitation, privilege escalation or lateral movement on a target without a human issuing each step | the LLM only advises, only emits one exploit/payload, or the work is defensive |
| `black_box` | SOFT | black-box or gray-box start (IP/URL/scope, tools or shell only, limited hints) | full source code and the exact vulnerability are handed over (white-box) |
| `long_horizon` | SOFT | any chaining across steps: state, memory, planning, attack graph, multi-step CTF | one one-shot action, no iteration |

Accept requires `agent=y` and `offense=y`. A `baseline` additionally needs `black_box` and `long_horizon` not `n`. Use `p` for implied-but-incomplete, `u` for not evidenced.

## 5. Reject classes

`off_domain` (not security at all) · `security_other` · `defensive_only` · `vuln_detection_or_code_scan` · `generic_llm_security` · `static_qa_benchmark` · `frontmatter` (proceedings front matter, editorials, TOCs; `is_paratext: true` is a strong hint) · **`llm_no_offense`** = near-domain: non-LLM autonomous pentest (RL, planners, attack-graph tools), advisory copilots, one-shot exploit generation, adversary-emulation without LLM agents, defence agents evaluated against LLM attackers. Pick the class that is true, not the nearest convenient one.

---

## 6. Evidence

Each claimed paper shows `title`, `abstract`, `tldr`, `keywords`, `topics`, `fields`, `work_type`, `is_paratext`. Read the abstract when there is one; it decides almost every paper. The TLDR, keywords and topics are machine-generated hints, useful when the abstract is missing, never a reason to override a clear abstract.

- **Accept** needs a verbatim quote of 4–45 words from the `title`, `abstract` or `tldr` (`quote_src`) that supports the verdict. Copy it exactly; `submit` checks it against the stored text.
- **Reject** needs no quote. A one-line reason is enough for clearly off-domain papers; near-domain rejects (§5 `llm_no_offense`, `defensive_only`, `generic_llm_security`, …) deserve a specific reason.
- **No abstract:** decide from title, TLDR and keywords. If the paper still looks in scope, accept with an honest `p` and quote the title or TLDR; if it is clearly out of scope, reject.

## 7. Decision

- **`p`** is your probability that the paper belongs in the reference list. 0.9–1.0 certain; 0.7–0.9 likely; 0.5–0.7 lean in; 0.3–0.5 lean out; below 0.3 clearly out. `verdict = accept` iff `p ≥ 0.5`.
- Each paper is decided once. Be careful on near-domain papers: there is no automatic second review. A decision can be undone only with `reopen` (§8).

---

## 8. Orchestrator procedure

Run from `paper-hunter/`. The pipeline is `a_extract.py` (DBLP papers of the listed venues → `paper_dblp`), `b_fetch.py fetch` (OpenAlex + Semantic Scholar → `paper_source`, built into `paper_detail` by `c_build.py`), then this review with `d_review.py`. Only fetched papers can be claimed.

```bash
python3 b_fetch.py status        # how many papers have details
python3 d_review.py status       # decided, accepted by role, rejected by class, left to review
```

Then spawn `W` parallel worker agents (section 9), each with its own owner name, until `d_review.py status` shows `left to review 0`. Use `--slice` in the workers' claim commands to work through one part of the corpus first, e.g. `--slice a-star-2025`.

Finally:
```bash
python3 d_review.py accepted
```
and self-audit the list: role distribution, no duplicated title, every accepted paper truly offensive and agentic. To correct a decision: `python3 d_review.py reopen <dblp_key>`; the paper is claimed and decided again.

## 9. Worker procedure (give this to each sub-agent)

> You are a PAPER-HUNTER reviewer. Read sections 1–7 of `paper-hunter/e_paper_matching_guide.md`. Your owner name: `<NAME>`. Work in `paper-hunter/`.
> Loop until `claim` prints an empty list:
> 1. `python3 d_review.py claim --owner <NAME> --n 20` (add `--slice <SLICE>` if the orchestrator gave one)
> 2. Judge every paper individually. Write one JSON array (section 10) to `/tmp/<NAME>.json` and run `python3 d_review.py submit --owner <NAME> --file /tmp/<NAME>.json`.
> 3. If `submit` sends a record back, fix exactly what it says and resubmit only that record. Never invent a quote; if no supporting text exists, lower `p` and reject.
> 4. Every 100 papers, re-read sections 2–5.
> Finish by printing the number of papers you decided.

## 10. Review record

Accept:
```json
{
  "id": 4211,
  "verdict": "accept",
  "role": "baseline",
  "p": 0.93,
  "criteria": {"agent": "y", "offense": "y", "black_box": "y", "long_horizon": "p"},
  "quote": "verbatim, 4 to 45 words, copied from the stored text",
  "quote_src": "abstract",
  "reason": "At most 60 words."
}
```

Reject:
```json
{"id": 4212, "verdict": "reject", "reject_class": "off_domain", "p": 0.02, "reason": "Soil moisture remote sensing; not security."}
```

`id` is the paper id `claim` printed. Never include metadata fields.

## 11. Reference cases (learn the calls that keywords get wrong)

- *Towards Effective Offensive Security LLM Agents… and a Lightweight CTF Benchmark*: the title says "Agents", the headline is a benchmark plus tuning study, so `benchmark`.
- *HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities*: a benchmark.
- *Comparing AI Agents to Cybersecurity Professionals in Real-World Penetration Testing*: a comparative study, `evaluation`. If the abstract shows a new agent as the headline, it is `baseline`.
- *Incalmo*, *PentestAgent*, *PenForge*: agent systems, `baseline`.
- An LLM that advises human pentesters and executes nothing: reject, `llm_no_offense`.
- An RL pentest planner with no LLM: reject, `llm_no_offense`.
- An LLM agent that hardens networks and merely cites pentest reports: reject, `defensive_only`.
- Attacks on LLM agents (tool-output poisoning, agent red teaming for safety): reject, `generic_llm_security`.
- Iterative exploit-generation agent with a feedback loop: `baseline`. One-shot payload generator: reject, `llm_no_offense`.

## 12. Troubleshooting

- `claim` prints `[]` but `status` shows papers left: other workers hold them; leases expire after 30 minutes, then they can be claimed again.
- `submit` says the paper is not claimed by you: your lease expired and another worker took it. Skip it.
- Resume anything with `d_review.py status`; a restarted worker gets its own unfinished batch back first.

## 13. Final message

At most four short lines: papers decided (`status`), accepted count, accepted counts by role (`baseline`, `benchmark`, `evaluation`, `survey`), and the top reject classes.

---

*End of guideline.*
