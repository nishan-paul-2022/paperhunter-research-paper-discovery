# Paper Hunter: Rebuild Specification

## Scope

The `paper-hunter/` folder needs a major overhaul. **Nothing that exists today can be assumed correct or reusable.** This includes code, prompts, tables, views, and anything else related to Paper Hunter. All of the Python files, prompt files, tables, and views listed below must be created from scratch.

The database (`archive/paperhunter.db`) already contains old Paper Hunter objects, some with the same names as the new ones (`paper_dblp`, `paper_review`, `paper_list`) and others that are now obsolete (`paper_openalex`, `paper_s2`, `paper_candidate_ai`, `paper_summary`). `a_extract.py` drops all of them before it creates the new schema. No other tables are touched.

## Research topic

Several steps evaluate papers against this topic:

> **LLM-based agentic framework for autonomous black-box penetration testing over long-horizon attack chains**

## Global rules

These apply to every step.

1. **Resumable and idempotent.** Each step processes only the work that is still pending (for example, papers with no logged call, or rows where `*_review = 0`) and can be stopped and rerun at any point without duplicating rows, renumbering papers, or repeating paid calls.
2. **Stable IDs.** `paper_id` is assigned once per `dblp_key` and never changes across reruns. All other tables depend on it.
3. **Atomic writes.** A call log row, the abstract it produced, and the matching `paper_status` update are committed in the same transaction.
4. **No secrets in the database.** API keys are removed from every URL before it is logged.
5. **Provenance.** Every abstract and every AI decision records where it came from.

## Pipeline overview

The steps run in this order. The order differs from the first draft: screening now happens **before** the AI web search, so the expensive AI steps only see papers that might be relevant (see "Changes from the first draft").

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `a_extract.py` | Python | Extract papers from DBLP and initialize their status |
| 2 | `b_abstract.py` | Python | Fetch abstracts via the OpenAlex and Semantic Scholar APIs |
| 3 | `c_vector.py` | Python | Embed every paper (the abstract if one exists, otherwise the title and metadata) |
| 4 | `d_match.py` | Python | Screen every paper against the research topic, aiming for zero false negatives |
| 5 | `e_abstract.md` | AI prompt | Search the web for abstracts of screened-in papers the APIs missed |
| 6 | `f_search.md` | AI prompt | Accept or reject screened-in papers that have abstracts, and classify the accepted ones |
| 7 | `g_remaining.md` | AI prompt | Accept or reject screened-in papers that still have no abstract, and classify the accepted ones |

After `e_abstract.md` finds new abstracts, rerun `c_vector.py` and `d_match.py`. They are incremental, so they only re-embed and re-screen the papers that changed.

---

# Part A: Tables

## A.0 Existing input tables
These already exist and are only read by the pipeline.

| Object | Key used | Purpose |
|---|---|---|
| `conf_list` (view) | `dblp_key` (e.g. `conf/ccs`), plus `dblp_acronym`, `core_title`, `ranking` | Conference series whose papers should be extracted |
| `journal_list` (view) | `dblp_key` (e.g. `journals/csur`), plus `dblp_name`, `scopus_title`, `quartile` | Journal streams whose papers should be extracted |

Both views return **no rows** while `conf_review` or `journal_review` has pending rows. `a_extract.py` stops with an error if either list is empty, so it never runs on a half-finished venue list.

## A.1 `paper_dblp`
Every paper extracted from DBLP. A DBLP record has fields that can repeat (`author`, `ee`), so the schema is fixed, not "all fields as they come".

