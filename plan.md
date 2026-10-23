Here's the full spec, restructured with tables and views first and then each file in order. Your four answers are included.

---

# Paper Hunter: Rebuild Specification

## Scope

The `paper-hunter/` folder needs a major overhaul. **Nothing that exists today can be assumed correct or reusable.** This includes code, prompts, tables, views, and anything else related to Paper Hunter. All of the Python files, prompt files, tables, and views listed below must be created from scratch.

## Research topic

Several steps evaluate papers against this topic:

> **LLM-based agentic framework for autonomous black-box penetration testing over long-horizon attack chains**

## Pipeline overview

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `a_extract.py` | Python | Extract papers from DBLP and initialize their status |
| 2 | `b_abstract.py` | Python | Fetch abstracts via the OpenAlex and Semantic Scholar APIs |
| 3 | `c_abstract.md` | AI prompt | Find abstracts that the APIs missed by searching online |
| 4 | `d_vector.py` | Python | Build a vector database of papers that have abstracts |
| 5 | `e_match.py` | Python | Semantically filter papers against the research topic |
| 6 | `f_search.md` | AI prompt | Accept or reject filtered papers and classify the accepted ones |
| 7 | `g_remaining.md` | AI prompt | Accept or reject papers that still have no abstract, and classify the accepted ones |

---

# Part A: Tables

## A.0 Existing input tables
These tables already exist and are only read by the pipeline.

| Table | Purpose |
|---|---|
| `conf_list` | Conferences whose papers should be extracted |
| `journal_list` | Journals whose papers should be extracted |

## A.1 `paper_dblp`
Every paper extracted from DBLP.

| Field | Description |
|---|---|
| `paper_id` | Auto-incrementing integer ID, starting from 1 |
| *(all DBLP fields)* | All information fetched from `dblp.xml` for that paper |

**Written by:** `a_extract.py`

## A.2 `paper_status`
The abstract-retrieval status of each paper.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | References `paper_dblp.paper_id` |
| `failure_count` | `0` | Number of calls that returned no abstract (derived from `paper_failure`) |
| `success_count` | `0` | Number of calls that returned an abstract (derived from `paper_success`) |
| `status` | `0` | `0` = no abstract found yet; `1` = abstract found |

**Definitions**
- **Success:** a call whose response contains the paper's abstract.
- **Failure:** a call whose response does not contain the abstract.

**Written by:** `a_extract.py` (inserts `paper_id` only); updated by `b_abstract.py` and `c_abstract.md`

## A.3 `paper_abstract`
The abstract of each paper for which one has been found.

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `abstract` | The paper's abstract |

**Written by:** `b_abstract.py`, `c_abstract.md`

## A.4 `paper_success` and `paper_failure`
A log of every call made for every paper, whether to an API or a website. Each call goes into exactly one of these tables, depending on the outcome. Both tables have the same structure.

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `api` | The full API call or website (complete request URL/endpoint) |
| `response` | The raw response, stored exactly as received (empty if there was no response) |

**Written by:** `b_abstract.py`, `c_abstract.md`

## A.5 `paper_rule`
The result of semantic screening against the research topic.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | References `paper_dblp.paper_id` |
| `rule_review` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `rule_candidate` | `NULL` | `NULL` = undecided; `1` = accepted; `0` = rejected |

**Written by:** `d_vector.py` (inserts `paper_id` only); updated by `e_match.py`

## A.6 `paper_ai`
The AI's final decision on papers that have abstracts and passed screening.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | References `paper_dblp.paper_id` |
| `ai_review` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `ai_candidate` | `NULL` | `NULL` = undecided; otherwise one of `baseline`, `survey`, `benchmark`, `evaluation`, `reject` |

**Written by:** `e_match.py` (inserts only papers with `rule_candidate = 1`); updated by `f_search.md`

## A.7 `paper_probably`
The AI's final decision on papers that have no abstract. Its structure is identical to `paper_ai`.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | References `paper_dblp.paper_id` |
| `ai_review` | `0` | `0` = not yet reviewed; `1` = reviewed |
| `ai_candidate` | `NULL` | `NULL` = undecided; otherwise one of `baseline`, `survey`, `benchmark`, `evaluation`, `reject` |

