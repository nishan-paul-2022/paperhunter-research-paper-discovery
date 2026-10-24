# Paper Hunter: Task List

This is the work plan for building the pipeline specified in `plan.md`. Do **one task per session**, in order. Each task names the `plan.md` sections it implements. Those sections are the specification; this file only sets the order, the scope of each task, and how to check it is done.

## Rules for every task

1. **Read first.** Read `plan.md` in full, then the task. If the task and `plan.md` disagree, or `plan.md` leaves a decision open, stop and ask. Do not guess.
2. **Stay in scope.** Create or change only the files the task lists. Never modify `conf_*` or `journal_*` tables or views, or anything in `conference-hunter/` or `journal-hunter/`.
3. **Location and conventions.** All pipeline files go in `paper-hunter/`. Follow the conventions of the sibling hunters (see `conference-hunter/a_extract.py`):
   - `REPO_ROOT` is the parent of the script's folder.
   - `DB_PATH = REPO_ROOT / "archive" / "paperhunter.db"`.
   - `.env` is read from `REPO_ROOT / ".env"` by the script itself (no `python-dotenv`).
   - Scripts are run from the repo root, e.g. `python paper-hunter/a_extract.py`.
4. **Verify with data.** Every "Done when" check must be run against the real database or files, and its output shown. A task is not done until every check passes.
5. **Report.** End the session with: what was built, the check outputs (with numbers), and anything that did not pass. Then tick the task's box below.

## Status

- [x] Task 0: Prepare the workspace
- [x] Task 1: Schema (`a_extract.py`, part 1)
- [x] Task 2: DBLP extraction (`a_extract.py`, part 2)
- [x] Task 3: OpenAlex fetching (`b_fetch.py`, part 1)
- [x] Task 4: Semantic Scholar, arXiv and title fallback (`b_fetch.py`, part 2)
- [x] Task 5: Embedding (`c_embed.py`)
- [x] Task 6: Seed set (**done by the user, not the AI**)
- [x] Task 7: Screening (`d_screen.py`)
- [x] Task 8: Web abstract lookup prompt (`e_lookup.md`)
- [x] Task 9: Classification prompt (`f_classify.md`)
- [x] Task 10: No-abstract inference prompt (`g_infer.md`)
- [ ] Task 11: End-to-end check

---

## Task 0: Prepare the workspace

**Implements:** `plan.md` Scope
**Files:** delete `paper-hunter/a_extract.py`, `paper-hunter/b_details.py`, `paper-hunter/c_search.md`; create `requirements.txt` (repo root)

**Steps**
1. Delete the three old files in `paper-hunter/`. They are not reused (Scope). Do **not** drop any database tables yet; Task 1 does that.
2. Check the inputs exist and print their sizes: `archive/dblp.xml`, `archive/dblp.dtd`, `archive/paperhunter.db`.
3. Print the row counts of `conf_list` and `journal_list`. Both must be non-zero.
4. Check that `.env` defines the variables the pipeline will use: `OPENALEX_API_KEY_1` … `OPENALEX_API_KEY_5`, `S2_API_KEY`, `CONTACT_MAILTO`. Print only each variable's name and whether it is set. **Never print values.**
5. Create `requirements.txt` in the repo root with the third-party packages the pipeline will need: an HTTP client, `lxml` for streaming XML, the embedding stack (`sentence-transformers`, `numpy`), and a vector store if one is chosen in Task 5. Pin exact versions. Install them in the active environment.

**Done when**
- `paper-hunter/` is empty (the old files are deleted); `requirements.txt` is in the repo root.
- The input checks, list counts, and `.env` check pass, and their output is shown.
- `pip install -r requirements.txt` succeeds.

---

## Task 1: Schema (`a_extract.py`, part 1)

**Implements:** `plan.md` Global rules 2–3, Part A (A.1–A.8), Part B (B.1, B.2), C.1 step 1
**Files:** create `paper-hunter/a_extract.py` (schema part only)

**Steps**
1. Write a `prepare_schema()` function that:
   - drops the old Paper Hunter objects listed in Scope (`paper_dblp`, `paper_review`, `paper_list`, `paper_openalex`, `paper_s2`, `paper_candidate_ai`, `paper_summary`), whether each is a table or a view. Drop them only once: on the first run, when the new schema is not there yet. A rerun must not wipe extracted data.
   - creates every table in A.1–A.8 and the views in B.1–B.2 with `CREATE … IF NOT EXISTS`.
   - stops with an error if `conf_list` or `journal_list` is empty.