| Field | Description |
|---|---|
| `paper_id` | `INTEGER PRIMARY KEY`, auto-incrementing from 1, assigned once and kept stable |
| `dblp_key` | DBLP record key, e.g. `conf/ccs/LinL23`. `UNIQUE` (the natural key used for upserts) |
| `venue_key` | Series key derived from `dblp_key`, e.g. `conf/ccs`. Joins to `conf_list` / `journal_list` |
| `record_type` | `article` or `inproceedings` |
| `title` | Title, with XML markup removed |
| `authors` | JSON array of author names, in order |
| `year` | Publication year |
| `venue` | `booktitle` (conferences) or `journal` (journals) as written in DBLP |
| `volume`, `number`, `pages` | As in DBLP (nullable) |
| `doi` | Normalized DOI (lowercase, no `https://doi.org/` prefix), taken from `ee`. Nullable |
| `arxiv_id` | arXiv ID taken from `ee`, if present. Nullable |
| `ee` | JSON array of every electronic-edition URL |
| `url` | DBLP `url` field |
| `crossref` | DBLP `crossref` field (the parent proceedings) |
| `mdate` | DBLP modification date |

**Written by:** `a_extract.py`

## A.2 `paper_status`
Abstract-retrieval status of each paper.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `failure_count` | `0` | Number of rows in `paper_failure` for this paper |
| `success_count` | `0` | Number of rows in `paper_success` for this paper |
| `status` | `0` | `0` = no abstract yet; `1` = abstract stored in `paper_abstract` |

The counts are updated in the same transaction as the log insert (Global rule 3), so they always equal the log tables.

**Written by:** `a_extract.py` (inserts `paper_id` only); updated by `b_abstract.py`, `e_abstract.md`, `g_remaining.md`

## A.3 `paper_abstract`
At most one abstract per paper.

