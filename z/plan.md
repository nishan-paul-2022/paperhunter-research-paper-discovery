# Paper Hunter

Database schema and processing pipeline for collecting, enriching, embedding and shortlisting research papers.

---

## Contents

1. [Tables](#1-tables)
   - [Core Paper Data](#11-core-paper-data)
   - [Progress](#12-progress)
   - [Semantic Search](#13-semantic-search)
   - [AI Shortlist](#14-ai-shortlist)
2. [Views](#2-views)
3. [Pipeline](#3-pipeline)
   - [Overview](#31-overview)
   - [Status Rules](#32-status-rules)
   - [Step Details](#33-step-details)

---

## 1. Tables

### 1.1 Core Paper Data

#### `paper_dblp`

`dblp_key` is UNIQUE, so rerunning step A updates existing papers instead of duplicating them.

| Column        | Type    |
|---------------|---------|
| `paper_id`    | INTEGER |
| `dblp_key`    | TEXT    |
| `venue_key`   | TEXT    |
| `record_type` | TEXT    |
| `title`       | TEXT    |
| `authors`     | TEXT    |
| `year`        | INTEGER |
| `venue`       | TEXT    |
| `doi`         | TEXT    |
| `ee`          | TEXT    |

#### `paper_field_arxiv`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `arxiv_id` | TEXT    |

#### `paper_field_abstract`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `abstract` | TEXT    |

### 1.2 Progress

#### `paper_status`

One row per paper. `status` is the last step that has finished with the paper: `A`, `B`, `C`, `D`, `E`, `F` or `G`.

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `status`   | TEXT    |

### 1.3 Semantic Search

#### `paper_semantic_model`

| Column  | Type    |
|---------|---------|
| `id`    | INTEGER |
| `model` | TEXT    |
| `dim`   | INTEGER |

#### `paper_semantic_keyword`

| Column    | Type |
|-----------|------|
| `keyword` | TEXT |

#### `paper_semantic_query`

| Column  | Type |
|---------|------|
| `query` | TEXT |

#### `paper_semantic_seed`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `evidence` | TEXT    |

#### `paper_semantic_vector`

`embed_basis` is `title` or `title_abstract`: the text the vector was made from.

| Column        | Type    |
|---------------|---------|
| `paper_id`    | INTEGER |
| `embed_basis` | TEXT    |
| `vector`      | BLOB    |

#### `paper_semantic_shortlist`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |

#### `paper_semantic_threshold`

`embed_basis` is `title` or `title_abstract`.

| Column        | Type |
|---------------|------|
| `embed_basis` | TEXT |
| `threshold`   | REAL |

### 1.4 AI Shortlist

#### `paper_ai_shortlist`

Accepted papers only.

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `category` | TEXT    |

---

## 2. Views

### `paper_review`

Every paper that still has no abstract, waiting for a web search.

**Depends on:** `paper_field_abstract`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |

### `paper_list`

Every accepted paper, with its arXiv ID, abstract, category and venue rank: the final result.

**Depends on:** `paper_dblp`, `paper_field_arxiv`, `paper_field_abstract`, `paper_ai_shortlist`, `conf_list`, `journal_list`

| Column       | Type    |
|--------------|---------|
| `paper_id`   | INTEGER |
| `dblp_key`   | TEXT    |
| `venue_key`  | TEXT    |
| `title`      | TEXT    |
| `authors`    | TEXT    |
| `year`       | INTEGER |
| `venue`      | TEXT    |
| `doi`        | TEXT    |
| `arxiv_id`   | TEXT    |
| `abstract`   | TEXT    |
| `category`   | TEXT    |
| `venue_name` | TEXT    |
| `venue_rank` | TEXT    |

### `paper_summary`

Every paper with all its fields, pipeline status, venue rank and category: one row per paper.

**Depends on:** `paper_dblp`, `paper_field_arxiv`, `paper_field_abstract`, `paper_status`, `conf_list`, `journal_list`, `paper_ai_shortlist`

| Column        | Type    |
|---------------|---------|
| `paper_id`    | INTEGER |
| `dblp_key`    | TEXT    |
| `venue_key`   | TEXT    |
| `record_type` | TEXT    |
| `title`       | TEXT    |
| `authors`     | TEXT    |
| `year`        | INTEGER |
| `venue`       | TEXT    |
| `doi`         | TEXT    |
| `ee`          | TEXT    |
| `arxiv_id`    | TEXT    |
| `abstract`    | TEXT    |
| `status`      | TEXT    |
| `venue_name`  | TEXT    |
| `venue_rank`  | TEXT    |
| `category`    | TEXT    |

---

## 3. Pipeline

### 3.1 Overview

| Step | File             | Goal                                  | Takes papers at status | Sets status | Inputs                                       | Writes To |
|------|------------------|---------------------------------------|------------------------|-------------|----------------------------------------------|-----------|
| A    | `a_extract.py`   | Build the base paper list             | new papers             | `A`         | `archive/dblp.xml`, `conf_list`, `journal_list` | `paper_dblp`, `paper_status`; `paper_id` in `paper_field_arxiv`, `paper_field_abstract` |
| B    | `b_arxiv.py`     | Fill in arXiv IDs                     | `A`                    | `B`         | `archive/dblp.xml`, `paper_summary`          | `paper_field_arxiv`, `paper_status` |
| C    | `c_fetch.py`     | Fetch abstracts from external APIs    | `B`                    | `C`         | `paper_summary`; arXiv, OpenAlex, Semantic Scholar APIs | `paper_field_arxiv`, `paper_field_abstract`, `paper_status` |
| D    | `d_abstract.md`  | Find remaining missing abstracts      | `C`                    | `D`         | `paper_summary`, `paper_review`, web search | `paper_field_abstract`, `paper_status` |
| E    | `e_embedding.py` | Generate embeddings                   | `D`                    | `E`         | `paper_summary`                              | `paper_semantic_model`, `paper_semantic_vector`, `paper_status` |
| F    | `f_semantic.py`  | Build the semantic shortlist          | `E`                    | `F`         | `paper_semantic_vector`, `paper_semantic_model`, `paper_semantic_query`, `paper_semantic_seed`, `paper_semantic_keyword`, `paper_summary` | `paper_semantic_threshold`, `paper_semantic_shortlist`, `paper_status` |
| G    | `g_search.md`    | Build the final AI-reviewed shortlist | `F`                    | `G`         | `paper_semantic_shortlist`, `paper_summary`  | `paper_ai_shortlist`, `paper_status` |

### 3.2 Status Rules

Every paper moves through every status from `A` to `G`, whether or not a step changes its data. Because of this, any file can be run at any time and any number of times: it always starts from the latest state, and no work is repeated.

1. **One status per paper.** `status` is the last step that has finished with the paper.
2. **Each step takes only papers at the previous status** (B takes `A`, C takes `B`, …, G takes `F`) and moves each one forward when it is done. Papers at any other status are ignored.
3. **Every paper moves forward**, including papers the step has nothing to do for. For example, B moves a paper that already has an arXiv ID from `A` to `B` without changing it, and G moves a paper that is not in `paper_semantic_shortlist` from `F` to `G` without an AI review.
4. **Status moves only on a finished outcome.** "Not found anywhere" is a finished outcome. A failed attempt (timeout, 429, 5xx, crash) is not: the paper keeps its status and the next run retries it.
5. **Data and status are written together** in one transaction per paper or batch, so a crash never leaves them out of step.
6. **Rerunning step A** adds new papers with status `A` and updates the DBLP fields of existing papers. Existing papers keep their status.
7. **The pipeline never moves a paper backward.** To redo a step for some papers (for example, after changing the seeds, queries or keywords), set their status back by hand.

### 3.3 Step Details

#### A. `a_extract.py` — Extract Papers

- **Inputs:** `archive/dblp.xml`, `conf_list`, `journal_list`
- **Output:**
  - Complete `paper_dblp`, including `record_type` and `ee`.
  - Populate `paper_id` in `paper_field_arxiv` and `paper_field_abstract`.
  - Add each new paper to `paper_status` with status `A`.

#### B. `b_arxiv.py` — Collect arXiv IDs

- **Inputs:** `archive/dblp.xml`, `paper_summary`
- **Takes:** papers at status `A`
- **Output:** Fill in every available value in `paper_field_arxiv`. Move every processed paper to `B`.

DBLP stores arXiv preprints as separate `journals/corr/` records and never links them to the published paper. Match each paper without an arXiv ID to its preprint as precisely as possible, by normalized title and DBLP's disambiguated author names. Write only matches that are certain. Leave ambiguous papers without an arXiv ID.

#### C. `c_fetch.py` — Fetch Abstracts from External APIs

- **Input:** `paper_summary`
- **Takes:** papers at status `B`
- **Output:** Fill in every available value in `paper_field_abstract` and `paper_field_arxiv`. Move every finished paper to `C`.

**Lookup chain.** A lookup is successful only if it returns an abstract. For each paper without an abstract, run the lookups in this order and stop at the first success:

| # | Lookup                       | Runs if                 |
|---|------------------------------|-------------------------|
| 1 | arXiv by arXiv ID            | the paper has an arXiv ID |
| 2 | OpenAlex by DOI              | the paper has a DOI     |
| 3 | OpenAlex by title            | always                  |
| 4 | Semantic Scholar by DOI      | the paper has a DOI     |
| 5 | Semantic Scholar by title    | always                  |

- **arXiv fallback.** When lookup 2, 3, 4 or 5 finds the paper but no abstract, run the arXiv ID discovery below. If it yields a new arXiv ID, store it in `paper_field_arxiv` and call arXiv by that ID. If arXiv returns an abstract, the chain stops there.
- **Title results** (lookups 3 and 5) are used only if they pass the title match check. A result that fails is treated as not found.
- **Moving to `C`.** A paper moves to `C` as soon as a lookup succeeds, or when every lookup that runs has finished without an abstract. A failed call (timeout, 429, 5xx) leaves the paper at `B` for the next run.

**Title match check.** A title result is accepted only if all three hold:

1. Normalized title similarity ≥ 95%.
2. Publication year within ±1 of `paper_dblp.year`.
3. At least one shared author surname.

**arXiv ID discovery.** Used only when the paper has no arXiv ID yet. Each source is read in its own way:

| Source           | Where the arXiv ID is found |
|------------------|-----------------------------|
| OpenAlex         | an arXiv DOI (`10.48550/arXiv.<id>`) in `doi` or `ids`, or a `locations[]` entry whose `landing_page_url` or `pdf_url` is `arxiv.org/abs/<id>` or `arxiv.org/pdf/<id>` |
| Semantic Scholar | `externalIds.ArXiv` |

Store the ID without its version suffix (`2308.06782`, not `2308.06782v2`).

**API keys.**

- Load API keys from the `.env` file.
- OpenAlex: when a key hits its free-tier limit, switch to the next OpenAlex key in `.env`.

**Performance.**

- Work in rounds. Each round takes a block of papers at status `B` and runs the lookup chain as batch stages: only papers still without an abstract go on to the next stage.
- Put as many papers as each API allows into one call: many IDs per arXiv `id_list` call, up to 100 DOIs per OpenAlex call (keeping the URL within the length limit), and up to 500 IDs per Semantic Scholar batch call.
- Run multiple batches concurrently, within each source's rate limit.
- Retry 429 and 5xx responses with backoff, and honor `Retry-After`.

#### D. `d_abstract.md` — Find Missing Abstracts

- **Inputs:** `paper_summary`, `paper_review`
- **Takes:** papers at status `C`
- **Output:** Fill in every available value in `paper_field_abstract`. Move every paper to `D`.

**Process.**

1. **Skip papers with an abstract.** Move every paper at status `C` that is not in `paper_review` to `D` in one SQL update.
2. **Search the rest.** Take the papers at status `C` that are in `paper_review`, one by one. For each paper:
   1. Read its details (title, authors, year, venue, DOI, `ee`, arXiv ID) from `paper_summary`.
   2. Search online for its abstract, **excluding** OpenAlex, Semantic Scholar and arXiv.
   3. If found, write it to `paper_field_abstract`.
   4. Move the paper to `D`, found or not.

#### E. `e_embedding.py` — Generate Embeddings

- **Input:** `paper_summary`
- **Takes:** papers at status `D`
- **Output:** Record the model in `paper_semantic_model`, complete `paper_semantic_vector`, and move every embedded paper to `E`.

**Process.** For each paper, embed **title + abstract only**: the same text form used for seeds and queries in step F. No other column of `paper_summary` is embedded.

- If the paper has an abstract, embed title + abstract and set `embed_basis` to `title_abstract`.
- Otherwise, embed the title alone and set `embed_basis` to `title`.

#### F. `f_semantic.py` — Semantic Shortlist

- **Inputs:** `paper_semantic_vector`, `paper_semantic_model`, `paper_semantic_query`, `paper_semantic_seed`, `paper_semantic_keyword`, and title and abstract from `paper_summary`
- **Takes:** papers at status `E`
- **Output:** Complete `paper_semantic_threshold` and `paper_semantic_shortlist`. Move every screened paper to `F`.

**Process.**

1. Load the model recorded in `paper_semantic_model`. Embed every query in `paper_semantic_query`.
2. **Thresholds.** Embed each seed in `paper_semantic_seed` in both forms: title alone, and title + abstract. A seed's score is its highest similarity to any query. For each `embed_basis`, the threshold is the lowest seed score minus a small margin. Store both thresholds in `paper_semantic_threshold`.
3. **Shortlist.** A paper's score is its highest similarity to any query. Add the paper to `paper_semantic_shortlist` if either holds:
   - its score is at or above the threshold for its `embed_basis`, or
   - its title or abstract contains a keyword from `paper_semantic_keyword` (whole-word match).
4. Move every screened paper to `F`, shortlisted or not.

#### G. `g_search.md` — AI Review

- **Inputs:**
  - Paper IDs from `paper_semantic_shortlist`
  - Paper information from the `paper_summary` view
- **Takes:** papers at status `F`
- **Output:** Complete `paper_ai_shortlist`. Move every paper to `G`.

**Process.**

1. Move every paper at status `F` that is not in `paper_semantic_shortlist` to `G` in one SQL update, with no AI review.
2. Go through the shortlisted papers at status `F` one by one. For each paper, use AI to decide whether it belongs to the research topic:
   - If accepted, add it to `paper_ai_shortlist` with its category.
   - Move the paper to `G` either way, so an interrupted review resumes where it stopped.
