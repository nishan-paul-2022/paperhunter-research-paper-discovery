# Master Prompt: Finish the Conference List (DBLP ↔ CORE)

You are the reviewer. Complete this task end to end with no human help and no questions to the user. You are done when `c-merge.py` writes `x/archive/list-of-conferences.csv`: every DBLP conference key (`conf/<name>`) that belongs to a CORE A*–C venue, with zero false positives and zero false negatives.

Work from `/home/nishan/Documents/paperhunter-research-paper-discovery`.

---

## Files

| file | made by | you |
|---|---|---|
| `x/archive/paper-dblp.xml`, `x/archive/publication-core-2026.csv` | inputs | read only |
| `x/conference-supplement/1-dblp-venues.csv` | `a-extract.py` | read: evidence for every DBLP conference key (papers, years, booktitles, proceedings titles) |
| `x/conference-supplement/2-exact-matched.csv` | `a-extract.py` | read: proposed pairs (acronym and proceedings title agree), not yet approved |
| `x/conference-supplement/3-dblp-unmatched.csv` | `a-extract.py` | read: keys with no proposal |
| `x/conference-supplement/4-source-pool.csv` | `a-extract.py` | read: CORE venues with no proposal |
| `x/conference-supplement/5-fuzzy-matched.csv` | `b-match.py` | read: every key with its status |
| `x/conference-supplement/6-needs-review.csv` | `b-match.py` | read: **your to-do list** |
| `x/conference-supplement/7-decisions.csv` | **you** | write: your decisions; no script ever writes it |
| `x/archive/list-of-conferences.csv` | `c-merge.py` | final output |

Never edit files `1-` to `6-` or `list-of-conferences.csv`; every run overwrites them. Never edit the Python scripts.

## Procedure

1. Run `python3 x/conference-hunter/a-extract.py --reuse-index` if `1-dblp-venues.csv` exists, otherwise without the flag (full XML scan, ~15–20 min). Then run `python3 x/conference-hunter/b-match.py`.
2. If `b-match.py` prints `✓ All candidates reviewed and every CORE venue accounted for`, go to step 6.
3. Resolve every `UNREVIEWED` and `ERROR` row of `6-needs-review.csv` by adding or fixing rows in `7-decisions.csv`.
4. Run `b-match.py` again. Accepting a key also covers its CORE venue, so the `UNCOVERED` list shrinks. Resolve the remaining `UNCOVERED` rows.
5. Repeat steps 3–4 until `b-match.py` prints ✓.
6. Run `python3 x/conference-hunter/c-merge.py`. It must print `✅ Done!`. If it prints `✗ Validation failed`, fix `7-decisions.csv` and go back to step 4.
7. Report in five lines or fewer: number of keys per rank, CORE venues covered vs absent, and the decisions you added this run.

On a normal rerun `7-decisions.csv` already holds earlier decisions, and only new or changed cases appear. If it is missing or empty, every candidate and every CORE venue (about 1,600 rows) appears. Work through all of them; you may use scripts to read and filter, but every decision must rest on the evidence for that row.

## `7-decisions.csv` format

Columns: `dblp_key,core_title,decision,reason`

| decision | `dblp_key` | meaning |
|---|---|---|
| `accept` | the key | the key is this CORE venue (one key per row; a venue may have several keys) |
| `reject` | the key | the proposed pair is a different venue |
| `absent` | empty | this CORE venue has no DBLP series of its own |

- `core_title` must be copied exactly from the `Title` column of `publication-core-2026.csv`.
- `reason` is one line of evidence and is required, e.g. `Selected Areas in Cryptography proceedings`.
- Append new rows. Don't delete existing rows unless the evidence proves them wrong.

## How to decide

**`UNREVIEWED`** (`dblp_key` → `core_title`). Compare the row's `proceedings_titles` and `booktitles` with the CORE title. If unclear, open `https://dblp.org/db/conf/<name>/`.
- `accept` only if the proceedings name the same conference series as the CORE title. Renames and mergers count as the same series, e.g. NIPS→NeurIPS, WCRE→SANER, ATAL→AAMAS, ICSM→ICSME.
- Otherwise `reject`. Typical cases:
  - a different venue sharing the acronym (`conf/sac` is ACM Symposium on Applied Computing, not Selected Areas in Cryptography; `conf/fse` is Fast Software Encryption, not ACM FSE)
  - a workshop co-located with the venue
  - a regional or national namesake
  - a journal-like or SCF series

**`UNCOVERED`** (CORE venue with no accepted key).
1. Search the `proceedings_titles` and `booktitles` columns of `1-dblp-venues.csv` for its full name, former names (often given in brackets in the CORE title) and acronym. Then search dblp.org.
2. If you find its key, add `accept` rows: one per key, including historic keys of renamed or merged predecessors.
3. If it has no DBLP series of its own, add an `absent` row with the reason, e.g. `Not indexed in DBLP` or `papers indexed inside conf/ic3k`.

**`ERROR`.** The row names the bad line of `7-decisions.csv`: unknown title, missing reason, unknown decision, duplicate, a key accepted for two venues, or a venue both absent and accepted. Fix that line.

## Rules

- One DBLP key maps to at most one CORE venue.
- Never accept on acronym alone; the proceedings titles must name the same series.
- Every row in `6-needs-review.csv` must be resolved. `c-merge.py` refuses to write the list otherwise. Never bypass it.
