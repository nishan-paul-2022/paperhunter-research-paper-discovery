# Paper Hunter: Rebuild Specification

## Scope

The `paper-hunter/` folder needs a major overhaul. **Nothing that exists today can be assumed correct or reusable.** This includes code, prompts, tables, views, and anything else related to Paper Hunter. All of the Python files, prompt files, tables, and views listed below must be created from scratch.

The database (`archive/paperhunter.db`) already contains old Paper Hunter objects: `paper_dblp`, `paper_review`, `paper_list`, `paper_openalex`, `paper_s2`, `paper_candidate_ai`, `paper_summary`. `a_extract.py` drops all of them before it creates the new schema. No other tables are touched.

## Research topic

Several steps evaluate papers against this topic:

> **LLM-based agentic framework for autonomous black-box penetration testing over long-horizon attack chains**

## Global rules

These apply to every step.

1. **Resumable and idempotent.** Each step processes only the work that is still pending (for example, papers with no logged call, or rows where `reviewed = 0`) and can be stopped and rerun at any point without duplicating rows, renumbering papers, or repeating paid calls.
2. **Stable IDs.** `paper_id` is assigned once per `dblp_key` and never changes across reruns. All other tables depend on it.
3. **Atomic writes.** A call-log row, the abstract it produced, and the matching `paper_abstract_status` update are committed in the same transaction.
4. **No secrets in the database.** API keys are removed from every URL before it is logged.
5. **Provenance.** Every abstract and every AI decision records where it came from.

## Pipeline overview

The steps run in this order. Screening happens **before** the AI web search, so the expensive AI steps only see papers that might be relevant.

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `a_extract.py` | Python | Extract papers from DBLP for the listed venues and a given year range |
| 2 | `b_fetch.py` | Python | Fetch abstracts from the OpenAlex and Semantic Scholar APIs |
| 3 | `c_embed.py` | Python | Embed every paper (the abstract if one exists, otherwise the title and metadata) |
| 4 | `d_screen.py` | Python | Screen every paper against the research topic, aiming for zero false negatives |
| 5 | `e_lookup.md` | AI prompt | Search the web for abstracts of screened-in papers the APIs missed |
| 6 | `f_classify.md` | AI prompt | Accept or reject screened-in papers that have an abstract, and classify the accepted ones |
| 7 | `g_infer.md` | AI prompt | Accept or reject screened-in papers that still have no abstract, using all other evidence, and classify the accepted ones |

After `e_lookup.md` finds new abstracts, rerun `c_embed.py` and `d_screen.py`. They are incremental, so they only re-embed and re-screen the papers that changed.

---

# Part A: Tables

## A.0 Existing input tables
These already exist and are only read by the pipeline.

| Object | Key used | Purpose |
|---|---|---|
| `conf_list` (view) | `dblp_key` (e.g. `conf/ccs`), plus `dblp_acronym`, `core_title`, `ranking` | Conference series whose papers should be extracted |
| `journal_list` (view) | `dblp_key` (e.g. `journals/csur`), plus `dblp_name`, `scopus_title`, `quartile` | Journal series whose papers should be extracted |

Both views return **no rows** while `conf_review` or `journal_review` has pending rows. `a_extract.py` stops with an error if either list is empty, so it never runs on a half-finished venue list.

## A.1 `paper_dblp`
Every paper extracted from DBLP. A DBLP record has fields that can repeat (`author`, `ee`), so the schema is fixed, not "all fields as they come".

| Field | Description |
|---|---|
| `paper_id` | `INTEGER PRIMARY KEY`, auto-incrementing from 1, assigned once and kept stable |
| `dblp_key` | DBLP record key, e.g. `conf/ccs/LinL23`. `UNIQUE` (the natural key used for upserts) |
| `venue_key` | The venue key the paper matched, e.g. `conf/ccs`. Joins to `conf_list.dblp_key` / `journal_list.dblp_key` |
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

## A.2 `paper_abstract_status`
Whether each paper has an abstract yet, and how many lookups have been made for it.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `has_abstract` | `0` | `0` = no abstract yet; `1` = abstract stored in `paper_abstract` |
| `hit_count` | `0` | Number of rows in `paper_abstract_hit` for this paper |
| `miss_count` | `0` | Number of rows in `paper_abstract_miss` for this paper |

The counts are updated in the same transaction as the log insert (Global rule 3), so they always equal the log tables.

**Written by:** `a_extract.py` (inserts `paper_id` only); updated by `b_fetch.py`, `e_lookup.md`, `g_infer.md`

