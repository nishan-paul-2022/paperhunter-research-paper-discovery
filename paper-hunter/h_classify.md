# Master Prompt: Classify Screened Papers with Abstracts (Paper Hunter)

You are an autonomous reviewer with shell access, and optional web access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Each paper in `paper_verdict_with_abstract` with `reviewed = 0` passed semantic screening and has an abstract. Make the final decision on it — accept it into one category, or reject it — and record the decision with a short reason.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_verdict_with_abstract` (set `reviewed`, `verdict`, `reason`). Never change the schema or any other table.
- Decide from the evidence in front of you: the title, authors, venue, year, and abstract. The screening score is only why the paper reached you; it is not evidence of relevance. If the abstract is genuinely ambiguous, you may look the paper up online before deciding, but never invent facts.
- Every paper gets exactly one `verdict`: one of `baseline`, `benchmark`, `survey`, `evaluation`, or `reject`. Screening aims for zero false negatives, so most papers you see will be `reject`; that is expected and correct.

## Research topic

> **LLM-based agentic framework for autonomous black-box penetration testing over long-horizon attack chains**

## Relevance definition (apply exactly)

A paper is **relevant** if it would belong in the related-work or evaluation section of a paper on the research topic: it is about LLM- or AI-agent-driven offensive security (penetration testing, red teaming, CTF solving, exploit generation, attack planning, or automated attack chains), or it is a benchmark or environment that such agents are evaluated on.

| Value | Meaning |
|---|---|
| `baseline` | Proposes a system, framework, or method for automated or LLM-driven offensive security that the research could compare against |
| `benchmark` | Contributes a benchmark, dataset, CTF suite, cyber range, or evaluation environment for offensive-security agents |
| `survey` | Survey, SoK, or systematic review covering the topic or a direct sub-area |
| `evaluation` | Empirical study that measures existing LLMs or agents on offensive-security tasks, without proposing a new system or benchmark |
| `reject` | Not relevant to the research topic |

**Tie-break.** If a paper fits more than one category, use the first match in this order: `benchmark` > `survey` > `baseline` > `evaluation`. (For example, a paper that proposes an agent and also releases a new benchmark is `benchmark`.)

**Judging calls.**
- The offensive work must be driven by LLMs or AI agents, or be an evaluation target for such agents. Classical-only security work (traditional fuzzers, formal methods, non-learning pentest tools, defensive detection, vulnerability scanning without an offensive agent) is `reject`.
- "LLM security" that is *about defending or attacking the model itself* (jailbreaks, prompt injection, model safety, watermarking) is `reject` unless the paper uses an LLM/agent to carry out offensive security against systems.
- Adjacent-but-off-topic work that merely mentions security, or uses an LLM for a non-offensive task, is `reject`.

## Workflow

### 1. Read a batch of pending papers

Process **20** per session, oldest first:

```sh
sqlite3 -readonly archive/paperhunter.db \
  "SELECT v.paper_id, p.title, p.authors, p.venue, p.year, a.abstract
   FROM paper_verdict_with_abstract v
   JOIN paper_dblp p   ON p.paper_id = v.paper_id
   JOIN paper_abstract a ON a.paper_id = v.paper_id
   WHERE v.reviewed = 0
   ORDER BY v.paper_id
   LIMIT 20;"
```

`authors` is a JSON array.

### 2. Decide and classify

For each paper: read the abstract, apply the relevance definition, and choose `accept` into one category or `reject`. If accepted, assign exactly one category using the table and the tie-break.

### 3. Record the decision (one statement per paper)

`reason` is one or two sentences that justify the decision and cite the evidence used (what in the abstract made it relevant or not, and why that category). Write the reason text to a file and load it with `readfile()` so quotes and newlines are safe:

```sh
printf '%s' "$REASON_TEXT" > /tmp/reason.txt

sqlite3 archive/paperhunter.db <<SQL
PRAGMA foreign_keys = ON;
UPDATE paper_verdict_with_abstract
SET reviewed = 1, verdict = 'benchmark', reason = readfile('/tmp/reason.txt')
WHERE paper_id = <ID>;
SQL
```

Use the paper's chosen value in place of `'benchmark'`. Set `reviewed = 1` on every paper you decide, accepted or rejected.

## Finish

Re-read the pending set and continue until no `reviewed = 0` rows remain. The accepted papers (verdict not `reject`) then appear in the `paper_list` view.
