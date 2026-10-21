# Paper-Hunter Review Guide

This is the **master prompt** for AI reviewers and for the orchestrator that runs them. Every fetched paper is read once and decided in the table `paper_decision`: **accepted** with a role (`baseline`, `benchmark`, `evaluation`, `survey`), or **rejected** with a reject class. The view `paper_list` holds the accepted papers once the view `paper_review` is empty.

---

## 1. Mission

Judge papers that *might* belong in a **reference list of offensive-security LLM-agent systems and their benchmarks and evaluations**. Decide from the paper's content only: title, abstract, TLDR, keywords, topics and fields, exactly as the worker query (§9) prints them from `archive/paperhunter.db`. Never use venue, year, authors, citation counts or any other bibliographic field (the query does not show them).

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

Each paper shows `title`, `abstract`, `tldr`, `keywords`, `topics`, `fields`, `work_type`, `is_paratext`. Read the abstract when there is one; it decides almost every paper. The TLDR, keywords and topics are machine-generated hints, useful when the abstract is missing, never a reason to override a clear abstract.

- **Accept** needs a verbatim quote of 4–45 words from the `title`, `abstract` or `tldr` (`quote_src`) that supports the verdict. Copy it exactly, character for character (dashes, quotes, accents); the database refuses a quote that is not in the stored text.
- **Reject** needs no quote. A one-line reason is enough for clearly off-domain papers; near-domain rejects (§5 `llm_no_offense`, `defensive_only`, `generic_llm_security`, …) deserve a specific reason.
- **No abstract:** decide from title, TLDR and keywords. If the paper still looks in scope, accept with an honest `p` and quote the title or TLDR; if it is clearly out of scope, reject.

## 7. Decision

- **`p`** is your probability that the paper belongs in the reference list. 0.9–1.0 certain; 0.7–0.9 likely; 0.5–0.7 lean in; 0.3–0.5 lean out; below 0.3 clearly out. `verdict = accept` iff `p ≥ 0.5`.
- Each paper is decided once by its worker. Be careful on near-domain papers: only accepts and borderline rejects get a second look (§10 step 4).

---