## A.3 `paper_abstract`
At most one abstract per paper.

| Field | Description |
|---|---|
| `paper_id` | Primary key, references `paper_dblp.paper_id` |
| `abstract` | Clean abstract text (OpenAlex's inverted index is rebuilt into plain text, HTML/JATS tags removed) |
| `source` | Where it came from: `openalex`, `s2`, or the web page URL |

When more than one source returns an abstract, keep the first one stored. A later source never overwrites it.

**Written by:** `b_fetch.py`, `e_lookup.md`, `g_infer.md`

## A.4 `paper_abstract_hit` and `paper_abstract_miss`
A log of every abstract lookup made for a paper, whether to an API or a website. `paper_abstract_hit` holds lookups that returned the abstract; `paper_abstract_miss` holds lookups that did not. Both tables have the same structure.

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `source` | Normalized source name: `openalex`, `s2`, or the website's domain (e.g. `dl.acm.org`). Used for the "never repeat a missed source" rule |
| `lookup_by` | What the lookup used: `doi`, `arxiv`, `title`, or `web` |
| `request` | The full request URL or page, **with API keys removed** |
| `response` | This paper's part of the raw response, stored as received (empty if there was none) |
| `called_at` | Timestamp |

**Outcomes.** Each lookup ends in exactly one of these:

| Outcome | Meaning | Logged in |
|---|---|---|
| Hit | The response contains a genuine abstract for **this** paper (see the match check in C.2) | `paper_abstract_hit` |
| Miss | The source answered properly but has no abstract for this paper (not found, abstract empty, or the title matched a different paper) | `paper_abstract_miss` |
| Transient error | HTTP 429, 5xx, timeout, or network error | **Not logged.** The call is retried with backoff. Otherwise one outage would mark the source as missed forever |

**Batch calls.** When one request covers many papers (C.2), each paper gets its own row with the shared `request` and its own part of the `response`.

**Written by:** `b_fetch.py`, `e_lookup.md`, `g_infer.md`

## A.5 `paper_seed`
Papers known to be relevant, used to calibrate the screening threshold (C.4). Filled in by hand; no pipeline file writes to it.

| Field | Description |
|---|---|
| `paper_id` | Primary key, references `paper_dblp.paper_id` |
| `category` | Expected category (A.10): `baseline`, `benchmark`, `survey`, or `evaluation` |
| `evidence` | Why it is known to be relevant (e.g. a citing survey or a URL) |

**Choosing seeds.** Pick papers from outside the pipeline (own knowledge, survey/SoK reference lists, benchmark leaderboards), not by embedding score, since the seeds test that score. Cover every category, and include papers whose titles lack obvious keywords. A seed must already be in `paper_dblp`.

**Written by:** hand

## A.6 `paper_screen`
Semantic screening result. Covers **every** paper, with or without an abstract.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `embed_basis` | — | `abstract` or `title`: which text was embedded |
| `embed_hash` | — | Hash of the embedded text. When it changes (e.g. an abstract was found), the paper is re-embedded and re-screened |
| `similarity` | `NULL` | Best cosine similarity across the topic queries |
| `passed_by` | `NULL` | Which test let the paper through: `semantic`, `keyword`, or `both`. `NULL` if rejected |
| `screened` | `0` | `0` = not yet screened; `1` = screened |
| `passed` | `NULL` | `NULL` = not yet screened; `1` = passed; `0` = rejected |

**Written by:** `c_embed.py` (inserts or resets rows); updated by `d_screen.py`

## A.7 `paper_verdict_with_abstract`
Final AI decision on screened-in papers that **have** an abstract.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `reviewed` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `verdict` | `NULL` | `NULL` = undecided; otherwise one of the A.10 values |
| `reason` | `NULL` | One or two sentences explaining the decision, citing the evidence used |

**Written by:** `d_screen.py` (inserts papers that passed and have an abstract), `g_infer.md` (moves in papers whose abstract it finds); updated by `f_classify.md`

## A.8 `paper_verdict_without_abstract`
Final AI decision on screened-in papers that have **no** abstract. Same structure as `paper_verdict_with_abstract`.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | Primary key, references `paper_dblp.paper_id` |
| `reviewed` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `verdict` | `NULL` | `NULL` = undecided; otherwise one of the A.10 values |
| `reason` | `NULL` | One or two sentences explaining the decision, citing the sources used |

A paper is never in both verdict tables.

**Written by:** `g_infer.md`

## A.9 `paper_embedding` (vector store)
One vector per `paper_id`, stored outside SQLite at `archive/paper_embedding.*` (e.g. a NumPy/FAISS flat index) or in SQLite with `sqlite-vec`. The embedding model name and vector size are stored with it.

**Written by:** `c_embed.py`

## A.10 Paper categories
The values of `verdict`. A paper is **relevant** if it would belong in the related-work or evaluation section of a paper on the research topic: it is about LLM- or AI-agent-driven offensive security (penetration testing, red teaming, CTF solving, exploit generation, attack planning, or automated attack chains), or it is a benchmark or environment that such agents are evaluated on.

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

## B.1 `paper_lookup_queue`
Screened-in papers that still have no abstract, in priority order. This is the work queue for `e_lookup.md` and `g_infer.md`.

| Field | Description |
|---|---|
| `paper_id` | Paper ID |
| `similarity` | Screening score |
| `miss_count` | Number of missed lookups so far |

- **Built on:** `paper_abstract_status` joined to `paper_screen`
- **Filter:** `has_abstract = 0` and `passed = 1`
- **Sort:** `similarity` descending (most likely relevant first), then `miss_count` ascending, then `paper_id`

**Read by:** `e_lookup.md`, `g_infer.md`

## B.2 `paper_list`
The final list of accepted papers. (Named like `conf_list` and `journal_list`, the final outputs of the other hunters.)

| Field | Description |
|---|---|
| *(all `paper_dblp` columns)* | Every column from `paper_dblp` |
| `category` | `baseline`, `survey`, `benchmark`, or `evaluation` |
| `reason` | The reason recorded by the AI |
| `decided_from` | `abstract` (from `paper_verdict_with_abstract`) or `metadata` (from `paper_verdict_without_abstract`) |
| `venue_name` | `core_title` or `scopus_title` |
| `venue_rank` | CORE `ranking` (conferences) or Scopus `quartile` (journals) |

- **Built on:** `paper_verdict_with_abstract` UNION ALL `paper_verdict_without_abstract`, joined to `paper_dblp`, then LEFT JOINed to `conf_list` and `journal_list` on `venue_key = dblp_key`. Use LEFT JOIN so a pending venue review, which empties those views, can only blank the venue columns and never removes papers.
- **Filter:** `reviewed = 1` and `verdict` not `reject` and not `NULL`

---

# Part C: Files

## C.1 `a_extract.py`: Extract papers from DBLP

**Reads:** `archive/dblp.xml`, `conf_list`, `journal_list`
**Writes:** `paper_dblp`, `paper_abstract_status`

1. **Prepare.** Drop the old Paper Hunter objects listed under Scope. Create every table and view in Parts A and B if it does not exist. Stop if `conf_list` or `journal_list` is empty.
2. **Year range.** The year range is passed on the command line, e.g. `python a_extract.py --from-year 2023 --to-year 2026`. Both arguments are required, and nothing about years is hard-coded.
3. **Venue keys.** The accepted venue keys are exactly the `dblp_key` values in `conf_list` and `journal_list`. Nothing else is added.
4. **Stream.** Parse `archive/dblp.xml` (5.4 GB) as a stream, using the DTD for entities. Never load the whole file into memory.
5. **Keep a record only if all of these are true:**
   - It is an `article` or `inproceedings` record. This drops `proceedings` records, which describe a whole volume (e.g. `conf/ccs/2024`, the CCS 2024 book), not a paper.
   - Its `publtype` is not `withdrawn`. Records marked `software` or `data` are kept: they are real articles in listed journals such as SoftwareX and Data.
   - Its `year` is within the command-line range.
   - Its venue key is one of the keys from step 3. The venue key is the `dblp_key` up to its second `/` (e.g. `conf/ccs/LinL23` → `conf/ccs`). Match on this key, **not** on the venue name text.
6. **Upsert** each kept record into `paper_dblp` by `dblp_key`: insert new records, update changed fields of existing ones, and keep `paper_id` unchanged.
7. **Initialize status.** Insert each new `paper_id` into `paper_abstract_status`. All other fields take their defaults.
8. **Report.** Print the number of papers per year and per venue type, and the number of papers without a DOI.

Note: DBLP files some co-located workshop papers under the parent conference key (e.g. `AISec@CCS` under `conf/ccs`). They are kept. This only adds false positives, which later steps remove.

---

## C.2 `b_fetch.py`: Fetch abstracts from APIs

**Reads:** `paper_dblp`, `paper_abstract_status`, `paper_abstract_hit`, `paper_abstract_miss`, `.env`
**Writes:** `paper_abstract`, `paper_abstract_hit`, `paper_abstract_miss`, `paper_abstract_status`

### Behavior
For every paper with `has_abstract = 0`, fetch the abstract, skipping any (`source`, `lookup_by`) pair already logged for that paper.

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

A title result that fails this check is logged as a **miss** for that paper.

### What counts as an abstract
The text must be non-empty after cleanup and must not be a placeholder (e.g. "No abstract available", or the title repeated). Otherwise the lookup is a miss.

### Efficiency and limits
- **Maximum yield, minimum time, minimum calls:** achieved by batching, the schedule above, and the skip rules.
- **Respect limits:** follow each API's documented rate limit and daily quota, honor `Retry-After`, and use exponential backoff for transient errors (A.4). Load keys from `.env`.
- **Checkpointing:** commit after every batch so a crash loses at most one batch.

### Table updates

| Table | Update |
|---|---|
| `paper_abstract` | Insert the abstract and `source` if the paper has none yet |
| `paper_abstract_hit` | One row per paper per lookup that returned the abstract |
| `paper_abstract_miss` | One row per paper per lookup that did not |
| `paper_abstract_status` | Update counts in the same transaction; set `has_abstract = 1` when an abstract is stored |

---

## C.3 `c_embed.py`: Embed every paper

**Reads:** `paper_dblp`, `paper_abstract`, `paper_screen`
**Writes:** `paper_embedding`, `paper_screen`

1. **Model.** Use an open-source embedding model that runs locally (e.g. `BAAI/bge-large-en-v1.5`). The model must not change between `c_embed.py` and `d_screen.py`.
2. **Text to embed** (clean text only, no raw API JSON):
   - With an abstract (`embed_basis = abstract`): title + abstract.
   - Without an abstract (`embed_basis = title`): title + venue + year.
   - Truncate to the model's maximum input length.
3. **Incremental.** For each paper, hash the text. Embed it only if the paper has no `paper_screen` row or its `embed_hash` has changed. When the hash changes, reset `screened = 0` and `passed = NULL` so `d_screen.py` screens it again.
4. **Store.** Save one vector per `paper_id` in `paper_embedding` (overwriting on re-embed). `d_screen.py` scores every vector exactly, so no approximate index is needed.
5. **Initialize screening.** Insert or update the paper's `paper_screen` row with `embed_basis` and `embed_hash`.

---

## C.4 `d_screen.py`: Screen papers against the research topic

**Reads:** `paper_embedding`, `paper_dblp`, `paper_abstract`, `paper_screen`, `paper_seed`
**Writes:** `paper_screen`, `paper_verdict_with_abstract`

### Method
Screen every paper with `screened = 0`. A paper **passes** if **either** test passes:

1. **Semantic test.** Compute exact cosine similarity between the paper's vector and **each** of a fixed set of topic queries, and take the maximum. The queries cover the topic and its direct neighbors, because benchmark, survey, and evaluation papers count as relevant (A.10). For example: the research topic itself; LLM agents for penetration testing; automated red teaming with LLMs; LLM agents solving CTF challenges; LLM-driven exploit generation; AI attack planning and attack graphs; autonomous cyber-operations agents; benchmarks for offensive-security agents. Never use a top-k search, because it silently drops papers past k.
2. **Keyword test.** Case-insensitive match on the title, plus the abstract if one exists, for terms such as: penetration test, pentest, red team, CTF, capture the flag, exploit, attack chain, attack graph, privilege escalation, lateral movement, Metasploit, offensive security, cyber range, kill chain.

### Threshold (how "no false negatives" is enforced)
- Stop with an error if `paper_seed` is empty.
- Set a separate threshold for each `embed_basis`. Each threshold is the **lowest** similarity any seed gets under that basis, minus a safety margin (default 0.05). Score every seed under the `title` basis too (title + venue + year), so the title threshold is calibrated on its own.
- Stop with an error if any seed does not pass.
- Print the number of papers passed for each basis, so the margin can be tuned.

### Table updates

| Event | Update |
|---|---|
| Paper screened | `paper_screen`: set `screened = 1` and `similarity` |
| Passed, has abstract | `passed = 1`, `passed_by`; insert into `paper_verdict_with_abstract` (defaults) if it is not already there |
| Passed, no abstract | `passed = 1`, `passed_by`. It now appears in `paper_lookup_queue` |
| Rejected | `passed = 0`. If the paper is in `paper_verdict_with_abstract` with `reviewed = 0` (from an earlier screen), remove it |

---

## C.5 `e_lookup.md`: AI prompt to find missing abstracts online

**Reads:** `paper_lookup_queue`, `paper_dblp`, `paper_abstract_miss`
**Writes:** `paper_abstract`, `paper_abstract_hit`, `paper_abstract_miss`, `paper_abstract_status`

### Purpose
The APIs could not find abstracts for some screened-in papers (listed in `paper_lookup_queue`). This prompt directs an AI to search the web for those abstracts. The prompt must say how the AI writes to the database (the `sqlite3` CLI on `archive/paperhunter.db`, one transaction per paper).

### Workflow
For each paper, in `paper_lookup_queue` order, in batches of a set size (e.g. 20 per session):

1. **Get the details.** Fetch the paper's full record from `paper_dblp`.
2. **Check past misses.** Read `paper_abstract_miss` for this paper. Do not query any `source` already logged there.
3. **Search online.** Use every available detail: DOI landing page, arXiv ID, publisher page, title, authors, venue, year.
4. **Verify.** Confirm the page is about this paper (title, authors, and venue or year match) and that the text is the paper's own abstract, not a summary, a citing paper's text, or a generated description.
5. **Log each source.** One row per source tried, with `source` = the domain, `lookup_by = web`, and the relevant page text in `response`:
   - Verified abstract found → `paper_abstract_hit`.
   - Not found → `paper_abstract_miss`.
6. **Update the results.** If a verified abstract was found, insert it into `paper_abstract` (`source` = page URL) and update `paper_abstract_status`, all in one transaction. Otherwise only `paper_abstract_status.miss_count` changes.

### Rules
- **Accuracy:** record an abstract only if it was actually found and verified. Never guess, paraphrase, or generate one.
- **No repeats:** never query a source already in `paper_abstract_miss` for the same paper.
- **Afterwards:** rerun `c_embed.py` and `d_screen.py` so the new abstracts are re-embedded and re-screened.

---

## C.6 `f_classify.md`: AI prompt to classify screened papers with abstracts

**Reads:** `paper_verdict_with_abstract`, `paper_dblp`, `paper_abstract`
**Writes:** `paper_verdict_with_abstract`

### Purpose
Make the final decision on each screened-in paper that has an abstract, and categorize the accepted ones. (The text is read from `paper_dblp` and `paper_abstract`, not from `paper_embedding`, which holds only vectors.)

### Workflow
For each row in `paper_verdict_with_abstract` with `reviewed = 0`, in batches:

1. **Get the data.** Title, authors, venue, year, and abstract.
2. **Decide.** Apply the relevance definition in A.10 and decide whether to **accept** or **reject** the paper. If it is unclear from the abstract, the AI may check the paper online before deciding.
3. **Classify.** If accepted, assign exactly one category using A.10 and its tie-break.
4. **Record.** Set `reviewed = 1`, `verdict` = the category or `reject`, and `reason`.

---

## C.7 `g_infer.md`: AI prompt to classify screened papers without abstracts

**Reads:** `paper_lookup_queue`, `paper_dblp`, `paper_abstract_miss`, `paper_verdict_without_abstract`
**Writes:** `paper_verdict_without_abstract`, and in the abstract-found case: `paper_abstract`, `paper_abstract_hit`, `paper_abstract_miss`, `paper_abstract_status`, `paper_verdict_with_abstract`

### Purpose
Decide on screened-in papers whose abstracts were never found, using every other piece of available information. Run this only after `e_lookup.md` has worked through its queue.

### Workflow
For each paper in `paper_lookup_queue` that is not already in `paper_verdict_without_abstract`, in batches:

1. **Gather database details.** Combine the paper's records from `paper_dblp` and `paper_abstract_miss` (the miss responses often contain keywords, topics, or a TL;DR even without an abstract).
2. **Search online.** Look for more information: the full-text PDF, the publisher page, citing papers, the project repository, and author pages. Do not retry sources already in `paper_abstract_miss` for the abstract.
3. **If a genuine abstract is found**, handle it the way `e_lookup.md` does (verify, log, store it, update `paper_abstract_status`), insert the paper into `paper_verdict_with_abstract` with defaults, and move on. `f_classify.md` will decide on it.
4. **Combine everything.** Merge title, authors, venue, year, keywords, topics, TL;DR, and anything else found.
5. **Decide.** Apply A.10 and decide whether to **accept** or **reject** the paper. With less evidence, lean toward accepting borderline papers (false positives are acceptable here) and say so in `reason`.
6. **Classify.** If accepted, assign exactly one category using A.10 and its tie-break.
7. **Record.** Insert into `paper_verdict_without_abstract` with `reviewed = 1`, `verdict`, and `reason`.
