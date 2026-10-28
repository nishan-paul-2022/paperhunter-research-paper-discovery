# PaperHunter - Plan

## Contents

1. [Tables](#1-tables)
2. [Views](#2-views)
3. [Semantic Settings](#3-semantic-settings)
4. [Pipeline](#4-pipeline)
5. [Cleanup](#5-cleanup)

---

## 1. Tables

### 1.1 `paper_semantic_threshold`

Filled by `c-context/c_threshold.py`, before the pipeline runs (see section 3).

`basis` is `title` or `title_abstract`.

| Column        | Type |
|---------------|------|
| `basis` | TEXT |
| `threshold`   | REAL |

### 1.2 `paper_dblp`

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

### 1.3 `paper_status`

One row per paper. `status` is the last step that has finished with the paper: `A`, `B`, `C`, `D`, `E`, `F` or `G`. Step C also uses `B-1`, `B-2`, `B-3` and `B-4` for papers part-way through its lookup stages.

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `status`   | TEXT    |

### 1.4 `paper_field_arxiv`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `arxiv_id` | TEXT    |

### 1.5 `paper_field_abstract`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `abstract` | TEXT    |

### 1.6 `paper_semantic_vector`

`basis` is `title` or `title_abstract`: the text the vector was made from.

| Column        | Type    |
|---------------|---------|
| `paper_id`    | INTEGER |
| `basis` | TEXT    |
| `vector`      | BLOB    |

### 1.7 `paper_semantic_shortlist`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |

### 1.8 `paper_ai_shortlist`

Accepted papers only.

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |
| `category` | TEXT    |

---

## 2. Views

### 2.1 `paper_summary`

Every paper with all the fields mentioned below (one row per paper).

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

### 2.2 `paper_review`

Every paper that has no abstract.

**Depends on:** `paper_field_abstract`

| Column     | Type    |
|------------|---------|
| `paper_id` | INTEGER |

### 2.3 `paper_list`

Every paper from `paper_ai_shortlist` only.

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

---

## 3. Semantic Settings

The search settings live in `b-config/semantic.json`, tracked in git, so every run uses exactly the same inputs.

```json
{
  "topic": "LLM-based agents for automated penetration testing",
  "queries": ["large language model agents for automated penetration testing", "..."],
  "keywords": ["penetration test", "pentest", "capture the flag", "..."],
  "seeds": [
    {
      "title": "PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing",
      "abstract": "Penetration testing, a crucial industrial practice ...",
      "category": "baseline"
    }
  ]
}
```

| Key        | Meaning |
|------------|---------|
| `topic`    | The research topic, in one line. |
| `queries`  | Sentences describing the topic. A paper's score is its highest similarity to any query. |
| `keywords` | Words or phrases. A paper containing one (whole-word match) is shortlisted regardless of its score. |
| `seeds`    | Known relevant papers, with their title and abstract copied exactly from one trusted source. They set the thresholds. `category` (`baseline`, `survey`, `benchmark` or `evaluation`) is for our reference only and is not used by the code. |

### `c-context/c_threshold.py`

The thresholds depend only on `semantic.json` (queries and seeds), the model and the margin, so they are computed once, before the pipeline, by `c-context/c_threshold.py`. Run it after `c-context/b_schema.py`, and again whenever `semantic.json` changes.

- **Constants** (imported by `e_embed.py` and `f_semantic.py`, so all three use the same values):
  - Model: `BAAI/bge-small-en-v1.5`, pinned to revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (already downloaded to the local Hugging Face cache).
  - Margin: `0.05`. It lowers each threshold slightly below the weakest seed, so relevant papers that score a little lower than every seed are not missed.
- **Process:**
  1. Embed every query from `semantic.json`.
  2. Embed each seed in both forms: title alone, and title + abstract. A seed's score is its highest similarity to any query.
  3. For each `basis`, the threshold is the lowest seed score minus the margin.
  4. Store both thresholds in `paper_semantic_threshold`.

---

## 4. Pipeline

### 4.1 Overview

| Step | File             | Goal                                  | Takes papers at status | Sets status | Inputs                                       | Writes To |
|------|------------------|---------------------------------------|------------------------|-------------|----------------------------------------------|-----------|
| A    | `a_extract.py`   | Build the base paper list             | new papers             | `A`         | `d-download/dblp.xml`, `conf_list`, `journal_list` | `paper_dblp`, `paper_status`; `paper_id` in `paper_field_arxiv`, `paper_field_abstract` |
| B    | `b_arxiv.py`     | Fill in arXiv IDs                     | `A`                    | `B`         | `d-download/dblp.xml`, `paper_summary`          | `paper_field_arxiv`, `paper_status` |
| C    | `c_fetch.py`     | Fetch abstracts from external APIs    | `B` to `B-4`           | `B-1` to `B-4`, `C` | `paper_summary`; arXiv, OpenAlex, Semantic Scholar APIs | `paper_field_arxiv`, `paper_field_abstract`, `paper_status` |
| D    | `d_abstract.md`  | Find remaining missing abstracts      | `C`                    | `D`         | `paper_summary`, `paper_review`, web search | `paper_field_abstract`, `paper_status` |
| E    | `e_embed.py` | Generate embeddings                   | `D`                    | `E`         | `paper_summary`                              | `paper_semantic_vector`, `paper_status` |
| F    | `f_semantic.py`  | Build the semantic shortlist          | `E`                    | `F`         | `paper_semantic_vector`, `paper_semantic_threshold`, `paper_summary`, `b-config/semantic.json` | `paper_semantic_shortlist`, `paper_status` |
| G    | `g_search.md`    | Build the final AI-reviewed shortlist | `F`                    | `G`         | `paper_semantic_shortlist`, `paper_summary`  | `paper_ai_shortlist`, `paper_status` |

### 4.2 Status Rules

Every paper moves through every status from `A` to `G`, whether or not a step changes its data. Because of this, any file can be run at any time and any number of times: it always starts from the latest state, and no work is repeated.

1. **One status per paper.** `status` is the last step that has finished with the paper.
2. **Each step takes only papers at the previous status** (B takes `A`, C takes `B` and its stage statuses `B-1` to `B-4`, …, G takes `F`) and moves each one forward when it is done. Papers at any other status are ignored.
3. **Every paper moves forward**, including papers the step has nothing to do for. For example, B moves a paper that already has an arXiv ID from `A` to `B` without changing it, and G moves a paper that is not in `paper_semantic_shortlist` from `F` to `G` without an AI review.
4. **Status moves only on a finished outcome.** "Not found anywhere" is a finished outcome. A failed attempt (timeout, 429, 5xx, crash) is not: the paper keeps its status and the next run retries it.
5. **Data and status are written together** in one transaction per paper or batch, so a crash never leaves them out of step.
6. **Rerunning step A** adds new papers with status `A` and updates the DBLP fields of existing papers. Existing papers keep their status.
7. **The pipeline never moves a paper backward.** To redo a step for some papers (for example, after changing the seeds, queries or keywords), set their status back by hand.

### 4.3 Step Details

#### A. `a_extract.py` — Extract Papers

- **Inputs:** `d-download/dblp.xml`, `conf_list`, `journal_list`
- **Output:**
  - Complete `paper_dblp`, including `record_type` and `ee`.
  - Populate `paper_id` in `paper_field_arxiv` and `paper_field_abstract`.
  - Add each new paper to `paper_status` with status `A`.

#### B. `b_arxiv.py` — Collect arXiv IDs

- **Inputs:** `d-download/dblp.xml`, `paper_summary`
- **Takes:** papers at status `A`
- **Output:** Fill in every available value in `paper_field_arxiv`. Move every processed paper to `B`.

DBLP stores arXiv preprints as separate `journals/corr/` records and never links them to the published paper. Match each paper without an arXiv ID to its preprint as precisely as possible, by normalized title and DBLP's disambiguated author names. Write only matches that are certain. Leave ambiguous papers without an arXiv ID.

#### C. `c_fetch.py` — Fetch Abstracts from External APIs

- **Input:** `paper_summary`
- **Takes:** papers at status `B`, `B-1`, `B-2`, `B-3` and `B-4`
- **Output:** Fill in every available value in `paper_field_abstract` and `paper_field_arxiv`. Move every finished paper to `C`.

**Stages.** Run five stages one after another, each over the whole database. A lookup is successful only if it returns an abstract of the same paper.

| # | Stage                              | Takes | Looks up papers that      | Without an abstract, moves to |
|---|------------------------------------|-------|---------------------------|-------------------------------|
| 1 | arXiv by arXiv ID                  | `B`   | have an arXiv ID          | `B-1` |
| 2 | OpenAlex by DOI, 100 per call      | `B-1` | have a DOI                | `B-2` |
| 3 | OpenAlex by DOI, one per call      | `B-2` | have a DOI                | `B-3` |
| 4 | Semantic Scholar by DOI            | `B-3` | have a DOI                | `B-4` |
| 5 | OpenAlex by title (Semantic Scholar by title when OpenAlex has no budget) | `B-4` | have a title but no DOI and no arXiv ID | `C` |

Stage 3 exists because OpenAlex's filter (stage 2) misses some works that its single-DOI lookup finds. DOIs containing `,` or `|` cannot go in a filter, so stage 2 passes them to stage 3.

- **Passing on.** At the start of each stage, papers it does not look up move straight to the next status with no call.
- **Found.** A paper with an abstract moves to `C` at once.
- **Same paper.** A DOI result is used only if it is the same paper: its DOI equals ours, or it passes the title match check below, or it is another version of the same work (title similarity ≥ 80% and at least one shared author name part). arXiv results must have the arXiv ID asked for. A result that fails is treated as not found. The rule leans towards accepting: a real abstract must never be rejected.
- **Abstract text.** Any length of two or more words is accepted; a single word is accepted only if it is at least 100 characters long (text without spaces, such as Chinese). Text is rejected only if it is a short single word, has no letters, is a placeholder (such as "No abstract available"), is a withdrawal notice (such as "This paper has been withdrawn"), is a known non-abstract text (such as repository or journal boilerplate, "status: Published", "version:NA", a copyright or funding line), or is the same as the title. A rejected text counts as not found, so the next stage still gets its chance.
- **arXiv fallback.** When stage 2, 3, 4 or 5 finds no abstract, run the arXiv ID discovery below. If it yields a new arXiv ID, store it in `paper_field_arxiv` and call arXiv by that ID (or by the paper's existing arXiv ID). If arXiv returns an abstract, the paper moves to `C`.
- **Title results** (stage 5) are used only if they pass the title match check. Each paper is asked of OpenAlex. Once OpenAlex's budget is used up (every key and no key), the rest of stage 5 asks Semantic Scholar's title match (`/paper/search/match`, year ±1) instead. A paper answered by either source is processed: found moves to `C`, not found moves to `C` without an abstract.
- **Failed calls.** A failed call (timeout, 429, 5xx) leaves the paper at its status for the next run.
- **Saving.** Write every abstract, arXiv ID and status change to the database the moment it is known. An interruption (Ctrl+C, crash, power loss) loses only the calls in flight. The first Ctrl+C finishes the calls in flight and stops; a second quits at once. A rerun continues from the statuses in the database.

**Wrong copy sweep.** After stage 5, every run checks the whole database for abstracts that a source stored on the wrong paper. Abstracts count as the same text when they are identical or near-identical: every abstract of at least 40 words is keyed by its first 40 and its last 40 normalized words (lowercase, punctuation removed), papers sharing a key are compared, and pairs with at least 95% text similarity are joined into one group. An abstract stored on two or more papers is a wrong copy on a paper when all of these hold:

1. The abstract has at least 40 words.
2. Another paper in the group owns it: at least half of that paper's title words appear in the abstract.
3. This paper's title shares no word with the abstract.
4. Both papers have authors, and they share no author name part.

For each wrong copy, ask every source that can answer for it: arXiv (if the paper has an arXiv ID), OpenAlex by DOI and S2 by DOI (if it has a DOI), and OpenAlex by title (always; Semantic Scholar by title when OpenAlex has no budget). If the paper has no arXiv ID and one of those answers is the same paper and lists an arXiv ID, store that ID and ask arXiv too (the arXiv fallback). Keep only answers that pass the same-paper and abstract-text checks and differ from the wrong copy. If several remain, use the text most sources agree on; on a tie, use the order arXiv, OpenAlex, S2, title. If every source answered and none has a different abstract, clear the wrong copy, so step D can look for the real one. If a source did not answer, leave the paper as it is for the next run. Only papers at status `C` are changed, so steps D to G never see an abstract change under them; papers at any status count as owners. The owner's abstract and every status stay unchanged.

**Title match check.** A result found by title, or a DOI result whose DOI differs from ours, is accepted only if all three hold:

1. Normalized title similarity ≥ 95%, comparing titles both as they are and with bracketed tags such as "(Extended Abstract)" removed.
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
- OpenAlex: use every `OPENALEX_API_KEY_<n>` in `.env`, one at a time in number order. Every OpenAlex call (batch, single and title search) uses the current key, at up to 30 calls per second. When a key's daily budget is used up, or OpenAlex rejects it, switch to the next key, once per key. When every key is used up, the rest of the run continues without a key, at up to 10 calls per second, using the separate no-key allowance. Single-DOI lookups are free either way. When the no-key allowance is used up too, stage 2 pauses and its papers wait for the next run, and stage 5 continues with Semantic Scholar by title; the other stages carry on. Budgets reset at 00:00 UTC.
- Semantic Scholar: send `S2_API_KEY` from `.env` in the `x-api-key` header.

**Performance.**

- Each stage reads its papers in chunks of 10,000.
- Put as many papers as each API allows into one call: 100 IDs per arXiv `id_list` call, up to 100 DOIs per OpenAlex call (keeping the URL within the length limit), and up to 500 IDs per Semantic Scholar batch call.
- Stages 2 and 3 run 24 calls at once; stages 1, 4 and 5 run one call at a time.
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

#### E. `e_embed.py` — Generate Embeddings

- **Input:** `paper_summary`
- **Takes:** papers at status `D`
- **Output:** Complete `paper_semantic_vector`, and move every embedded paper to `E`.

**Process.** For each paper, embed **title + abstract only**: the same text form used for seeds and queries in step F. No other column of `paper_summary` is embedded.

- If the paper has an abstract, embed title + abstract and set `basis` to `title_abstract`.
- Otherwise, embed the title alone and set `basis` to `title`.

#### F. `f_semantic.py` — Semantic Shortlist

- **Inputs:** `paper_semantic_vector`, `paper_semantic_threshold`, title and abstract from `paper_summary`, and queries and keywords from `b-config/semantic.json`
- **Takes:** papers at status `E`
- **Output:** Complete `paper_semantic_shortlist`. Move every screened paper to `F`.

**Process.**

1. Load the model from `c_threshold.py`'s constants and embed every query from `semantic.json`. Read both thresholds from `paper_semantic_threshold`; stop if the table is empty (run `c-context/c_threshold.py` first).
2. **Shortlist.** A paper's score is its highest similarity to any query. Add the paper to `paper_semantic_shortlist` if either holds:
   - its score is at or above the threshold for its `basis`, or
   - its title or abstract contains a keyword from `semantic.json` (whole-word match).
3. Move every screened paper to `F`, shortlisted or not.

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

---

## 5. Cleanup

When this plan is implemented, the AI removes everything the project no longer needs, across the whole repository: `b-config/`, `c-context/`, `e-conference-hunter/`, `f-journal-hunter/`, `g-paper-hunter/`, `README.md`, `requirements.txt` and any other file.

- **Files:** old g-paper-hunter scripts and prompts replaced by this plan, and any other file no step uses.
- **Database:** paper tables and views in `b-config/paperhunter.db` that are not in this plan.
- **Code:** functions, constants and imports that nothing calls.
- **Dependencies:** packages in `requirements.txt` that no script imports.
- **Docs:** `README.md` updated to the new run order, with no references to removed files.

The conference and journal hunters stay, because step A needs `conf_list` and `journal_list`. Only their unused parts are removed.
