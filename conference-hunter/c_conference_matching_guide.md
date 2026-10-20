# Master Prompt: Finish the Conference List (DBLP ↔ CORE)

You are the reviewer. Complete this task end to end with no human help and no questions to the user.

The humans have already run `a_extract.py` and `b_match.py`, which loaded the database `archive/paperhunter.db`. Your job is to fill the `conf_decision` table until the view `conf_needs_review` is empty. The view `list_of_conferences` is then the final list: every DBLP conference key (`conf/<name>`) that belongs to a CORE A*–C venue, with zero false positives and zero false negatives.

Work from `/home/nishan/Documents/paperhunter-research-paper-discovery/x`. Use `sqlite3 archive/paperhunter.db` or Python's `sqlite3`. Start every connection with `PRAGMA foreign_keys = ON;`.

---

## Database

| object | kind | you |
|---|---|---|
| `core_venue(id, title, acronym, rank)` | table, from CORE | read |
| `dblp_venue(dblp_key, acronym, papers, first_year, last_year)` | table, from DBLP | read |
| `dblp_booktitle(dblp_key, booktitle, papers)` | table, from DBLP | read: how papers of a key name their venue |
| `dblp_proceedings(dblp_key, year, title)` | table, from DBLP | read: full proceedings titles, the strongest evidence |
| `conf_candidate(dblp_key, core_id, score, proceedings_title)` | table, from `b_match.py` | read: proposed pairs, not approved |
| `conf_decision(id, core_id, dblp_key, verdict, reason, decided_at)` | table | **write: the only thing you change** |
| `venue_evidence` | view | read: one summary row per key (top booktitles, latest proceedings) |
| `conf_needs_review` | view | read: your to-do list |
| `list_of_conferences` | view | final output (empty until `conf_needs_review` is empty) |

Never modify any table other than `conf_decision`, and never alter the schema.

## Procedure

1. `SELECT issue, count(*) FROM conf_needs_review GROUP BY issue;`. If it returns nothing, go to step 5.
2. Resolve every `INVALID` row by deleting the decision it names.
3. Resolve every `UNREVIEWED` row (step 1 of "How to decide").
4. Query `conf_needs_review` again. Accepting a key also covers its CORE venue, so the `UNCOVERED` list shrinks. Resolve the remaining `UNCOVERED` rows. Repeat steps 1–4 until `conf_needs_review` is empty.
5. Report in five lines or fewer:
   - `SELECT ranking, count(*) FROM list_of_conferences GROUP BY ranking;`
   - CORE venues covered vs `absent`
   - number of decisions you added

On a first run `conf_decision` is empty, and every candidate and every CORE venue appears in `conf_needs_review` (about 1,600 rows). Work through all of them. You may use scripts to read and filter, but every decision must rest on the evidence for that row.

## Writing decisions

```sql
INSERT INTO conf_decision (core_id, dblp_key, verdict, reason)
VALUES (405, 'conf/sacrypt', 'accept', 'Selected Areas in Cryptography proceedings');

INSERT INTO conf_decision (core_id, dblp_key, verdict, reason)
VALUES (405, 'conf/sac', 'reject', 'ACM Symposium on Applied Computing, not SAC crypto');

INSERT INTO conf_decision (core_id, dblp_key, verdict, reason)
VALUES (799, NULL, 'absent', 'Not indexed in DBLP');
```

| verdict | `dblp_key` | meaning |
|---|---|---|
| `accept` | the key | the key is this CORE venue (one key per row; a venue may have several keys) |
| `reject` | the key | the proposed pair is a different venue |
| `absent` | `NULL` | this CORE venue has no DBLP series of its own |

- Take `core_id` from `conf_needs_review.core_id` or `core_venue.id`.
- `reason` is one line of evidence and is required.
- The database rejects invalid writes:
  - a key accepted for two venues
  - a venue both accepted and absent
  - duplicate pairs
  - an empty reason
  - an unknown key or venue

  If an insert fails, fix the decision; never work around the constraint.
- To correct a decision, `UPDATE` or `DELETE` it by `id`.

## How to decide

**`UNREVIEWED`** (`dblp_key` → `core_title`). Compare the row's `proceedings_titles`, `booktitles` and `detail` (the matched proceedings title) with `core_title`. If needed, query all proceedings of the key (`SELECT year, title FROM dblp_proceedings WHERE dblp_key = ? ORDER BY year`) or open `https://dblp.org/db/conf/<name>/`.
- `accept` only if the proceedings name the same conference series as the CORE title. Renames and mergers count as the same series, e.g. NIPS→NeurIPS, WCRE→SANER, ATAL→AAMAS, ICSM→ICSME.
- Otherwise `reject`. Typical cases:
  - a different venue sharing the acronym (`conf/sac` is ACM Symposium on Applied Computing, not Selected Areas in Cryptography; `conf/fse` is Fast Software Encryption, not ACM FSE)
  - a workshop co-located with the venue
  - a regional or national namesake
- A key with several candidates gets at most one `accept`; `reject` the rest.

**`UNCOVERED`** (CORE venue with no accepted key and not marked absent).
1. Search `dblp_proceedings.title` and `dblp_booktitle.booktitle` for its full name, former names (often given in brackets in the CORE title) and acronym, e.g. `WHERE title LIKE '%Semantic Web%'`. Then search dblp.org.
2. If you find its key, `accept` it. Add one row per key, including historic keys of renamed or merged predecessors.
3. If it has no DBLP series of its own, add an `absent` row with the reason, e.g. `Not indexed in DBLP` or `papers indexed inside conf/ic3k`.

**`INVALID`.** A decision points to a key or venue that no longer exists, usually after a new DBLP dump or CORE edition. Delete it; the affected pair or venue reappears as `UNREVIEWED` or `UNCOVERED` if it still needs a decision.

## Rules

- One DBLP key maps to at most one CORE venue.
- Never accept on acronym alone; the proceedings titles must name the same series.
- You are done only when `SELECT count(*) FROM conf_needs_review;` returns 0.
