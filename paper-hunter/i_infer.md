# Master Prompt: Decide Screened Papers Without Abstracts (Paper Hunter)

You are an autonomous reviewer with web search and shell access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Some screened-in papers still have no abstract after the APIs and the web-lookup pass. They are listed in `paper_lookup_queue`. For each, either find and store a genuine abstract online, or, failing that, decide from every other piece of evidence whether the paper is relevant and classify it. Run this only after the web-lookup prompt (`g_lookup.md`) has worked through the queue.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_verdict_without_abstract`, and, in the abstract-found case only, to `paper_abstract`, `paper_abstract_hit`, `paper_abstract_miss`, `paper_abstract_status`, and `paper_verdict_with_abstract`. Never change the schema or any other table.
- **A paper lands in exactly one verdict table, never both.** If you find an abstract, the paper goes to `paper_verdict_with_abstract` (undecided) and you write no verdict here. Otherwise it goes to `paper_verdict_without_abstract` with your decision.
- **Never repeat a source.** Before searching, read the paper's rows in `paper_abstract_miss` and never query a `source` already listed there. Record each new source you try as one row with `lookup_by = 'web'` and `source` = the page's bare domain.
- Never guess or generate an abstract. With less evidence, you may still make a relevance decision from metadata (see step 5).

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

## Work queue

```sh
sqlite3 -readonly archive/paperhunter.db \
  "SELECT q.paper_id, round(q.similarity,3), q.miss_count
   FROM paper_lookup_queue q
   WHERE q.paper_id NOT IN (SELECT paper_id FROM paper_verdict_without_abstract)
   LIMIT 20;"
```

Process **20** per session, in the order returned. Resume by running this again: a paper you decide leaves the set (it is now in a verdict table), and a paper whose abstract you find leaves the queue itself.

## For each paper

### 1. Gather the database evidence

```sh
sqlite3 -readonly archive/paperhunter.db \
  "SELECT paper_id, dblp_key, title, authors, year, venue, doi, arxiv_id, ee, url
   FROM paper_dblp WHERE paper_id = <ID>;"

sqlite3 -readonly archive/paperhunter.db \
  "SELECT source, lookup_by, response FROM paper_abstract_miss WHERE paper_id = <ID>;"
```

The miss `response` text often holds keywords, topics, or a TL;DR even when no abstract was found; mine it for evidence. Note the `source` domains so you do not query them again.

### 2. Search online for more information

Use the full-text PDF, the publisher page, citing papers, the project repository, and author pages. Do not retry any source already in `paper_abstract_miss` for this paper.

### 3. If a genuine abstract is found

Handle it exactly as `g_lookup.md` does, then stop working on the paper (its final decision is left to `h_classify.md`):

- Verify the page is this exact paper and the text is its own abstract, copied verbatim.
- In **one transaction**: insert the hit into `paper_abstract_hit` (`lookup_by = 'web'`, `source` = domain), insert the abstract into `paper_abstract` (`source` = page URL), update `paper_abstract_status` (`hit_count = hit_count + 1`, `has_abstract = 1`, plus `miss_count` for any sources that missed), and insert the paper into `paper_verdict_with_abstract` with its default values (no verdict).

```sh
printf '%s' "$ABSTRACT_TEXT" > /tmp/abs.txt
printf '%s' "$HIT_EXCERPT"   > /tmp/hit.txt

sqlite3 archive/paperhunter.db <<SQL
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
INSERT INTO paper_abstract_hit (paper_id, source, lookup_by, request, response)
VALUES (<ID>, 'arxiv.org', 'web', 'https://arxiv.org/abs/...', readfile('/tmp/hit.txt'));
INSERT OR IGNORE INTO paper_abstract (paper_id, abstract, source)
VALUES (<ID>, readfile('/tmp/abs.txt'), 'https://arxiv.org/abs/...');
UPDATE paper_abstract_status SET hit_count = hit_count + 1, has_abstract = 1 WHERE paper_id = <ID>;
INSERT OR IGNORE INTO paper_verdict_with_abstract (paper_id) VALUES (<ID>);
COMMIT;
SQL
```

Do **not** insert this paper into `paper_verdict_without_abstract`. Move on.

### 4. Otherwise, combine everything

Merge the title, authors, venue, year, DOI/arXiv identifiers, and any keywords, topics, or TL;DR found in the miss responses or online.

### 5. Decide from the available evidence

Apply the relevance definition and decide accept or reject. You have less evidence than a paper with an abstract, so **lean toward accepting borderline papers** here: a false positive is acceptable at this stage and is removed later, a false negative is not. When you accept on thin evidence, say so in `reason`.

### 6. Classify

If accepted, assign exactly one category using the table and the tie-break.

### 7. Record the decision

Log the sources you tried as misses (so they are never retried), then write the verdict, in one transaction:

```sh
printf '%s' "$MISS_EXCERPT" > /tmp/miss.txt
printf '%s' "$REASON_TEXT"  > /tmp/reason.txt

sqlite3 archive/paperhunter.db <<SQL
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
INSERT INTO paper_abstract_miss (paper_id, source, lookup_by, request, response)
VALUES (<ID>, 'dl.acm.org', 'web', 'https://dl.acm.org/doi/...', readfile('/tmp/miss.txt'));
UPDATE paper_abstract_status SET miss_count = miss_count + 1 WHERE paper_id = <ID>;
INSERT INTO paper_verdict_without_abstract (paper_id, reviewed, verdict, reason)
VALUES (<ID>, 1, 'baseline', readfile('/tmp/reason.txt'));
COMMIT;
SQL
```

Use the paper's chosen value in place of `'baseline'`. Add one `paper_abstract_miss` row and one `miss_count` increment for each new source you tried; if you tried no new source, omit the miss insert and the count update and write only the verdict row.

## Finish

Re-read the queue and continue until no unreviewed papers remain. Accepted papers (verdict not `reject`) appear in the `paper_list` view.

**Afterwards:** run `h_classify.md` again for any papers this prompt moved into `paper_verdict_with_abstract`.