2. Use proper constraints: primary keys, `UNIQUE (dblp_key)`, foreign keys to `paper_dblp(paper_id)`, `CHECK` constraints for every enumerated value (`has_abstract`, `embed_basis`, `passed_by`, `screened`, `passed`, `reviewed`, `verdict`, `category`, `lookup_by`), and indexes on the columns the views and later steps filter on.
3. `paper_embedding` (A.9) is **not** created here; Task 5 creates it.
4. The script must run with only the schema step, e.g. `python paper-hunter/a_extract.py --schema-only`, for this task's checks. Task 2 adds the extraction.

**Done when**
- Run `--schema-only` twice. Both runs succeed, and the second changes nothing.
- `.schema` shows every table and view from A.1–A.8, B.1, B.2 with the column names exactly as in `plan.md`.
- None of the old objects remain except those recreated under the same name (`paper_dblp`, `paper_list`).
- Inserting a row that violates each `CHECK` constraint fails (show one test per constraint, then roll back).
- No `conf_*` or `journal_*` object was changed (compare `.schema` for those objects before and after).

---

## Task 2: DBLP extraction (`a_extract.py`, part 2)

**Implements:** `plan.md` Global rules 1–2, A.1, A.2, C.1 steps 2–8
**Files:** `paper-hunter/a_extract.py`

**Steps**
1. Add the required command-line arguments `--from-year` and `--to-year`.
2. Stream `archive/dblp.xml` with the DTD (e.g. `lxml.etree.iterparse` with `load_dtd=True`, clearing each element after use). Many records do not start on a new line, so do not parse line by line.
3. Apply the C.1 step 5 filters exactly. Fill every A.1 column: normalize the DOI, extract the arXiv ID from `ee`, store `authors` and `ee` as JSON arrays, and remove XML markup from titles.
4. Upsert by `dblp_key` with stable `paper_id`. Insert new `paper_abstract_status` rows. Commit in batches.
5. Print the C.1 step 8 report.

**Done when**
- `python paper-hunter/a_extract.py --from-year 2023 --to-year 2026` completes.
- **Expected count:** `paper_dblp` has 1,056,112 rows (±0.5%). A direct scan of the current `dblp.xml` for these years found 663,040 `article` and 393,072 `inproceedings` records under listed keys, with withdrawn records excluded. Explain any larger difference before finishing.
- No row has `record_type = 'proceedings'`, and none is a withdrawn record.
- Every `venue_key` is in `conf_list.dblp_key` or `journal_list.dblp_key`.
- Every `year` is between 2023 and 2026.
- `paper_abstract_status` has exactly one row per `paper_dblp` row, all with defaults.
- Spot check: `conf/uss/DengLVLLX0R0024` (PentestGPT) is present with its DOI and authors.
- **Rerun check:** run the same command again. The row count and every `paper_id` stay the same (compare a checksum of `paper_id, dblp_key` before and after).
- Running without `--from-year` or `--to-year` fails with a usage error.

---

## Task 3: OpenAlex fetching (`b_fetch.py`, part 1)

**Implements:** `plan.md` Global rules 1, 3, 4, 5; A.3; A.4; C.2 (OpenAlex DOI path only)
**Files:** create `paper-hunter/b_fetch.py`

**Steps**
1. Build the shared core that Task 4 extends:
   - load keys from `.env`;
   - remove API keys from every logged `request`;
   - sort each lookup into hit, miss, or transient error (A.4), and retry transient errors with backoff and `Retry-After`;
   - validate abstracts (C.2 "What counts as an abstract");
   - write the abstract, the log row, and the `paper_abstract_status` update in one transaction;
   - follow the skip rules, so a (`source`, `lookup_by`) pair already logged for a paper is never repeated.
2. Implement the OpenAlex DOI batch lookup (up to 50 DOIs per request). Rebuild abstracts from `abstract_inverted_index`. Split each batch response into one log row per paper.
3. Add a `--limit N` option to process only the first N pending papers, for testing.