**Written by:** `g_remaining.md`

## A.8 Paper categories
These are the categories used in `ai_candidate`:

| Value | Meaning |
|---|---|
| `baseline` | Baseline paper |
| `survey` | Survey paper |
| `benchmark` | Benchmark paper |
| `evaluation` | Evaluation paper |
| `reject` | Not relevant to the research topic |

---

# Part B: Views

## B.1 `paper_review`
Papers that still have no abstract, in priority order.

| Field | Description |
|---|---|
| `paper_id` | Paper ID |
| `failure_count` | Number of failed calls |

- **Built on:** `paper_status`
- **Filter:** only papers with `status = 0`.
- **Sort:** by `failure_count`, ascending. The first row is the highest-priority paper and the last row is the lowest.

**Read by:** `c_abstract.md`, `g_remaining.md`

## B.2 `paper_list`
The final list of accepted papers.

| Field | Description |
|---|---|
| *(all `paper_dblp` columns)* | Every column from `paper_dblp` |
| `category` | `baseline`, `survey`, `benchmark`, or `evaluation` |

- **Built on:** `paper_ai` and `paper_probably`, combined with `paper_dblp`, `conf_list`, and `journal_list`.
- **Filter:** only accepted papers, meaning `ai_candidate` is not `reject` (and not `NULL`).

---

# Part C: Files

## C.1 `a_extract.py`: Extract papers from DBLP

**Reads:** `archive/dblp.xml`, `conf_list`, `journal_list`
**Writes:** `paper_dblp`, `paper_status`

1. Read papers from `archive/dblp.xml`.
2. Keep only papers whose venue appears in `conf_list` or `journal_list`.
3. Insert each kept paper into `paper_dblp`, with all of its DBLP information and a new incremental `paper_id`.
4. Insert each `paper_id` into `paper_status`. All other fields take their default value of `0`.

---

## C.2 `b_abstract.py`: Fetch abstracts via APIs

**Reads:** `paper_dblp`, `.env`
**Writes:** `paper_abstract`, `paper_success`, `paper_failure`, `paper_status`

### Behavior
Fetch the abstract for every paper in `paper_dblp`.

### API strategy

**Sources**
- Call **OpenAlex** and **Semantic Scholar** simultaneously for each paper.
- Load the API keys from the `.env` file.

**Lookup order**
1. Query both APIs by **DOI** first.
2. If the DOI lookup fails, retry using the paper's **title**.

**Efficiency requirements**
The code must be designed with all of the following in mind together:
- **Maximum yield:** get as many abstracts as possible.
- **Minimum time:** finish as quickly as possible.
- **Minimum API calls:** avoid unnecessary calls to reduce credit usage.
- **API limits:** respect each API's rate limits, throttling rules, and any other usage restrictions.

### Table updates

| Table | Update |
|---|---|
| `paper_abstract` | Insert the abstract when one is found |
| `paper_success` | Log every successful API call |
| `paper_failure` | Log every failed API call |
| `paper_status` | Refresh `success_count` and `failure_count` from the two log tables; set `status = 1` once an abstract is found |

---

## C.3 `c_abstract.md`: AI prompt for an online abstract search

**Reads:** `paper_review`, `paper_dblp`, `paper_failure`
**Writes:** `paper_abstract`, `paper_success`, `paper_failure`, `paper_status`

### Purpose
Many papers may still be listed in `paper_review` because the APIs could not find their abstracts. This prompt directs an AI to search the web for those abstracts.

### Workflow
For each paper, in the order listed in `paper_review` (highest priority first):

