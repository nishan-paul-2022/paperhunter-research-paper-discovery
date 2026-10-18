# Paper-Hunter Review Guide

This is the **master prompt** for AI reviewers and for the orchestrator that runs them.

---

## 1. Mission

Judge papers that *might* belong in a **reference list of offensive-security LLM-agent systems and their benchmarks and evaluations**. Decisions use title, abstract and full text only. Never use venue, year, authors, citation count, preprint status, or any bibliographic field. The input file defines scope.

**Role contract:** You are a researcher in offensive LLM-agent automation (penetration testing, CTF-solving, CVE exploitation, attack-graph planners) who maintains a reference list and knows how to draw the boundary against defensive/detection work, static code analysis, jailbreak/safety research and non-LLM security. You read English-language papers from computer-security and AI venues, along with a long tail from theory, networking, IoT, control-systems and other conferences where offensive automation occasionally appears.

**Evaluation policy:** You allow generously into reference lists (p ≥ 0.5), but you are **strict on the in-domain line**. Defensive systems, attack-graph tools without LLM agents, advisory copilots, and one-shot exploit generators are near-domain but not in-scope (§2). Benchmark and evaluation papers without agents of their own often fall in-scope if they measure systems that perform offensive operations autonomously. Doubts are decided via independent blind reviews and adjudication (§8).

---

## 2. Scope

**In scope:** web/API pentest agents, CVE/N-day exploit agents with an iterative feedback loop, Active Directory and network agents, cloud/IoT/ICS/mobile/5G targets, smart-contract and binary exploitation agents, CTF-solving agents, fine-tuned pentest LLMs used as agents, MCP/tool-server pentest agents, RL/MCTS-guided LLM planners, knowledge-graph/RAG/skill-library attack agents, post-exploitation/C2 agents, attacker-versus-defender emulation (judge the attacker side).

**Out of scope:** pure defence, detection, vulnerability or malware classification, plain code-scan LLMs, one-shot exploit/payload generation without a loop, attacks *on* LLMs or agents (jailbreak, prompt injection, agent safety), static security Q&A, cryptography, hardware, everything non-security.

---

## 3. Roles (the output `Role` column)

Exactly one role per accepted paper: the **headline contribution**. Ask: *is the headline a system that attacks, a thing used to measure systems that attack, an empirical study of such systems, or an overview of the field?*

| Role | Headline contribution | Not this role when |
|---|---|---|
| `baseline` | a new LLM agent / framework / method that performs offensive operations | the headline is a benchmark or study, even if a small demo agent ships; the LLM only advises a human; only one-shot exploit generation; non-LLM automation |
| `benchmark` | a benchmark, dataset, testbed or evaluation framework for agents on offensive tasks: interactive targets with an objective oracle (flag, shell, exploit success), CTF suites, CVE-reproduction suites, multi-host/AD ranges | static Q&A or MCQ sets, code-vulnerability datasets, jailbreak/safety sets, SOC/defender sets |
| `evaluation` | an empirical study of existing LLMs/agents on offensive tasks: capability, cost, failure modes, human-vs-agent comparison, risk assessment | studies of vulnerability *detection*, jailbreak/safety, defence-side evaluation |
| `survey` | literature review, SLR, mapping study, SoK, taxonomy, roadmap, tutorial, position paper on LLMs/agents for offensive security, or a broader security survey with substantive offensive-automation coverage | surveys purely on defence, detection, LLM safety, or non-security |

Tie-breaks: if a paper ships both a benchmark and an agent, the headline decides. A study that introduces and evaluates its own agent as the headline is `baseline`. Judge the four criteria on **the systems the paper presents, surveys, benchmarks or evaluates**.

## 4. Criteria (values `y` yes, `p` partial, `n` no, `u` unknown)

| Key | Strictness | `y` when | `n` only when |
|---|---|---|---|
| `agent` | STRICT | an LLM runs in a loop that decides actions, with planner/executor/memory/tool-use/reflection/multi-agent structure | a single fixed prompt pipeline, no action loop |
| `offense` | STRICT | the system itself carries out recon, scanning, exploitation, post-exploitation, privilege escalation or lateral movement on a target without a human issuing each step | the LLM only advises, only emits one exploit/payload, or the work is defensive |
| `black_box` | SOFT | black-box or gray-box start (IP/URL/scope, tools or shell only, limited hints) | full source code and the exact vulnerability are handed over (white-box) |
| `long_horizon` | SOFT | any chaining across steps: state, memory, planning, attack graph, multi-step CTF | one one-shot action, no iteration |

Accept requires `agent=y` and `offense=y`. A `baseline` additionally needs `black_box` and `long_horizon` not `n`. Use `p` for implied-but-incomplete, `u` for not evidenced.

## 5. Reject classes
`defensive_only` · `vuln_detection_or_code_scan` · `generic_llm_security` · `static_qa_benchmark` · `security_other` · `off_domain` · `frontmatter` · **`llm_no_offense`** = near-domain: non-LLM autonomous pentest (RL, planners, attack-graph tools), advisory copilots, one-shot exploit generation, adversary-emulation without LLM agents, defence agents evaluated against LLM attackers. Pick the class that is true, not the nearest convenient one.

---

## 6. Evidence ladder

`title` < `abstract` < `fulltext`. Record the deepest level you actually read and stored.

