# Master Prompt: Finish the Journal List (DBLP ↔ Scopus/SJR)

You are the reviewer. Complete this task end to end with no human help and no questions to the user.

The humans have already run `a-extract.py` and `b-match.py`, which loaded the database `x/archive/paperhunter.db`. Your job is to fill the `journal_decision` table until the view `journal_needs_review` is empty. The view `list_of_journals` is then the final list: every DBLP journal key (`journals/<name>`) that belongs to a Scopus Q1–Q4 computer-science journal, with zero false positives and zero false negatives.

Work from `/home/nishan/Documents/paperhunter-research-paper-discovery/x`. Use `sqlite3 archive/paperhunter.db` or Python's `sqlite3`. Start every connection with `PRAGMA foreign_keys = ON;`.

---

## Database

| object | kind | you |
|---|---|---|
| `sjr_journal(id, title, issn_1, issn_2, publisher, quartile)` | table, from Scopus (`id` = Sourceid) | read |
| `dblp_journal(dblp_key, papers, first_year, last_year)` | table, from DBLP | read |
| `dblp_journal_name(dblp_key, name, papers)` | table, from DBLP | read: how articles of a key name their journal (ISO-4 abbreviations) |
| `dblp_issn(dblp_key, issn, papers)` | table, from DBLP | read: checksum-valid ISSNs found in article links, the strongest evidence |
| `dblp_doi_prefix(dblp_key, prefix, papers)` | table, from DBLP | read: publisher of the articles (10.1145 ACM, 10.1109 IEEE, 10.1016 Elsevier, 10.1007 Springer, 10.1002 Wiley, 10.1080 Taylor & Francis, 10.3390 MDPI, 10.1142 World Scientific, 10.3233 IOS Press, 10.1504 Inderscience, 10.4018 IGI Global) |
| `journal_candidate(dblp_key, sjr_id, method, score, detail)` | table, from `b-match.py` | read: proposed pairs, not approved |
| `journal_decision(id, sjr_id, dblp_key, verdict, reason, decided_at)` | table | **write: the only thing you change** |
| `journal_evidence` | view | read: one summary row per key (top names, ISSNs, DOI prefixes) |
| `journal_needs_review` | view | read: your to-do list |
| `list_of_journals` | view | final output (empty until `journal_needs_review` is empty) |

Never modify any table other than `journal_decision`, and never alter the schema.

DBLP's XML has no ISSN field. `dblp_issn` holds only the ISSNs embedded in article links (old Elsevier DOIs and PIIs, Wiley SICI DOIs), so most keys have none. Absence of an ISSN proves nothing; a matching ISSN is near-proof.

## Procedure

1. `SELECT issue, count(*) FROM journal_needs_review GROUP BY issue;`. If it returns nothing, go to step 5.
2. Resolve every `INVALID` row by deleting the decision it names.
3. Resolve every `UNREVIEWED` row (step 1 of "How to decide").
4. Query `journal_needs_review` again. Accepting a key also covers its SJR journal, so the `UNCOVERED` list shrinks. Resolve the remaining `UNCOVERED` rows. Repeat steps 1–4 until `journal_needs_review` is empty.
5. Report in five lines or fewer:
   - `SELECT quartile, count(*) FROM list_of_journals GROUP BY quartile;`
   - SJR journals covered vs `absent`
   - number of decisions you added

On a first run `journal_decision` is empty, and every candidate and every SJR journal appears in `journal_needs_review`. Work through all of them. You may use scripts to read and filter, but every decision must rest on the evidence for that row.

## Writing decisions

```sql
INSERT INTO journal_decision (sjr_id, dblp_key, verdict, reason)
VALUES (12345, 'journals/csur', 'accept', 'ACM Comput. Surv. = ACM Computing Surveys, DOI prefix 10.1145');

INSERT INTO journal_decision (sjr_id, dblp_key, verdict, reason)
VALUES (23456, 'journals/ai', 'reject', 'Artificial Intelligence (Elsevier), not Artificial Intelligence in Medicine');

INSERT INTO journal_decision (sjr_id, dblp_key, verdict, reason)
VALUES (34567, NULL, 'absent', 'Not indexed in DBLP');
```

| verdict | `dblp_key` | meaning |
|---|---|---|
| `accept` | the key | the key is this SJR journal (one key per row; a journal may have several keys) |
| `reject` | the key | the proposed pair is a different journal |
| `absent` | `NULL` | this SJR journal has no DBLP key of its own |

- Take `sjr_id` from `journal_needs_review.sjr_id` or `sjr_journal.id`.
- `reason` is one line of evidence and is required.
- The database rejects invalid writes:
  - a key accepted for two journals
  - a journal both accepted and absent
  - duplicate pairs
  - an empty reason
  - an unknown key or journal

  If an insert fails, fix the decision; never work around the constraint.
- To correct a decision, `UPDATE` or `DELETE` it by `id`.

## How to decide

**`UNREVIEWED`** (`dblp_key` → `sjr_title`). Compare the row's `names`, `issns`, `doi_prefixes` and `detail` with `sjr_title`, `issn_1`, `issn_2` and `publisher`. If needed, open `https://dblp.org/db/journals/<name>/`, which shows the full title and ISSNs.
- `method = 'issn'`: accept when the shared ISSN carries most of the key's ISSN-bearing links and the names agree. A low `score` means only a few links carry that ISSN: check that it is not a sister journal or a cited article.
- `method = 'name'`: accept only if the DBLP name expands to the same title and the publisher (DOI prefix vs `publisher`) agrees. Renames count as the same journal, e.g. ACM TISSEC → ACM Transactions on Privacy and Security, J. Am. Soc. Inf. Sci. → JASIST.
- Otherwise `reject`. Typical cases:
  - a different journal whose title contains the DBLP name (`Artif. Intell.` vs `Artificial Intelligence in Medicine`; `Computers` vs `Computers and Security`)
  - a namesake from another publisher or country
  - sections or series of the same title with different ISSNs (`J. Comb. Theory A` vs `B`)
- A key with several candidates gets at most one `accept`; `reject` the rest.

**`UNCOVERED`** (SJR journal with no accepted key and not marked absent).
1. Search `dblp_issn.issn` for its ISSNs, and `dblp_journal_name.name` for its title, its ISO-4 abbreviation and former names, e.g. `WHERE name LIKE '%Geosci%'`. Then search dblp.org.
2. If you find its key, `accept` it. Add one row per key, including historic keys of renamed or merged predecessors.
3. If it has no DBLP key of its own, add an `absent` row with the reason, e.g. `Not indexed in DBLP`. Many SJR computer-science journals (applied, regional, medical, engineering) are not in DBLP; that is expected.

**`INVALID`.** A decision points to a key or journal that no longer exists, usually after a new DBLP dump or Scopus edition. Delete it; the affected pair or journal reappears as `UNREVIEWED` or `UNCOVERED` if it still needs a decision.

## Rules

- One DBLP key maps to at most one SJR journal.
- Never accept on a short or generic name alone; the publisher or an ISSN must agree.
- You are done only when `SELECT count(*) FROM journal_needs_review;` returns 0.