**Done when**
- `--limit 500` runs and prints the number of hits, misses, and transient errors.
- For every paper processed: `hit_count` and `miss_count` equal its rows in the log tables, and `has_abstract = 1` exactly when it has a `paper_abstract` row.
- `grep` over `paper_abstract_hit.request` and `paper_abstract_miss.request` finds no API key value (compare against `.env` in code; do not print the keys).
- Five random stored abstracts are shown next to their titles. They are clean text, with no inverted index and no HTML.
- Rerunning `--limit 500` makes **no** new OpenAlex calls for those papers (show the call count).
- Simulating a 429 or timeout (e.g. with a mocked response) leaves no log row and triggers a retry.

---

## Task 4: Semantic Scholar, arXiv and title fallback (`b_fetch.py`, part 2)

**Implements:** `plan.md` C.2 in full
**Files:** `paper-hunter/b_fetch.py`

**Steps**
1. Add the Semantic Scholar DOI batch lookup (`POST /paper/batch`, up to 500 IDs).
2. Add the arXiv-ID lookup and the title fallback for both sources, following the C.2 lookup order. Run a title lookup only when the work was **not found** at all.
3. Implement the title-match check (similarity ≥ 0.95, year ±1, a shared author surname). A failed check is logged as a miss.
4. Run OpenAlex and S2 as two concurrent workers, each with its own rate limiter. S2 processes only papers that OpenAlex has finished without finding an abstract.
5. Commit after every batch so a crash loses at most one batch.

**Done when**
- `--limit 2000` runs. Print hits, misses, and errors per source and per `lookup_by`.
- No paper has an S2 log row unless it also has an OpenAlex miss row (shown by query).
- No paper has a title-lookup row for a source that found the work by DOI or arXiv ID.
- Ten title-lookup hits are shown with the DBLP title next to the returned title. All ten match.
- Killing the process mid-run and restarting it loses at most one batch and repeats no logged lookups.
- The run respects both APIs' documented rate limits (state the limits used and show the observed request rate).

After this task, the user runs `b_fetch.py` without `--limit` on all papers. This is a long run and is not part of the task.

---

## Task 5: Embedding (`c_embed.py`)

**Implements:** `plan.md` A.6, A.9, C.3
**Files:** create `paper-hunter/c_embed.py`; update `requirements.txt` if needed

**Steps**
1. Load the local embedding model (default `BAAI/bge-large-en-v1.5`). Use the GPU if one is available, otherwise the CPU.
2. Build the text for each paper as in C.3 step 2. Compute `embed_hash`.
3. Create the `paper_embedding` store (A.9) and save the model name and vector size with it. Refuse to run if the stored model differs from the configured one.
4. Embed in batches, only papers whose hash is new or changed. On a change, reset `screened = 0` and `passed = NULL`.
5. Add `--limit N` for testing.

**Done when**
- `--limit 5000` runs. Print the number embedded, the split by basis, and the throughput (papers per second).
- Every embedded paper has exactly one vector and one `paper_screen` row with a matching `embed_hash`.
- A rerun embeds 0 papers.
- Changing one paper's abstract in a test copy of the database causes exactly that paper to be re-embedded and reset.
- Vectors are normalized (show the norm range).

After this task, the user runs `c_embed.py` on all papers.

---

## Task 6: Seed set (done by the user)

**Implements:** `plan.md` A.5
**Owner:** the user. The AI does not do this task.

The user fills `paper_seed` (`paper_id`, `category`, `evidence`) with papers known to be relevant, following the A.5 guidance: cover all four categories and include papers whose titles lack obvious keywords.

**Done when:** `paper_seed` has rows in every category, and every `paper_id` exists in `paper_dblp`.

---

## Task 7: Screening (`d_screen.py`)

**Implements:** `plan.md` A.6, A.7, B.1, C.4
**Files:** create `paper-hunter/d_screen.py`

**Steps**
1. Define the topic queries (C.4 semantic test) and the keyword list (C.4 keyword test) as constants at the top of the file.
2. Embed the queries with the same model as `c_embed.py` (check the model name stored in `paper_embedding`).
3. Calibrate one threshold per basis from `paper_seed`, as in C.4 "Threshold". Score every seed under the `title` basis too. Stop if `paper_seed` is empty or any seed fails. Create `paper_screen_threshold` if it does not exist, store the thresholds, and trigger a full rescreen when they change.
4. Screen every paper with `screened = 0` using exact cosine similarity over all vectors (no top-k). Apply the C.4 table updates, including removing unreviewed rows from `paper_verdict_with_abstract` for papers that are now rejected.
5. Print both thresholds, the seed scores, and the number passed per basis and per `passed_by`.

