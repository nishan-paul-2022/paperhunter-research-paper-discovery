# Master Prompt: D · Find Missing Abstracts (Paper Hunter)

You are an autonomous research assistant with shell and web access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Move every paper at status `C` to status `D`. Papers that already have an abstract move at once. For each paper still without one, search the web for its own abstract and store it if found.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Use `.venv/bin/python`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_field_abstract.abstract` and `paper_status.status`. Never change the schema or any other table.
- **Accuracy over coverage.** Store an abstract only when you found it on a trustworthy page and verified it belongs to this paper (same title, same authors). Copy it exactly. Never guess, paraphrase, translate, summarize or generate one. A missing abstract is a correct outcome; a wrong one is not.
- **Do not use** OpenAlex, Semantic Scholar or arXiv: step C already tried them.
- Write each paper's abstract and its status change in **one transaction**.

## Step 1: Move papers that already have an abstract

```sh
sqlite3 archive/paperhunter.db "
PRAGMA foreign_keys = ON;
UPDATE paper_status SET status = 'D'
WHERE status = 'C' AND paper_id NOT IN (SELECT paper_id FROM paper_review);"
```

## Step 2: Search the rest, one paper at a time

Read a batch of 20 papers at status `C` that are in `paper_review`:

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT paper_id, title, authors, year, venue, doi, ee, arxiv_id
FROM paper_summary
WHERE status = 'C' AND paper_id IN (SELECT paper_id FROM paper_review)
ORDER BY paper_id LIMIT 20;"
```

`authors` and `ee` are JSON arrays. `ee` holds the paper's own links (publisher page, PDF); start there.

For each paper:

1. Open the `ee` links and the DOI page (`https://doi.org/<doi>`). If none shows the abstract, run **one** web search for the exact title in quotes.
2. Accept a page only if its title matches the paper's title (allowing punctuation and casing) and at least one author matches.
3. Record the result. Use the found abstract, or `None` if there is none:

```sh
.venv/bin/python - <<'PY'
import sqlite3
paper_id = 123456
abstract = None
conn = sqlite3.connect("archive/paperhunter.db", timeout=120)
conn.execute("PRAGMA foreign_keys = ON")
with conn:
    if abstract:
        conn.execute("UPDATE paper_field_abstract SET abstract = ? WHERE paper_id = ? AND abstract IS NULL",
                     (" ".join(abstract.split()), paper_id))
    conn.execute("UPDATE paper_status SET status = 'D' WHERE paper_id = ? AND status = 'C'", (paper_id,))
print("recorded", paper_id)
PY
```

Repeat batches until the query returns nothing. An interrupted session resumes where it stopped, because finished papers are already at `D`.

## Finish

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT status, count(*) FROM paper_status GROUP BY status;
SELECT 'still without abstract', count(*) FROM paper_review;"
```

**Next:** run `paper-hunter/e_embed.py`.