## 8. The data

Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`; the database is `archive/paperhunter.db`. Change only `paper_decision`. Never alter the schema or any other table.

| object | what it holds |
|---|---|
| `paper_dblp(id, dblp_key, venue_key, year, title, authors, venue, doi)` | every 2023–2026 paper of the venues in `conf_list` and `journal_list`, from `paper-hunter/a_extract.py` |
| `paper_source(paper_id, src, via, found, raw, fetched_at)` | the raw OpenAlex and Semantic Scholar answers, from `paper-hunter/b_details.py`. Never read it |
| `paper_detail(paper_id, abstract, abstract_src, tldr, keywords, topics, fields, work_type, is_paratext, is_retracted, language, arxiv_id, openalex_id, s2_id)` | one merged row per paper that either API found: the evidence you judge. `keywords`, `topics`, `fields` are `; `-separated. A paper neither API found has no row; judge it from its title |
| `paper_decision(paper_id, verdict, role, p, agent, offense, black_box, long_horizon, quote, quote_src, reject_class, reason, reviewer, decided_at)` | **your output**, one row per paper |
| `paper_review` | to-do list: `UNFETCHED` (details not fetched yet: not your work), `UNDECIDED` (fetched, no decision), `INVALID` (an accept whose quote is no longer in the stored text) |
| `paper_list` | final output: the accepted papers. It stays empty while `paper_review` has any row |

The database refuses a decision that breaks the rules of §3–§7:
- a paper that is not `UNDECIDED` (unknown, not fetched, or already decided);
- an accept without a role, with `p < 0.5`, with `agent` or `offense` other than `y`, without `black_box`, `long_horizon` or `quote_src`, with a `reject_class`, or with a quote outside 4–45 words or not found verbatim in its `quote_src`;
- a `baseline` with `black_box` or `long_horizon` = `n`;
- a reject without a `reject_class`, with `p >= 0.5`, or with a `role`, `quote` or `quote_src`;
- a `reason` shorter than 10 characters or longer than 60 words; an empty `reviewer`.

If an insert is refused, your record is wrong: fix exactly what the error names, never work around the constraint.

## 9. Worker procedure (give this to each sub-agent)

> You are a PAPER-HUNTER reviewer. Read sections 1–8 and 11–12 of `paper-hunter/c_search.md`. Your name: `<NAME>`. Your share: papers with `paper_id % <W> = <K>`. Work from the repository root.
> Loop until step 1 prints `[]`:
> 1. Read your next 20 papers:
>    ```bash
>    python3 - <<'EOF'
>    import json, sqlite3
>    conn = sqlite3.connect("archive/paperhunter.db", timeout=300)
>    conn.row_factory = sqlite3.Row
>    rows = conn.execute("""
>        SELECT p.id AS paper_id, p.title, d.abstract, d.tldr, d.keywords, d.topics, d.fields, d.work_type, d.is_paratext
>        FROM paper_review r JOIN paper_dblp p ON p.id = r.paper_id LEFT JOIN paper_detail d ON d.paper_id = p.id
>        WHERE r.issue = 'UNDECIDED' AND r.paper_id % <W> = <K> LIMIT 20""").fetchall()
>    print(json.dumps([dict(r) for r in rows], indent=1, ensure_ascii=False))
>    EOF
>    ```
> 2. Judge every paper individually (§1–7). Write one JSON array of records (§11) to `/tmp/<NAME>.json`.
> 3. Store them; each record is inserted on its own and reported `ok` or `REFUSED` with the reason:
>    ```bash
>    python3 - <<'EOF'
>    import json, sqlite3
>    COLS = ("paper_id", "verdict", "role", "p", "agent", "offense", "black_box", "long_horizon",
>            "quote", "quote_src", "reject_class", "reason", "reviewer")
>    conn = sqlite3.connect("archive/paperhunter.db", timeout=300)
>    conn.execute("PRAGMA foreign_keys = ON")
>    for rec in json.load(open("/tmp/<NAME>.json")):
>        try:
>            with conn:
>                conn.execute(f"INSERT INTO paper_decision ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
>                             [{**rec, "reviewer": "<NAME>"}.get(c) for c in COLS])
>            print(rec.get("paper_id"), "ok")
>        except sqlite3.Error as e:
>            print(rec.get("paper_id"), "REFUSED:", e)
>    EOF
>    ```
> 4. For every `REFUSED` record, fix exactly what the error says and store only that record again. Never invent a quote: if no stored text supports the paper, lower `p` and reject.
> 5. Every 100 papers, re-read sections 2–5.
>
> Finish by printing the number of papers you decided.

## 10. Orchestrator procedure

**Step 0. Preflight.**

```sql
SELECT issue, count(*) FROM paper_review GROUP BY issue;
SELECT verdict, count(*) FROM paper_decision GROUP BY verdict;
```

- `paper_dblp` empty: stop and tell the humans to run `paper-hunter/a_extract.py`, then `paper-hunter/b_details.py`.
- Any `UNFETCHED` row: stop and tell the humans to run `paper-hunter/b_details.py` (when an API's daily budget runs out, again the next day) until `UNFETCHED` is gone.

**Step 1. `INVALID`.** For each row, delete the decision (`DELETE FROM paper_decision WHERE paper_id = ?`); the paper comes back as `UNDECIDED`.

**Step 2. Review.** Spawn `W` parallel workers (§9), worker `K` = 0 … `W`−1, each with its own name. When a worker finishes or dies, start it again with the same `K` until no `UNDECIDED` row is left.

**Step 3. Read the result.**

```sql
SELECT role, count(*) FROM paper_decision WHERE verdict = 'accept' GROUP BY role;
SELECT reject_class, count(*) FROM paper_decision WHERE verdict = 'reject' GROUP BY reject_class ORDER BY 2 DESC;
```

**Step 4. Second look.** Re-read every accept and every reject with `p >= 0.3` against §2–5 and §12, with the same evidence the workers saw:

```sql
SELECT d.paper_id, d.verdict, d.role, d.p, d.reject_class, d.quote, d.reason, p.title, x.abstract, x.tldr, x.keywords
FROM paper_decision d JOIN paper_dblp p ON p.id = d.paper_id LEFT JOIN paper_detail x ON x.paper_id = d.paper_id
WHERE d.verdict = 'accept' OR d.p >= 0.3
ORDER BY d.verdict, d.role, d.p;
```

To correct a decision, delete it and insert the right record with your own name as `reviewer` (the worker's step 3). Check that every accepted paper is truly agentic and offensive and that its role is its headline contribution. Accepted papers with the same title (a workshop and a main-track version, a conference paper and its journal extension) are separate publications: keep both if both are in scope.

**Step 5. Final gate.** Both must hold; otherwise go back to Step 0.
- `SELECT count(*) FROM paper_review;` returns 0.
- `SELECT count(*) FROM paper_list;` equals the number of accepts.

## 11. Review record

Keys are the columns of `paper_decision`; leave out the ones that do not apply.

Accept:
```json
{
  "paper_id": 4211,
  "verdict": "accept",
  "role": "baseline",
  "p": 0.93,
  "agent": "y", "offense": "y", "black_box": "y", "long_horizon": "p",
  "quote": "verbatim, 4 to 45 words, copied from the stored text",
  "quote_src": "abstract",
  "reason": "At most 60 words."
}
```

Reject (the criteria are optional on a reject):
```json
{"paper_id": 4212, "verdict": "reject", "reject_class": "off_domain", "p": 0.02, "reason": "Soil moisture remote sensing; not security."}
```

`paper_id` is the id the worker query printed. Never include metadata fields.

## 12. Reference cases (learn the calls that keywords get wrong)

- *Towards Effective Offensive Security LLM Agents… and a Lightweight CTF Benchmark*: the title says "Agents", the headline is a benchmark plus tuning study, so `benchmark`.
- *HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities*: a benchmark.
- *Comparing AI Agents to Cybersecurity Professionals in Real-World Penetration Testing*: a comparative study, `evaluation`. If the abstract shows a new agent as the headline, it is `baseline`.
- *Incalmo*, *PentestAgent*, *PenForge*: agent systems, `baseline`.
- An LLM that advises human pentesters and executes nothing: reject, `llm_no_offense`.
- An RL pentest planner with no LLM: reject, `llm_no_offense`.
- An LLM agent that hardens networks and merely cites pentest reports: reject, `defensive_only`.
- Attacks on LLM agents (tool-output poisoning, agent red teaming for safety): reject, `generic_llm_security`.
- Iterative exploit-generation agent with a feedback loop: `baseline`. One-shot payload generator: reject, `llm_no_offense`.

## 13. Troubleshooting

- Step 1 of the worker prints `[]` but `paper_review` still shows `UNDECIDED` rows: they belong to other workers' shares (`paper_id % W`); restart the worker that owns them.
- `paper is not UNDECIDED`: another reviewer already decided the paper, or it is not fetched yet. Skip it.
- `quote not found verbatim`: copy the quote again from the printed text, character for character, or quote another source.
- `CHECK constraint failed`: the record breaks a rule of §8; the error names it.
- `database is locked`: another worker is writing; run the step again.

## 14. Final message

At most four short lines: papers decided, accepted count, accepted counts by role (`baseline`, `benchmark`, `evaluation`, `survey`), and the top reject classes.

---

*End of guideline.*
