# Paper Hunter

Database schema and processing pipeline for collecting, enriching, embedding and shortlisting research papers.

---

## Contents

1. [Tables](#1-tables)
   - [Core Paper Data](#11-core-paper-data)
   - [External API Details](#12-external-api-details)
   - [Semantic Search](#13-semantic-search)
   - [AI Shortlist](#14-ai-shortlist)
2. [Views](#2-views)
3. [Pipeline](#3-pipeline)
   - [Overview](#31-overview)
   - [Step Details](#32-step-details)

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

### 1.2 External API Details

The following three tables share the same structure and store the raw request and response for each external source.

| Table                    | Source           |
|--------------------------|------------------|
| `paper_details_arxiv`    | arXiv            |
| `paper_details_openalex` | OpenAlex         |
| `paper_details_s2`       | Semantic Scholar |

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `request`  | TEXT    |
| `response` | TEXT    |

- `request IS NULL` means the source has not been called for this paper yet.
- `request` is the URL of the call that settled the paper (for a batch call, the batch URL). If a DOI lookup is followed by a title lookup, the title lookup is stored.
- `response` is this paper's own record from the response, or empty when the source has no record for it.
- Only definitive answers are stored (found / not found). Failed calls (timeouts, 429, 5xx) are not stored, so the next run retries them.

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

Every reviewed paper gets a row. `category` is the accepted category, or `reject`.

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `category` | TEXT    |

---

## 2. Views

### `paper_absent_abstract`

**Depends on:** `paper_field_abstract`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |

### `paper_list`

Accepted papers only (`category` is not `reject`).

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

**Depends on:** `paper_dblp`, `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex`, `paper_details_s2`, `conf_list`, `journal_list`, `paper_ai_shortlist`

| Column              | Type    |
|---------------------|---------|
| `paper_id`          | INTEGER |
| `dblp_key`          | TEXT    |
| `venue_key`         | TEXT    |
| `record_type`       | TEXT    |
| `title`             | TEXT    |
| `authors`           | TEXT    |
| `year`              | INTEGER |
| `venue`             | TEXT    |
| `doi`               | TEXT    |
| `ee`                | TEXT    |
| `arxiv_id`          | TEXT    |
| `abstract`          | TEXT    |
| `request_arxiv`     | TEXT    |
| `response_arxiv`    | TEXT    |
| `request_openalex`  | TEXT    |
| `response_openalex` | TEXT    |
| `request_s2`        | TEXT    |
| `response_s2`       | TEXT    |
| `venue_name`        | TEXT    |
| `venue_rank`        | TEXT    |
| `category`          | TEXT    |

---

## 3. Pipeline

### 3.1 Overview

The pipeline runs forward only: each step runs after the previous one has finished, and no step goes back to change an earlier step's output.

| Step | File             | Goal                                                    | Inputs                                       | Writes To |
|------|------------------|---------------------------------------------------------|----------------------------------------------|-----------|
| A    | `a_extract.py`   | Build the base paper list                               | `archive/dblp.xml`, `conf_list`, `journal_list` | `paper_dblp`; `paper_id` in `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex`, `paper_details_s2` |
| B    | `b_arxiv.py`     | Fill in arXiv IDs                                       | `archive/dblp.xml`, `paper_summary`          | `paper_field_arxiv` |
| C    | `c_fetch.py`     | Enrich papers from external APIs                        | `paper_summary`, `paper_absent_abstract`; arXiv, OpenAlex, Semantic Scholar APIs | `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex`, `paper_details_s2` |
| D    | `d_abstract.md`  | Find remaining missing abstracts                        | `paper_absent_abstract`, web search          | `paper_field_abstract` |
| E    | `e_embedding.py` | Generate embeddings                                     | `paper_summary`                              | `paper_semantic_model`, `paper_semantic_vector` |
| F    | `f_semantic.py`  | Build the semantic shortlist                            | `paper_semantic_vector`, `paper_semantic_model`, `paper_semantic_query`, `paper_semantic_seed`, `paper_semantic_keyword`, `paper_summary` | `paper_semantic_threshold`, `paper_semantic_shortlist` |
| G    | `g_search.md`    | Build the final AI-reviewed shortlist                   | `paper_semantic_shortlist`, `paper_summary`  | `paper_ai_shortlist` |

### 3.2 Step Details

#### A. `a_extract.py` — Extract Papers

- **Inputs:** `archive/dblp.xml`, `conf_list`, `journal_list`
- **Output:**
  - Complete `paper_dblp`, including `record_type` and `ee`.
  - Populate `paper_id` in `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex` and `paper_details_s2`.

#### B. `b_arxiv.py` — Collect arXiv IDs

- **Inputs:** `archive/dblp.xml`, `paper_summary`
- **Output:** Fill in every available value in `paper_field_arxiv`.

DBLP stores arXiv preprints as separate `journals/corr/` records and never links them to the published paper. Match each paper to its preprint as precisely as possible, by normalized title and DBLP's disambiguated author names. Write only matches that are certain. Leave ambiguous papers without an arXiv ID.

#### C. `c_fetch.py` — Fetch Details from External APIs

- **Inputs:** `paper_summary`, `paper_absent_abstract`
- **Output:** Fill in every available value in `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex` and `paper_details_s2`.

**Work queue.** Every run starts from the current state of the database:

- A paper is queued for a source when that source's `request` is NULL in `paper_summary` (for arXiv, only when the paper has an arXiv ID).
- A source that already has a stored request for a paper is never called again for it.
- Rerunning `c_fetch.py` any number of times only processes what is still missing.

**API calls.** Every paper is sent to all three APIs. None may be skipped, even if another source has already returned an abstract.

| API              | Lookup                                                     |
|------------------|------------------------------------------------------------|
| arXiv            | By arXiv ID (if the paper has one)                         |
| OpenAlex         | By DOI (if the paper has one); if the DOI lookup gives no abstract, by title |
| Semantic Scholar | By DOI (if the paper has one); if the DOI lookup gives no abstract, by title |

- "DOI lookup gives no abstract" means the paper was not found by its DOI, or was found without an abstract.
- Papers without a DOI are looked up by title directly.

**Title match check.** A title lookup result is accepted only if all three hold:

1. Normalized title similarity ≥ 95%.
2. Publication year within ±1 of `paper_dblp.year`.
3. At least one shared author surname.

**Abstract.** Write `paper_field_abstract` only when it is still empty. The first valid abstract found is kept.

**arXiv ID discovery.** If a response contains an arXiv ID and the paper has none in `paper_field_arxiv`:

1. Update `paper_field_arxiv` with that ID.
2. Call the arXiv API with that ID.

Each source is read in its own way:

| Source           | Where the arXiv ID is found |
|------------------|-----------------------------|
| Semantic Scholar | `externalIds.ArXiv` |
| OpenAlex         | an arXiv DOI (`10.48550/arXiv.<id>`) in `doi` or `ids`, or a `locations[]` entry whose `landing_page_url` or `pdf_url` is `arxiv.org/abs/<id>` or `arxiv.org/pdf/<id>` |

Store the ID without its version suffix (`2308.06782`, not `2308.06782v2`).

**API keys.**

- Load API keys from the `.env` file.
- For OpenAlex: when a key hits its free-tier limit, switch to the next OpenAlex key in `.env`.

**Performance.**

- Put as many papers as each API allows into a single batch call:
  - OpenAlex: up to 100 DOIs per call, keeping the URL within the length limit.
  - Semantic Scholar: up to 500 IDs per batch call.
  - arXiv: many IDs per `id_list` call.
- Run multiple batches concurrently, while staying within each source's rate limit.
- Handle throttling with proper engineering: retry 429 and 5xx responses with backoff, and honor `Retry-After`.

#### D. `d_abstract.md` — Find Missing Abstracts

- **Input:** `paper_absent_abstract`
- **Output:** Fill in every available value in `paper_field_abstract`.

**Process.** Go through `paper_absent_abstract` row by row. For each paper:

1. Search online, **excluding** OpenAlex, Semantic Scholar and arXiv.
2. Retrieve the abstract from any available source.
3. Update `paper_field_abstract`.

#### E. `e_embedding.py` — Generate Embeddings

- **Input:** `paper_summary`
- **Output:** Record the model in `paper_semantic_model`, and complete `paper_semantic_vector`.

**Process.** For each paper, embed **title + abstract only**: the same text form used for seeds and queries in step F. No other column of `paper_summary` is embedded.

- If the paper has an abstract, embed title + abstract and set `embed_basis` to `title_abstract`.
- Otherwise, embed the title alone and set `embed_basis` to `title`.

#### F. `f_semantic.py` — Semantic Shortlist

- **Inputs:** `paper_semantic_vector`, `paper_semantic_model`, `paper_semantic_query`, `paper_semantic_seed`, `paper_semantic_keyword`, and title and abstract from `paper_summary`
- **Output:** Complete `paper_semantic_threshold` and `paper_semantic_shortlist`.

**Process.**

1. Load the model recorded in `paper_semantic_model`. Embed every query in `paper_semantic_query`.
2. **Thresholds.** Embed each seed in `paper_semantic_seed` in both forms: title alone, and title + abstract. A seed's score is its highest similarity to any query. For each `embed_basis`, the threshold is the lowest seed score minus a small margin. Store both thresholds in `paper_semantic_threshold`.
3. **Shortlist.** A paper's score is its highest similarity to any query. Add the paper to `paper_semantic_shortlist` if either holds:
   - its score is at or above the threshold for its `embed_basis`, or
   - its title or abstract contains a keyword from `paper_semantic_keyword` (whole-word match).

#### G. `g_search.md` — AI Review

- **Inputs:**
  - Paper IDs from `paper_semantic_shortlist`
  - Paper information from the `paper_summary` view
- **Output:** Complete `paper_ai_shortlist`.

**Process.** Use AI to review every shortlisted paper that has no row in `paper_ai_shortlist` yet. Record each decision as a category, or `reject`. An interrupted review resumes where it stopped.