**Done when**
- The run completes. Every seed has `passed = 1`.
- Every screened paper has `screened = 1` and a non-NULL `passed`.
- `paper_verdict_with_abstract` contains exactly the passed papers that have an abstract (show by query).
- `paper_lookup_queue` contains exactly the passed papers that have no abstract, sorted as in B.1.
- A rerun screens 0 papers.
- In a test copy of the database, adding a seed that lowers a threshold causes a full rescreen. Previously rejected papers above the new threshold now pass, and papers already reviewed in `paper_verdict_with_abstract` keep their verdicts.
- Print 20 random rejected papers with the highest similarity just below the threshold, so the user can judge the margin.

---

## Task 8: Web abstract lookup prompt (`e_lookup.md`)

**Implements:** `plan.md` A.3, A.4, B.1, C.5
**Files:** create `paper-hunter/e_lookup.md`

**Steps**
1. Write the prompt so an AI with web search and shell access can run it with no other context. Include:
   - the database path, and the exact `sqlite3` commands to read the queue, read a paper's misses, and write one paper's results in one transaction;
   - the workflow, verification rules, and logging rules from C.5;
   - the batch size, and how to resume.
2. Show how `source` (the domain) and `lookup_by = web` are recorded, and how `paper_abstract_status` counts are updated in the same transaction.

**Done when**
- A pilot run of the prompt on 5 papers from `paper_lookup_queue` (in a fresh AI session) writes correct rows: each source tried is logged once; counts match the logs; no abstract was stored without a verifiable URL.
- Each abstract stored in the pilot is shown with its source URL, so the user can confirm it.
- Rerunning the pilot skips sources already in `paper_abstract_miss`.

After this task, the user runs `e_lookup.md` through the queue, then reruns `c_embed.py` and `d_screen.py`.

---

## Task 9: Classification prompt (`f_classify.md`)

**Implements:** `plan.md` A.7, A.10, C.6
**Files:** create `paper-hunter/f_classify.md`

**Steps**
1. Write the prompt with the full A.10 relevance definition, the category definitions, and the tie-break order, quoted exactly.
2. Include the `sqlite3` commands to read pending rows (`reviewed = 0`) with title, authors, venue, year and abstract, and to write `reviewed`, `verdict` and `reason` per paper.
3. Specify the batch size and how to resume.

**Done when**
- A pilot run on 20 pending papers writes a valid `verdict` and a non-empty `reason` for each.
- The pilot's verdicts are shown in a table (title, verdict, reason) for the user to check.
- Any seed papers in the pilot are accepted, with their expected category.

After this task, the user runs `f_classify.md` through all pending rows.

---

## Task 10: No-abstract inference prompt (`g_infer.md`)

**Implements:** `plan.md` A.4, A.8, A.10, C.7
**Files:** create `paper-hunter/g_infer.md`

**Steps**
1. Write the prompt following C.7, including the abstract-found branch: log it, store it, update the status, insert into `paper_verdict_with_abstract`, and do not write a verdict.
2. Include the A.10 definitions and tie-break, quoted exactly, and the instruction to lean toward accepting borderline papers.
3. Include the `sqlite3` commands, the batch size, and how to resume.

**Done when**
- A pilot run on 10 papers from `paper_lookup_queue` writes either a verdict row in `paper_verdict_without_abstract` or, if an abstract was found, a correctly logged abstract plus a `paper_verdict_with_abstract` row. Never both.
- The pilot's results are shown in a table (title, verdict or "abstract found", reason or source URL).

After this task, the user runs `g_infer.md` through the queue, then runs `f_classify.md` again for any papers it moved.

---

## Task 11: End-to-end check

**Implements:** `plan.md` Global rules, B.2
**Files:** none (read-only checks)

**Done when** every query below runs and passes, and its output is shown:
- Every `paper_dblp` paper has a `paper_abstract_status` row and a `paper_screen` row.
- `hit_count` and `miss_count` equal the log-table counts for every paper.
- No paper is in both verdict tables.
- No paper with an abstract is in `paper_verdict_without_abstract`.
- Every passed paper with an abstract is in `paper_verdict_with_abstract` with `reviewed = 1`.
- Every passed paper without an abstract is in `paper_verdict_without_abstract` with `reviewed = 1`.
- Every seed paper is in `paper_list` with its expected category.
- `paper_list` row count by `category`, by `decided_from`, and by `venue_rank`.
- A funnel report: papers extracted → with abstract → passed screening → accepted, by category.