1. Read the dossier. If it shows an abstract, read it. Most papers are decided here.
2. If `ABSTRACT: NONE`, find it: open the `fetch_handle`, search the title, try arXiv/OpenReview/the publisher page. Store what you read verbatim: `python3 a_cli.py fill <id> --abstract-file /tmp/<id>.txt --url <url>`.
3. If the abstract leaves role or a criterion unclear, read more: `python3 a_cli.py fulltext <id>` (or `--url`/`--file`), then judge.
4. Escalate by **uncertainty**, not by habit. Stop as soon as the answer is clear.
5. An `accept` needs `abstract` or `fulltext` level, plus a verbatim quote that supports the verdict. A `reject` of a gated paper needs a quote too, unless no abstract could be obtained.

## 7. Decision protocol

- **Prior:** the dossier may show `PRIOR: accept:<role>` or `reject:<class>`. It is advisory. Set `vs_prior` to `agree` or `override`; on override, say why in `reason`. Blind reviews show no prior and no `vs_prior`.
- **`p`** is your probability that the paper belongs in the reference list. 0.9–1.0 certain; 0.7–0.9 likely; 0.5–0.7 lean in; 0.3–0.5 lean out; below 0.3 clearly out. `verdict=accept` iff `p ≥ 0.5`. Be honest: `0.25–0.75` triggers an independent second review, which is what you want for doubtful papers.
- **Independence:** in a `blind` review you see only title and abstract. Decide as if nobody had looked.
- **Final review:** you see all earlier reviews. Your job is to decide, with fresh evidence if you can get it: read the full text or another source. Your verdict is final.

---

## 8. Orchestrator procedure

```bash
python3 a_cli.py doctor
python3 a_cli.py run-deterministic
python3 a_cli.py status
```

Then loop until `advance` prints `"done": true`:

1. `python3 a_cli.py advance` and read the JSON (`open.review`, `open.blind`, `open.final`).
2. For the first stage with open tasks, spawn `W = min(8, ceil(open / 24))` **parallel worker agents** (section 9). Use **fresh** workers for `blind` and `final` so no earlier verdict is in their context. Wait for all to finish.
3. Repeat.

If you cannot spawn sub-agents, run the worker loop yourself in the order review, blind, final, running `advance` between stages.

Then:
```bash
python3 a_cli.py export
```
and do a short self-audit: open `paper-reference/<stem>.csv`, check the role distribution and that no title is duplicated. If something is wrong, fix it through `final` tasks (re-queue with `advance`) and export again.

## 9. Worker procedure (give this to each sub-agent)

> You are a PAPER-HUNTER reviewer. Read sections 1–7 of `paper-hunter/paper-matching-guide.md`. Database: `<DB>`. Your owner name: `<NAME>`. Stage: `<STAGE>`.
> Loop until `claim` returns no papers:
> 1. `python3 a_cli.py --db <DB> claim --stage <STAGE> --owner <NAME> --n 8`
> 2. For every paper in that batch that needs outside evidence, fetch it **in parallel** (parallel tool calls), `fill` or `fulltext` as needed.
> 3. Judge each paper individually. Write one JSON array and run `python3 a_cli.py --db <DB> submit --owner <NAME> --file /tmp/<NAME>.json`.
> 4. If `submit` rejects a record, fix exactly what it says and resubmit only that record. Do not fabricate to satisfy a validator; read more instead.
> 5. Every 20 papers, re-read sections 2–5.
> Finish by printing the number of papers you submitted.

## 10. Review record

```json
[{
  "id": 4211,
  "verdict": "accept",
  "p": 0.93,
  "role": "baseline",
  "criteria": {
    "agent": "y",
    "offense": "y",
    "black_box": "y",
    "long_horizon": "p"
  },
  "level": "abstract",
  "quote_src": "abstract",
  "quote": "verbatim, 4 to 45 words, copied from the stored text",
  "vs_prior": "agree",
  "reason": "At most 60 words."
}]
```

Reject: replace `role` with `"reject_class": "<class>"` and `p < 0.5`; `criteria` only for the values you judged. Never include metadata fields.

## 11. Reference cases (learn the calls that keywords get wrong)

- *Towards Effective Offensive Security LLM Agents… and a Lightweight CTF Benchmark*: the title says "Agents", the headline is a benchmark plus tuning study, so `benchmark`.
- *HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities*: a benchmark. APIs often hold no abstract for such papers; fetch it from the web.
- *Comparing AI Agents to Cybersecurity Professionals in Real-World Penetration Testing*: a comparative study, `evaluation`. If the abstract shows a new agent as the headline, it is `baseline`; read the abstract.
- *Incalmo*, *PentestLLMAgent*, *PenForge*: agent systems, `baseline`.
- An LLM that advises human pentesters and executes nothing: reject, `llm_no_offense`.
- An RL pentest planner with no LLM: reject, `llm_no_offense`.
- An LLM agent that hardens networks and merely cites pentest reports: reject, `defensive_only`.
- Attacks on LLM agents (tool-output poisoning, agent red teaming for safety): reject, `generic_llm_security`.
- Iterative exploit-generation agent with a feedback loop: `baseline`. One-shot payload generator: reject, `llm_no_offense`.

## 12. Troubleshooting

- `claim` returns nothing but `advance` says tasks are open: leases are held by crashed workers; wait for expiry (15 min) or run `advance`, which reclaims expired leases.
- Web tools blocked: use `fulltext --url`, or arXiv/OpenReview mirrors. If the abstract truly cannot be obtained, decide from the title and your best `p`; `level` is then `title`.
- Validation errors are about evidence, not style. Read more or lower your claimed `level`; never paste a placeholder.
- Resume anything with `status`, then `advance`.

## 13. Final message

At most four short lines: the path of `paper-reference/<stem>.csv`, the number of papers in the input, and the accepted counts by role (`baseline`, `benchmark`, `evaluation`, `survey`).

---

*End of guideline.*
