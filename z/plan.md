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

| Column      | Type    |
|-------------|---------|
| `paper_id`  | INTEGER |
| `dblp_key`  | TEXT    |
| `venue_key` | TEXT    |
| `title`     | TEXT    |
| `authors`   | TEXT    |
| `year`      | INTEGER |
| `venue`     | TEXT    |
| `doi`       | TEXT    |

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

#### `paper_semantic_query` and `paper_semantic_seed`

Both tables share the same structure.

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `category` | TEXT    |
| `evidence` | TEXT    |

#### `paper_semantic_vector`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `vector`   | BLOB    |

#### `paper_semantic_shortlist`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |

#### `paper_semantic_threshold`

| Column        | Type |
|---------------|------|
| `embed_basis` | TEXT |
| `threshold`   | REAL |

### 1.4 AI Shortlist

#### `paper_ai_shortlist`

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

**Depends on:** `paper_dblp`, `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex`, `paper_details_s2`, `conf_list`, `journal_list`, `paper_list`

| Column              | Type    |
|---------------------|---------|
| `paper_id`          | INTEGER |
| `dblp_key`          | TEXT    |
| `venue_key`         | TEXT    |
| `title`             | TEXT    |
| `authors`           | TEXT    |
| `year`              | INTEGER |
| `venue`             | TEXT    |
| `doi`               | TEXT    |
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

| Step | File             | Goal                                                    | Inputs                                       | Writes To |
|------|------------------|---------------------------------------------------------|----------------------------------------------|-----------|
| A    | `a_extract.py`   | Build the base paper list                               | `archive/dblp.xml`, `conf_list`, `journal_list` | `paper_dblp`; `paper_id` in `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex`, `paper_details_s2` |
| B    | `b_arxiv.py`     | Fill in arXiv IDs                                       | `archive/dblp.xml`, `paper_summary`          | `paper_field_arxiv` |
| C    | `c_fetch.py`     | Enrich papers from external APIs                        | arXiv, OpenAlex, Semantic Scholar APIs       | `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex`, `paper_details_s2` |
| D    | `d_abstract.md`  | Find remaining missing abstracts                        | `paper_absent_abstract`, web search          | `paper_field_abstract` |
| E    | `e_embedding.py` | Generate embeddings                                     | `paper_summary`                              | `paper_semantic_vector` |
| F    | `f_semantic.py`  | Build the semantic shortlist                            | `paper_semantic_vector`                      | `paper_semantic_shortlist` |
| G    | `g_search.md`    | Build the final AI-reviewed shortlist                   | `paper_semantic_shortlist`, `paper_summary`  | `paper_ai_shortlist` |

### 3.2 Step Details

#### A. `a_extract.py` — Extract Papers

- **Inputs:** `archive/dblp.xml`, `conf_list`, `journal_list`
- **Output:**
  - Complete `paper_dblp`.
  - Populate `paper_id` in `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex` and `paper_details_s2`.

#### B. `b_arxiv.py` — Collect arXiv IDs

- **Inputs:** `archive/dblp.xml`, `paper_summary`
- **Output:** Fill in every available value in `paper_field_arxiv`.

#### C. `c_fetch.py` — Fetch Details from External APIs

- **Output:** Fill in every available value in `paper_field_arxiv`, `paper_field_abstract`, `paper_details_arxiv`, `paper_details_openalex` and `paper_details_s2`.

**API calls.** For every paper, call all three APIs simultaneously. None may be skipped.

| API              | Lookup                                                     |
|------------------|------------------------------------------------------------|
| arXiv            | By arXiv ID (if the paper has one)                         |
| OpenAlex         | By DOI (if the paper has one); if the DOI lookup fails, by title |
| Semantic Scholar | By DOI (if the paper has one); if the DOI lookup fails, by title |

**API keys.**

- Load API keys from the `.env` file.
- For OpenAlex: when a key hits its free-tier limit, switch to the next OpenAlex key in `.env`.

**Performance.**

- Maximize parallel and batch processing, so the largest number of papers is processed in the shortest time.
- Handle throttling with proper engineering.

**arXiv ID discovery.** OpenAlex and Semantic Scholar responses usually include an arXiv ID. If a response contains an arXiv ID and the paper has none in `paper_field_arxiv`:

1. Update `paper_field_arxiv` with that ID.
2. Call the arXiv API with that ID.

#### D. `d_abstract.md` — Find Missing Abstracts

- **Input:** `paper_absent_abstract`
- **Output:** Fill in every available value in `paper_field_abstract`.

**Process.** Go through `paper_absent_abstract` row by row. For each paper:

1. Search online, **excluding** OpenAlex, Semantic Scholar and arXiv.
2. Retrieve the abstract from any available source.
3. Update `paper_field_abstract`.

#### E. `e_embedding.py` — Generate Embeddings

- **Input:** `paper_summary`
- **Output:** Complete `paper_semantic_vector`.

**Process.** Go through `paper_summary` row by row. For each paper, vectorize the complete row and update `paper_semantic_vector`.

#### F. `f_semantic.py` — Semantic Shortlist

- **Input:** `paper_semantic_vector`
- **Output:** Complete `paper_semantic_shortlist`.

#### G. `g_search.md` — AI Review

- **Inputs:**
  - Paper IDs from `paper_semantic_shortlist`
  - Paper information from the `paper_summary` view
- **Output:** Complete `paper_ai_shortlist`.

**Process.** Use AI to review every shortlisted paper and update `paper_ai_shortlist`.
