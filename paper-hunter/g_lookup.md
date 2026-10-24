# Master Prompt: Find Missing Abstracts Online (Paper Hunter)

You are an autonomous research assistant with web search and shell access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Some screened-in papers have no abstract because the OpenAlex and Semantic Scholar APIs could not find one. They are listed in the view `paper_lookup_queue`. For each, search the web for the paper's own abstract, verify it, and store it. Log every source you try so the work is resumable and no source is ever queried twice for the same paper.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_abstract`, `paper_abstract_hit`, `paper_abstract_miss`, and `paper_abstract_status`. Never change the schema or any other table.
- **Accuracy over coverage.** Store an abstract only when you have found it on a trustworthy page and verified it is this paper's own abstract. Never guess, paraphrase, translate, summarize, or generate an abstract. A missing abstract is a correct outcome; a wrong one is not.
- **Never repeat a source.** Before searching a paper, read its rows in `paper_abstract_miss`. Never query a `source` already listed there for that paper.
- Record each source you try as exactly one row, with `lookup_by = 'web'` and `source` = the page's bare domain (for example `dl.acm.org`, `arxiv.org`, `openreview.net`, `www.usenix.org`), lowercased, no `https://`, no path.
- Write each paper's results in **one transaction** (Global rule 3): the abstract, its log rows, and the `paper_abstract_status` update all commit together or not at all.

## Work queue

`paper_lookup_queue` already lists the screened-in papers that still need an abstract, most-promising first (highest screening similarity, then fewest misses so far). Read a batch of **20** per session:

```sh
sqlite3 -readonly archive/paperhunter.db \
  "SELECT paper_id, round(similarity,3), miss_count FROM paper_lookup_queue LIMIT 20;"
```

Resume by simply running this again: a found abstract leaves the queue automatically (its `has_abstract` becomes 1), and sources that missed stay logged, so a later session skips them.

## For each paper

### 1. Get the full record

```sh
sqlite3 -readonly archive/paperhunter.db \
  "SELECT paper_id, dblp_key, title, authors, year, venue, doi, arxiv_id, ee, url
   FROM paper_dblp WHERE paper_id = <ID>;"
```

`authors` and `ee` are JSON arrays. `ee` holds the electronic-edition links (publisher page, PDF, etc.); they are the best starting points.

### 2. Read the sources already tried

```sh
sqlite3 -readonly archive/paperhunter.db \
  "SELECT source, lookup_by FROM paper_abstract_miss WHERE paper_id = <ID>
   UNION ALL
   SELECT source, lookup_by FROM paper_abstract_hit WHERE paper_id = <ID>;"
```

Do not query any `source` (domain) that appears here.

### 3. Search the web

Use every detail from step 1: the DOI landing page, the arXiv ID, the `ee` and `url` links, the publisher's page, and a title + authors + venue + year search. Prefer primary sources: the publisher (`dl.acm.org`, `ieeexplore.ieee.org`, `link.springer.com`, `www.usenix.org`, `www.ndss-symposium.org`, `openreview.net`), `arxiv.org`, or the authors' own copy. Avoid aggregators that rephrase abstracts.

### 4. Verify before storing

Accept a text as the abstract only when **both** hold:

- **Right paper.** The page is about this exact paper: its title matches, and the authors and the venue or year agree.
- **Real abstract.** The text is the paper's own abstract, copied verbatim, not a summary, a citing paper's description, a blurb, a review, or an AI-generated gloss. It must be non-empty and not a placeholder such as "No abstract available".

If you cannot verify, treat the source as a miss.

### 5. Record the outcome (one transaction per paper)

Each source you tried becomes one row: a verified abstract goes to `paper_abstract_hit`, anything else to `paper_abstract_miss`. Put the relevant page text in `response` (the abstract itself for a hit; the page excerpt or "not found" note for a miss).

To keep arbitrary text (quotes, newlines, Unicode) safe, write each text value to a small file and load it with `readfile()`. For a paper where `www.example.org` yielded a verified abstract and `dl.acm.org` did not:

```sh
printf '%s' "$ABSTRACT_TEXT"      > /tmp/abs.txt
printf '%s' "$HIT_PAGE_EXCERPT"   > /tmp/hit_resp.txt
printf '%s' "$MISS_PAGE_EXCERPT"  > /tmp/miss_resp.txt

sqlite3 archive/paperhunter.db <<SQL
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

INSERT INTO paper_abstract_hit (paper_id, source, lookup_by, request, response)
VALUES (<ID>, 'www.example.org', 'web', 'https://www.example.org/paper/...', readfile('/tmp/hit_resp.txt'));

INSERT INTO paper_abstract_miss (paper_id, source, lookup_by, request, response)
VALUES (<ID>, 'dl.acm.org', 'web', 'https://dl.acm.org/doi/...', readfile('/tmp/miss_resp.txt'));

INSERT OR IGNORE INTO paper_abstract (paper_id, abstract, source)
VALUES (<ID>, readfile('/tmp/abs.txt'), 'https://www.example.org/paper/...');

UPDATE paper_abstract_status
SET hit_count  = hit_count  + 1,
    miss_count = miss_count + 1,
    has_abstract = 1
WHERE paper_id = <ID>;

COMMIT;
SQL
```

Adjust each run to the sources you actually tried:

- **`hit_count`** increases by the number of `paper_abstract_hit` rows you insert for this paper (0 or 1), and **`miss_count`** by the number of `paper_abstract_miss` rows. The counts must always equal the paper's row counts in the two log tables.
- Set **`has_abstract = 1`** only when a verified abstract was stored; otherwise leave it unchanged.
- The `source` column uses the bare domain; the `request` column stores the full URL of the page you read.
- `paper_abstract` holds at most one row per paper (`INSERT OR IGNORE` keeps the first). `source` there is the page URL the abstract came from.
- A paper may already carry earlier `openalex`/`s2` miss rows; leave them, and never reuse those source names.

## Finish

When the batch is done, re-read the queue and continue until it is empty or only papers remain whose every reachable source has been tried and missed.

**Afterwards:** rerun `paper-hunter/e_embed.py` and `paper-hunter/f_screen.py` so the newly found abstracts are re-embedded (switching those papers from the title basis to the abstract basis) and re-screened.