| Field | Description |
|---|---|
| `paper_id` | Primary key, references `paper_dblp.paper_id` |
| `abstract` | Clean abstract text (OpenAlex's inverted index is rebuilt into plain text, HTML/JATS tags removed) |
| `source` | Where it came from: `openalex`, `s2`, or the web page URL |

When more than one source returns an abstract, keep the first one stored. A later source never overwrites it.

**Written by:** `b_abstract.py`, `e_abstract.md`, `g_remaining.md`

## A.4 `paper_success` and `paper_failure`
A log of every call made for a paper, whether to an API or a website. Both tables have the same structure.

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `source` | Normalized source name: `openalex`, `s2`, or the website's domain (e.g. `dl.acm.org`). Used for the "never repeat a failed source" rule |
| `lookup` | What was looked up: `doi`, `arxiv`, `title`, or `web` |
| `api` | The full request URL or page, **with API keys removed** |
| `response` | This paper's part of the raw response, stored as received (empty if there was none) |
| `called_at` | Timestamp |

**Outcomes.** Each call is logged as exactly one of these:

| Outcome | Meaning | Logged in |
|---|---|---|
| Success | The response contains a genuine abstract for **this** paper (see the match check in C.2) | `paper_success` |
| Failure | The source answered properly but has no abstract for this paper (not found, abstract empty, or the title matched a different paper) | `paper_failure` |
| Transient error | HTTP 429, 5xx, timeout, or network error | **Not logged.** The call is retried with backoff. Otherwise one outage would mark the source as failed forever |

**Batch calls.** When one request covers many papers (C.2), each paper gets its own row with the shared `api` and its own part of the `response`.

**Written by:** `b_abstract.py`, `e_abstract.md`, `g_remaining.md`

## A.5 `paper_rule`
Semantic screening result. Covers **every** paper, with or without an abstract.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `basis` | — | `abstract` or `title`: which text was embedded |
| `embedded_hash` | — | Hash of the embedded text. When it changes (e.g. an abstract was found), the paper is re-embedded and re-screened |
| `rule_score` | `NULL` | Best similarity score across the topic queries |
| `rule_hit` | `NULL` | What let the paper through: `semantic`, `keyword`, or `both` |
| `rule_review` | `0` | `0` = not yet screened; `1` = screened |
| `rule_candidate` | `NULL` | `NULL` = undecided; `1` = accepted; `0` = rejected |

**Written by:** `c_vector.py` (inserts or resets rows); updated by `d_match.py`

## A.6 `paper_ai`
Final AI decision on screened-in papers that **have** an abstract.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `ai_review` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `ai_candidate` | `NULL` | `NULL` = undecided; otherwise one of the A.8 values |
| `ai_reason` | `NULL` | One or two sentences explaining the decision, citing the evidence used |

**Written by:** `d_match.py` (inserts accepted papers that have an abstract), `g_remaining.md` (moves in papers whose abstract it finds); updated by `f_search.md`

## A.7 `paper_probably`
Final AI decision on screened-in papers that have **no** abstract. Same structure as `paper_ai`.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `ai_review` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `ai_candidate` | `NULL` | `NULL` = undecided; otherwise one of the A.8 values |
| `ai_reason` | `NULL` | One or two sentences explaining the decision, citing the sources used |

A paper is never in both `paper_ai` and `paper_probably`.

**Written by:** `g_remaining.md`

## A.8 Paper categories
The values of `ai_candidate`. A paper is **relevant** if it would belong in the related-work or evaluation section of a paper on the research topic: it is about LLM- or AI-agent-driven offensive security (penetration testing, red teaming, CTF solving, exploit generation, attack planning, or automated attack chains), or it is a benchmark or environment that such agents are evaluated on.

| Value | Meaning |
|---|---|
| `baseline` | Proposes a system, framework, or method for automated or LLM-driven offensive security that the research could compare against |
| `benchmark` | Contributes a benchmark, dataset, CTF suite, cyber range, or evaluation environment for offensive-security agents |
| `survey` | Survey, SoK, or systematic review covering the topic or a direct sub-area |
| `evaluation` | Empirical study that measures existing LLMs or agents on offensive-security tasks, without proposing a new system or benchmark |
| `reject` | Not relevant to the research topic |

**Tie-break.** If a paper fits more than one category, use the first match in this order: `benchmark` > `survey` > `baseline` > `evaluation`. (For example, a paper that proposes an agent and also releases a new benchmark is `benchmark`.)

---

# Part B: Views

## B.1 `paper_review`
Screened-in papers that still have no abstract, in priority order. This is the work queue for `e_abstract.md` and `g_remaining.md`.

| Field | Description |
|---|---|
| `paper_id` | Paper ID |
| `rule_score` | Screening score |
| `failure_count` | Number of failed calls |

- **Built on:** `paper_status` joined to `paper_rule`
- **Filter:** `status = 0` and `rule_candidate = 1`
- **Sort:** `rule_score` descending (most likely relevant first), then `failure_count` ascending, then `paper_id`

**Read by:** `e_abstract.md`, `g_remaining.md`

## B.2 `paper_list`
The final list of accepted papers.

| Field | Description |
|---|---|
| *(all `paper_dblp` columns)* | Every column from `paper_dblp` |
| `category` | `baseline`, `survey`, `benchmark`, or `evaluation` |
| `ai_reason` | The reason recorded by the AI |
| `basis` | `abstract` (from `paper_ai`) or `no_abstract` (from `paper_probably`) |
| `venue_name` | `core_title` or `scopus_title` |
| `venue_rank` | CORE `ranking` (conferences) or Scopus `quartile` (journals) |

- **Built on:** `paper_ai` UNION ALL `paper_probably`, joined to `paper_dblp`, then LEFT JOINed to `conf_list` and `journal_list` on `venue_key = dblp_key`. Use LEFT JOIN so a pending venue review, which empties those views, can only blank the venue columns and never removes papers.
- **Filter:** `ai_review = 1` and `ai_candidate` not `reject` and not `NULL`

---

# Part C: Files

## C.1 `a_extract.py`: Extract papers from DBLP

**Reads:** `archive/dblp.xml`, `conf_list`, `journal_list`
**Writes:** `paper_dblp`, `paper_status`

1. **Prepare.** Drop the old Paper Hunter objects listed under Scope. Create every table and view in Parts A and B if it does not exist. Stop if `conf_list` or `journal_list` is empty.
2. **Stream.** Parse `archive/dblp.xml` (5.4 GB) as a stream, using the DTD for entities. Never load the whole file into memory.
3. **Keep a record only if all of these are true:**
   - It is an `article` or `inproceedings` record.
   - Its `publtype` is not `informal`, `withdrawn`, or `data`.
   - Its `year` is in `YEARS` (a constant at the top of the file; default **2023–2026**, see Open decisions).
   - Its series key matches a `dblp_key` in `conf_list` or `journal_list`. The series key is the `dblp_key` up to its second `/` (e.g. `conf/ccs/LinL23` → `conf/ccs`). Match on this key, **not** on the venue name text.
4. **Upsert** each kept record into `paper_dblp` by `dblp_key`: insert new records, update changed fields of existing ones, and keep `paper_id` unchanged.
5. **Initialize status.** Insert each new `paper_id` into `paper_status`. All other fields take their defaults.
6. **Report.** Print the number of papers per year and per venue type, and the number of papers without a DOI.

Note: DBLP files some co-located workshop papers under the parent conference key (e.g. `AISec@CCS` under `conf/ccs`). They are kept. This only adds false positives, which later steps remove.

---

## C.2 `b_abstract.py`: Fetch abstracts via APIs

**Reads:** `paper_dblp`, `paper_status`, `paper_success`, `paper_failure`, `.env`
**Writes:** `paper_abstract`, `paper_success`, `paper_failure`, `paper_status`

### Behavior
For every paper with `status = 0`, fetch the abstract, skipping any (source, lookup) pair already logged for that paper.

### Lookup order (per source)
1. **DOI**, if the paper has one. Use batch requests: OpenAlex `filter=doi:a|b|…` (up to 50 DOIs per request) and Semantic Scholar `POST /paper/batch` (up to 500 IDs per request).
2. **arXiv ID**, if the paper has one and the DOI lookup did not find the work.
3. **Title**, only if the source did **not find the work at all** by DOI or arXiv ID. If the source found the work but it has no abstract, a title search would return the same record, so the call is skipped.

### Source scheduling
OpenAlex and Semantic Scholar run **at the same time** as two independent workers, each limited by its own rate limit. To keep API calls down without losing yield:
- OpenAlex processes every pending paper.
- Semantic Scholar processes only papers that OpenAlex has finished without finding an abstract. This way S2 credits are not spent on papers that already have one, and S2 still covers everything OpenAlex missed.

### Title-match check
Title lookups can return a different paper. Accept a title result only if **all** of these hold:
- the normalized titles match (lowercase, punctuation removed) with a similarity of at least 0.95,
- the year is within ±1, and
- at least one author surname is shared.

A title result that fails this check is logged as a **failure** for that paper.

### What counts as an abstract
The text must be non-empty after cleanup and must not be a placeholder (e.g. "No abstract available", or the title repeated). Otherwise the call is a failure.

### Efficiency and limits
- **Maximum yield, minimum time, minimum calls:** achieved by batching, the schedule above, and the skip rules.
- **Respect limits:** follow each API's documented rate limit and daily quota, honor `Retry-After`, and use exponential backoff for transient errors (A.4). Load keys from `.env`.
- **Checkpointing:** commit after every batch so a crash loses at most one batch.

### Table updates

| Table | Update |
|---|---|
| `paper_abstract` | Insert the abstract and `source` if the paper has none yet |
| `paper_success` | One row per paper per successful call |
| `paper_failure` | One row per paper per failed call |
| `paper_status` | Update counts in the same transaction; set `status = 1` when an abstract is stored |

---

## C.3 `c_vector.py`: Embed every paper

**Reads:** `paper_dblp`, `paper_abstract`, `paper_rule`
**Writes:** vector store, `paper_rule`

1. **Model.** Use an open-source embedding model that runs locally (e.g. `BAAI/bge-large-en-v1.5`). Store the model name and vector size with the vector store. The model must not change between `c_vector.py` and `d_match.py`.
2. **Text to embed** (clean text only, no raw API JSON):
   - With an abstract (`basis = abstract`): title + abstract.
   - Without an abstract (`basis = title`): title + venue + year.
   - Truncate to the model's maximum input length.
3. **Incremental.** For each paper, hash the text. Embed it only if the paper has no `paper_rule` row or its `embedded_hash` has changed. When the hash changes, reset `rule_review = 0` and `rule_candidate = NULL` so `d_match.py` screens it again.
4. **Store.** Save one vector per `paper_id` (overwriting on re-embed). A flat file (e.g. NumPy/FAISS flat index) or `sqlite-vec` is enough. `d_match.py` scores every vector exactly, so no approximate index is needed.
5. **Initialize screening.** Insert or update the paper's `paper_rule` row with `basis` and `embedded_hash`.

---

## C.4 `d_match.py`: Screen papers by research topic

**Reads:** vector store, `paper_dblp`, `paper_abstract`, `paper_rule`
**Writes:** `paper_rule`, `paper_ai`

### Method
Screen every paper with `rule_review = 0`. A paper is **accepted** if **either** test passes:

1. **Semantic test.** Compute exact cosine similarity between the paper's vector and **each** of a fixed set of topic queries, and take the maximum. The queries cover the topic and its direct neighbors, because benchmark, survey, and evaluation papers count as relevant (A.8). For example: the research topic itself; LLM agents for penetration testing; automated red teaming with LLMs; LLM agents solving CTF challenges; LLM-driven exploit generation; AI attack planning and attack graphs; autonomous cyber-operations agents; benchmarks for offensive-security agents. Never use a top-k search, because it silently drops papers past k.
2. **Keyword test.** Case-insensitive match on the title, plus the abstract if one exists, for terms such as: penetration test, pentest, red team, CTF, capture the flag, exploit, attack chain, attack graph, privilege escalation, lateral movement, Metasploit, offensive security, cyber range, kill chain.

### Threshold (how "no false negatives" is enforced)
- Keep a **seed set** of at least 30 known-relevant papers covering all four categories (file `seed_papers.txt`, one DBLP key or DOI per line).
- Set a separate threshold for each `basis`. Each threshold is the **lowest** score any seed paper gets under that basis, minus a safety margin (default 0.05). Score the seed papers under the `title` basis too, so the title threshold is calibrated on its own.
- Stop with an error if any seed paper is not accepted.
- Print the number of papers accepted for each basis, so the margin can be tuned.

### Table updates

| Event | Update |
|---|---|
| Paper screened | `paper_rule`: set `rule_review = 1`, `rule_score`, `rule_hit` |
| Accepted, has abstract | `rule_candidate = 1`; insert into `paper_ai` (defaults) if it is not already there |
| Accepted, no abstract | `rule_candidate = 1`. It now appears in `paper_review` |
| Rejected | `rule_candidate = 0`. If the paper is in `paper_ai` with `ai_review = 0` (from an earlier screen), remove it |

---

## C.5 `e_abstract.md`: AI prompt for an online abstract search

**Reads:** `paper_review`, `paper_dblp`, `paper_failure`
**Writes:** `paper_abstract`, `paper_success`, `paper_failure`, `paper_status`

### Purpose
The APIs could not find abstracts for some screened-in papers (listed in `paper_review`). This prompt directs an AI to search the web for those abstracts. The prompt must say how the AI writes to the database (the `sqlite3` CLI on `archive/paperhunter.db`, one transaction per paper).

### Workflow
For each paper, in `paper_review` order, in batches of a set size (e.g. 20 per session):

1. **Get the details.** Fetch the paper's full record from `paper_dblp`.
2. **Check past failures.** Read `paper_failure` for this paper. Do not query any `source` already logged there.
3. **Search online.** Use every available detail: DOI landing page, arXiv ID, publisher page, title, authors, venue, year.
4. **Verify.** Confirm the page is about this paper (title, authors, and venue or year match) and that the text is the paper's own abstract, not a summary, a citing paper's text, or a generated description.
5. **Log each source.** One row per source tried, with `source` = the domain, `lookup = web`, and the relevant page text in `response`:
   - Verified abstract found → `paper_success`.
   - Not found → `paper_failure`.
6. **Update the results.** If a verified abstract was found, insert it into `paper_abstract` (`source` = page URL) and update `paper_status`, all in one transaction. Otherwise only `paper_status.failure_count` changes.

### Rules
- **Accuracy:** record an abstract only if it was actually found and verified. Never guess, paraphrase, or generate one.
- **No repeats:** never query a source already in `paper_failure` for the same paper.
- **Afterwards:** rerun `c_vector.py` and `d_match.py` so the new abstracts are re-embedded and re-screened.

---

## C.6 `f_search.md`: AI prompt to classify screened papers with abstracts

**Reads:** `paper_ai`, `paper_dblp`, `paper_abstract`
**Writes:** `paper_ai`

### Purpose
Make the final decision on each screened-in paper that has an abstract, and categorize the accepted ones. (The text is read from `paper_dblp` and `paper_abstract`, not the vector store, which holds only vectors.)

### Workflow
For each row in `paper_ai` with `ai_review = 0`, in batches:

1. **Get the data.** Title, authors, venue, year, and abstract.
2. **Decide.** Apply the relevance definition in A.8 and decide whether to **accept** or **reject** the paper. If it is unclear from the abstract, the AI may check the paper online before deciding.
3. **Classify.** If accepted, assign exactly one category using A.8 and its tie-break.
4. **Record.** Set `ai_review = 1`, `ai_candidate` = the category or `reject`, and `ai_reason`.

---

## C.7 `g_remaining.md`: AI prompt to classify screened papers without abstracts

**Reads:** `paper_review`, `paper_dblp`, `paper_failure`, `paper_probably`
**Writes:** `paper_probably`, and in the abstract-found case: `paper_abstract`, `paper_success`, `paper_failure`, `paper_status`, `paper_ai`

### Purpose
Decide on screened-in papers whose abstracts were never found, using every other piece of available information. Run this only after `e_abstract.md` has worked through its queue.

### Workflow
For each paper in `paper_review` that is not already in `paper_probably`, in batches:

1. **Gather database details.** Combine the paper's records from `paper_dblp` and `paper_failure` (the failure responses often contain keywords, topics, or a TL;DR even without an abstract).
2. **Search online.** Look for more information: the full-text PDF, the publisher page, citing papers, the project repository, and author pages. Do not retry sources already in `paper_failure` for the abstract.
3. **If a genuine abstract is found**, handle it the way `e_abstract.md` does (verify, log, store it, update `paper_status`), insert the paper into `paper_ai` with defaults, and move on. `f_search.md` will decide on it.
4. **Combine everything.** Merge title, authors, venue, year, keywords, topics, TL;DR, and anything else found.
5. **Decide.** Apply A.8 and decide whether to **accept** or **reject** the paper. With less evidence, lean toward accepting borderline papers (false positives are acceptable here) and say so in `ai_reason`.
6. **Classify.** If accepted, assign exactly one category using A.8 and its tie-break.
7. **Record.** Insert into `paper_probably` with `ai_review = 1`, `ai_candidate`, and `ai_reason`.

---

# Changes from the first draft

| # | Gap in the first draft | Fix |
|---|---|---|
| 1 | No year limit. All years for 865 conferences and 1,062 journals would be millions of papers (2023–2026 alone is about 1.06M) | `YEARS` constant in `a_extract.py`, default 2023–2026 |
| 2 | "Venue appears in `conf_list`" was undefined. Those lists hold DBLP series keys, not venue names | Match the series prefix of `dblp_key` against `dblp_key`; keep only `article`/`inproceedings`; drop informal/withdrawn |
| 3 | "All DBLP fields" can't be columns (authors and `ee` repeat), and a fresh `paper_id` on every run would break every foreign key | Fixed schema; upsert by `dblp_key`; stable `paper_id` |
| 4 | No pending-work rules, so a rerun would repeat paid API and AI calls | Global rule 1 and a per-step pending filter |
| 5 | Rate-limit and timeout errors counted as failures, and the "never retry a failed source" rule would then block that source forever | Transient outcome: not logged, retried with backoff |
| 6 | "Skip sources already in `paper_failure`" compared full URLs, which never repeat exactly | `source` and `lookup` columns |
| 7 | OpenAlex API keys would be stored in `api` URLs | Keys removed before logging |
| 8 | Title fallback could store another paper's abstract | Title/year/author match check |
| 9 | Title fallback also ran when the DOI lookup found the work with no abstract, which wastes a call | Title lookup only when the work was not found |
| 10 | "Both APIs at once" conflicted with "minimum API calls", and one call per paper is far too slow for about 1M papers | Batch endpoints; S2 handles only OpenAlex misses; both workers run at the same time |
| 11 | A batch call covers many papers, but the logs are one row per paper | One row per paper with that paper's part of the response |
| 12 | Which abstract to keep when both sources return one was undefined | First stored wins; `source` recorded |
| 13 | **Papers without abstracts were never screened.** `c_abstract` (AI web search) and `g_remaining` (AI decision) would run on every such paper. At a 7–15% miss rate that is roughly 75k–160k AI web sessions | Embed and screen every paper (title basis when there is no abstract); AI steps see only screened-in papers; steps reordered and renamed (`c_vector`, `d_match`, `e_abstract`) |
| 14 | `paper_review` priority by fewest failures put papers that were barely tried first and ignored relevance | Sort by `rule_score` first |
| 15 | Embedding raw API JSON adds noise and makes papers with and without responses incomparable | Embed clean title + abstract only |
| 16 | "No false negatives" had no method or check, and a single topic sentence is narrower than the four accepted categories | Multiple topic queries + keyword OR rule + seed-set-calibrated thresholds for each basis + hard stop if a seed paper is missed |
| 17 | A top-k vector search silently drops papers past k | Exact scoring of every vector |
| 18 | `f_search` read text from the vector store, which holds only vectors | Read from `paper_dblp` + `paper_abstract` |
| 19 | The categories were tautological ("Baseline paper") and there was no relevance definition or tie-break | Definitions, relevance scope, and tie-break order in A.8 |
| 20 | `g_remaining` searched online but discarded any abstract it found and logged nothing | Found abstracts follow the `e_abstract` path and the paper moves to `paper_ai` |
| 21 | AI decisions could not be audited | `ai_reason` column |
| 22 | `paper_list` INNER JOINed views that empty out during a venue review, which would silently empty the final list | LEFT JOIN; venue name and rank added |
| 23 | New table names collide with old objects (`paper_dblp`, `paper_review`, `paper_list`) | `a_extract.py` drops the old Paper Hunter objects first |
| 24 | The AI prompts did not say how they write to the database | `sqlite3` CLI, one transaction per paper |

# Open decisions

These have working defaults in the plan. Change them if needed.

1. **Year window:** 2023–2026 (the same as the old extractor). Older pentest-automation work (pre-LLM planners, attack-graph tools) would need an earlier start year.
2. **Series lineage:** predecessor series keys (`conf_dblp_lineage`, `journal_dblp_lineage`) are **not** matched. With a 2023+ window this rarely matters. If the window is widened, include predecessor keys of listed series.
3. **Seed set:** `seed_papers.txt` needs at least 30 known-relevant papers before `d_match.py` can run.
4. **Embedding model:** `bge-large-en-v1.5` is a suggestion. Any local model works if `c_vector.py` and `d_match.py` use the same one.
