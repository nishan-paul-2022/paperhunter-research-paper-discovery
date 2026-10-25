# Master Prompt: G · AI Review of the Semantic Shortlist (Paper Hunter)

You are an autonomous reviewer with shell access and, where needed, web access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Move every paper at status `F` to status `G`. Papers that are not in `paper_semantic_shortlist` move at once. Review each shortlisted paper and, if it belongs to the research topic, add it to `paper_ai_shortlist` with its category.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Use `.venv/bin/python`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_ai_shortlist` and `paper_status.status`. Never change the schema or any other table.
- Decide from the title, authors, venue, year and abstract. Shortlisting only means the paper reached you; it is not evidence of relevance. Look a paper up online only if it has no abstract and its title alone cannot decide it. Never invent facts.
- Write each paper's decision and its status change in **one transaction**.

## Research topic

Read it from `archive/semantic.json` (`topic`):

```sh
.venv/bin/python -c "import json; print(json.load(open('archive/semantic.json'))['topic'])"
```

## Relevance and categories

A paper is **relevant** if it would belong in the related-work or evaluation section of a paper on the research topic: LLM- or AI-agent-driven offensive security (penetration testing, red teaming, CTF solving, exploit generation, attack planning, automated attack chains), or a benchmark or environment such agents are evaluated on.

| Category | Meaning |
|---|---|
| `baseline` | Proposes a system, framework or method for automated or LLM-driven offensive security |
| `benchmark` | Contributes a benchmark, dataset, CTF suite, cyber range or evaluation environment for offensive-security agents |
| `survey` | Survey, SoK or systematic review of the topic or a direct sub-area |
| `evaluation` | Empirical study measuring existing LLMs or agents on offensive-security tasks, without a new system or benchmark |

- **Tie-break:** `benchmark` > `survey` > `baseline` > `evaluation`.
- **Reject** (no row in `paper_ai_shortlist`): classical security work with no LLM or AI agent; defensive work; attacks *on* the model itself (jailbreaks, prompt injection, model safety) unless an LLM or agent attacks systems; work that only mentions security or uses an LLM for a non-offensive task.

## Step 1: Move papers that were not shortlisted

```sh
sqlite3 archive/paperhunter.db "
PRAGMA foreign_keys = ON;
UPDATE paper_status SET status = 'G'
WHERE status = 'F' AND paper_id NOT IN (SELECT paper_id FROM paper_semantic_shortlist);"
```

## Step 2: Review shortlisted papers, one at a time

Read a batch of 20:

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT paper_id, title, authors, venue, year, abstract
FROM paper_summary
WHERE status = 'F' AND paper_id IN (SELECT paper_id FROM paper_semantic_shortlist)
ORDER BY paper_id LIMIT 20;"
```

Record each decision. Use a category to accept, or `None` to reject:

```sh
.venv/bin/python - <<'PY'
import sqlite3
paper_id = 123456
category = None
conn = sqlite3.connect("archive/paperhunter.db", timeout=120)
conn.execute("PRAGMA foreign_keys = ON")
with conn:
    if category:
        conn.execute("INSERT OR REPLACE INTO paper_ai_shortlist (paper_id, category) VALUES (?, ?)",
                     (paper_id, category))
    conn.execute("UPDATE paper_status SET status = 'G' WHERE paper_id = ? AND status = 'F'", (paper_id,))
print("recorded", paper_id, category)
PY
```

Repeat batches until the query returns nothing. An interrupted session resumes where it stopped, because reviewed papers are already at `G`.

## Finish

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT category, count(*) FROM paper_ai_shortlist GROUP BY category;
SELECT status, count(*) FROM paper_status GROUP BY status;"
```

The accepted papers are in the view `paper_list`.