1. **Get the details.** Take the next `paper_id` from `paper_review` and fetch its full record from `paper_dblp`.
2. **Check past failures.** Read `paper_failure` for this `paper_id`. Skip every API or website already logged there, because searching them again wastes tokens.
3. **Search online.** Use every available detail to search for the abstract: DOI, venue, title, authors, year, and every other field in `paper_dblp`.
4. **Log each source.** Record every API or website the AI tries:
   - **Abstract found:** log it in `paper_success`, with whatever information the source returned in the `response` field.
   - **Abstract not found:** log it in `paper_failure`, with whatever information the source returned in the `response` field.
5. **Update the results.**
   - **If a genuine abstract is found:** update `paper_abstract`, `paper_success`, and `paper_status`.
   - **If no abstract is found:** update `paper_failure`.

### Rules
- **Accuracy:** record an abstract only if the AI has truly found it. Never guess or generate one.
- **No repeats:** never query a source already logged in `paper_failure` for the same paper.

---

## C.4 `d_vector.py`: Build the vector database

**Reads:** `paper_abstract`, `paper_dblp`, `paper_success`
**Writes:** vector database, `paper_rule`

1. **Embedding model:** use an open-source embedding model hosted locally.
2. **Input:** for every paper in `paper_abstract`, combine its details from:
   - `paper_dblp`: bibliographic metadata
   - `paper_abstract`: the abstract
   - `paper_success`: the raw responses from successful sources
3. **Output:** embed each paper and store it in the vector database.
4. **Initialize screening:** insert each `paper_id` into `paper_rule`. The other fields take their default values.

---

## C.5 `e_match.py`: Filter papers by research topic

**Reads:** vector database, `paper_abstract`
**Writes:** `paper_rule`, `paper_ai`

### Method
- Run a **semantic search** in the vector database against the research topic, using the same embedding model as `d_vector.py`.
- Screen every paper in `paper_abstract`.

### Filtering requirement
- **No false negatives:** a relevant paper must never be excluded.
- **False positives are acceptable:** an irrelevant paper may be included.
- Apply the filter with the highest level of rigor that satisfies both rules above.

### Table updates

| Event | Update |
|---|---|
| Paper reviewed | `paper_rule`: set `rule_review = 1` |
| Paper accepted | `paper_rule`: set `rule_candidate = 1`; insert `paper_id` into `paper_ai` (other fields take their defaults) |
| Paper rejected | `paper_rule`: set `rule_candidate = 0` |

---

## C.6 `f_search.md`: AI prompt to classify filtered papers

**Reads:** `paper_ai`, vector database
**Writes:** `paper_ai`

### Purpose
Make the final decision on each paper that passed semantic screening, and categorize the ones that are accepted.

### Workflow
For each paper in `paper_ai`:

1. **Get the data.** Retrieve the paper's data from the vector database.
2. **Decide.** Compare the paper against the research topic and decide whether to **accept** or **reject** it.
3. **Classify.** If the paper is accepted, assign exactly one category: `baseline`, `survey`, `benchmark`, or `evaluation` (see A.8).
4. **Record.** Update `paper_ai`:
   - Set `ai_review = 1`.
   - Set `ai_candidate` to the assigned category, or to `reject` if the paper was rejected.

---

## C.7 `g_remaining.md`: AI prompt to classify papers without abstracts

**Reads:** `paper_review`, `paper_dblp`, `paper_failure`
**Writes:** `paper_probably`

### Purpose
Decide on papers whose abstracts were never found (those still in `paper_review`), using every other piece of available information.

### Workflow
For each paper in `paper_review`:

1. **Gather database details.** Combine the paper's records from `paper_dblp` and `paper_failure`.
2. **Search online.** Use those details to the fullest to find more information about the paper.
3. **Combine everything.** Merge all information from the database and the online search: title, authors, venue, year, keywords, topics, TL;DR, and anything else found.
4. **Decide.** Based on the combined information, compare the paper against the research topic and decide whether to **accept** or **reject** it.
5. **Classify.** If the paper is accepted, assign exactly one category: `baseline`, `survey`, `benchmark`, or `evaluation` (see A.8).
6. **Record.** Write the result to `paper_probably`:
   - Set `ai_review = 1`.
   - Set `ai_candidate` to the assigned category, or to `reject` if the paper was rejected.
