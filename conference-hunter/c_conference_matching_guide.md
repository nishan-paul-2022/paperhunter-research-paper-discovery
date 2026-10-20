# Master Prompt: Finish the Conference List (DBLP ↔ CORE)

You are the reviewer. Complete this task end to end with no human help and no questions to the user.

**Goal.** Fill and audit the table `conf_decision` in `archive/paperhunter.db` so that the view `conf_list` contains **every** DBLP series that is a CORE A*–C conference, and **nothing else**. Zero false positives, zero false negatives.

**Ground rules.**
- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. Use `sqlite3 archive/paperhunter.db` or Python's `sqlite3`, and start every connection with `PRAGMA foreign_keys = ON;`.
- Change only `conf_decision`. Never alter the schema or any other table.
- Trust nothing in the database blindly. `conf_candidate` is a wide machine proposal: most pairs that share an acronym are namesakes, and some true pairs are still never proposed. Existing `conf_decision` rows may be wrong or incomplete. You audit both.
- You may use scripts to read, filter and batch-insert, but every single decision must rest on evidence you looked at for that pair. Never accept or reject by a score, a pattern or a rule alone.

---

## 1. The data

A **DBLP series** is one row of `conf_dblp_venue`. There are three kinds:

| `dblp_key` | what it is | example |
|---|---|---|
| `conf/<name>` | a DBLP conference key with all its `inproceedings` | `conf/icse` |
| `journals/<name>#<LABEL>` | the issues of a journal whose DBLP `number` names a conference (trailing digits dropped: `OOPSLA1`, `OOPSLA2` → `OOPSLA`) | `journals/pacmpl#POPL`, `journals/pacmhci#CSCW`, `journals/pacmnet#CoNEXT` |
| `journals/<name>` | a whole journal | `journals/pvldb` |

| object | what it holds |
|---|---|
| `conf_core_venue(id, title, acronym, rank)` | CORE 2026 venues, rank A*/A/B/C. The title often names former names and mergers in brackets: "(was WICSA)", "(merger of DALT, AOSE and PROMAS)". These are authoritative predecessor hints. |
| `conf_dblp_venue(dblp_key, acronym, papers, first_year, last_year)` | one row per series. `acronym` is the key name or the issue label; `papers` and years cover its papers |
| `conf_dblp_booktitle(dblp_key, booktitle, papers, first_year, last_year)` | how the series' papers name their venue (the journal name for journal series), with counts and year span. `X@Y` is a workshop X held at Y; `Y (Findings)`, `Y Companion`, `Y Workshops` are sub-volumes |
| `conf_dblp_title(dblp_key, proc_key, year, title, publisher, papers)` | every proceedings volume of a `conf/` key: its own DBLP key (`conf/cvpr/2024w`), full title, publisher, and how many papers it holds. The strongest evidence. `year` is the volume's publication year (it can trail the event by a year; 0 means missing) |
| `conf_candidate(dblp_key, core_id, score, proceedings_title)` | proposals from `b_match.py`. Every series whose name, booktitle, journal name or edition acronym equals the CORE acronym or a former acronym from the CORE title. `score` is the share of CORE title words found in the best proceedings title or booktitle, which is stored in `proceedings_title`. The score is a hint only: a low score does not make a reject, and a high one does not make an accept |
| `conf_decision(id, core_id, dblp_key, verdict, reason, decided_at)` | **your output** |
| `conf_summary` | one summary row per series: acronym, years, number of volumes, top 5 booktitles with year spans, the 3 newest proceedings titles with paper counts |
| `conf_review` | to-do list: `UNREVIEWED` candidates, `UNCOVERED` CORE venues, `INVALID` decisions |
| `conf_list` | final output; it stays empty while `conf_review` has any row |

Look at a series' whole history before deciding, not just `conf_summary`:

```sql
SELECT year, proc_key, title, publisher, papers FROM conf_dblp_title WHERE dblp_key = 'conf/x' ORDER BY year;
SELECT booktitle, papers, first_year, last_year FROM conf_dblp_booktitle WHERE dblp_key = 'conf/x' ORDER BY papers DESC;
SELECT * FROM conf_dblp_venue WHERE dblp_key = 'conf/x';
```

Then check the web, which is authoritative and settles most doubts:
- DBLP venue page `https://dblp.org/db/conf/<name>/` (or `/db/journals/<name>/`) gives the series' full name and often links its predecessor or successor ("continued as", "renamed").
- DBLP venue search `https://dblp.org/search/venue/api?q=<words>&format=json&h=100` lists every DBLP venue whose name matches, with its key in `url`. Send at most one request per second.
- The CORE portal and the conference's own website.

If `conf_dblp_venue` has no `acronym` column or `conf_dblp_title` has no `proc_key` column, stop and tell the humans to run `circumstance/b_schema.py` and then `conference-hunter/a_extract.py`.

---

## 2. When a series belongs to a CORE venue

The unit is the **whole series**. Accept series `S` for venue `V` only when `S` is `V`'s own proceedings, meaning the bulk of `S`'s volumes or issues are proceedings of `V`'s own event under its current or a former name.

**Accept**
- The proceedings titles name `V` itself.
- A predecessor or successor series of `V` (rename, merger, takeover). It must be backed by at least one of:
  - the CORE title (`was`, `previously`, `merger of`, `combined`);
  - the DBLP venue page linking the two;
  - continuous edition numbering ("15th" follows "14th") or an explicit statement on the conference site.

  Examples: NIPS→NeurIPS, WCRE→SANER, ICSM→ICSME, ICMAS/Agents/ATAL→AAMAS, WICSA→ICSA, TPHOLs→ITP.
- A **new key for the same series**. DBLP sometimes starts a fresh key (`conf/atal`→`conf/ifaamas` for AAMAS 2025+, `conf/alife`→`conf/isalalife` for ALIFE 2018+). A venue is not finished when its first series is found.
- A **journal series that is `V`'s proceedings**. Since some year, many conferences publish their main track in a journal:
  - **Issue streams** — accept these, not the whole journal:
    - `journals/pacmpl#POPL`, `#PLDI`, `#OOPSLA`, `#ICFP`
    - `journals/pacmse#FSE`, `#ISSTA`
    - `journals/pacmhci#CSCW`, `#MHCI`, `#CHI PLAY`, …
    - `journals/pacmnet#CoNEXT`
  - **Whole journals that are entirely one conference's proceedings:** `journals/pvldb` (VLDB), `journals/popets` (PETS), `journals/tches` (CHES), `journals/tosc` (FSE crypto), `journals/pomacs` (SIGMETRICS). Verify each on the DBLP journal page.
- A key that holds a **track of `V`'s own event** split out by DBLP. Example: `conf/icse-seip`, whose title is "International Conference on Software Engineering: Software Engineering in Practice". The main key already counts the same tracks in other years.
- `V`'s companion, Findings, workshop or poster volumes **inside `V`'s own key**. These count with the key; you cannot split a series.

**Reject**
- **Namesakes**: another conference with the same acronym. Decide by full title and publisher, never by acronym.
- **Separate events held with `V`**, even when the title mentions `V`. This covers co-located workshops and symposia with their own name and edition count (`conf/forge` and `conf/mobilesoft` at ICSE; `conf/ecom` "co-located with SIGIR").
- **Regional or national spin-offs**: `conf/sigir-ap` (SIGIR Asia-Pacific), `conf/dcscw` (German CSCW).
- **Umbrella or federated keys** whose volumes are mostly other venues: `conf/visigrapp`, and the Springer SCF series (`conf/icws2`, `conf/cloud2`, `conf/bigdata2`, `conf/services2`).
- **Keys where `V` is only a minority partner**, joint for a few years: `conf/ram` (CIS joint only since 2015), `conf/imagapp`.
- **Workshop post-proceedings** of `V`: `conf/aamas` holds the ALAMAS workshop volumes, not AAMAS itself.
- **Mixed journals**: a whole journal that also publishes regular papers or several conferences, such as:
  - `journals/pacmpl` (several conferences);
  - `journals/pacmmod` (SIGMOD and PODS share numbered issues);
  - `journals/tog` (SIGGRAPH plus regular papers);
  - `journals/tvcg` (IEEE VIS, VR plus regular papers);
  - `journals/cgf` (EuroVis, SGP plus regular papers);
  - `journals/bioinformatics` (ISMB plus regular papers).

  Never accept a mixed journal. Tag the affected venue instead (§3).

**One series → at most one venue.** If a series plausibly fits two CORE venues, accept the one it actually is and reject the other with the reason.

**`absent`** means `V` has **no DBLP series of its own**. Use it only after the search in §4 step 3 finds nothing. If `V`'s papers sit inside another venue's key, it is still `absent`, and the reason must point there: `papers indexed inside conf/ic3k (booktitle KEOD)`.

---

## 3. Writing decisions

```sql
INSERT INTO conf_decision (core_id, dblp_key, verdict, reason) VALUES
 ((SELECT id FROM conf_core_venue WHERE title = 'Selected Areas in Cryptography'), 'conf/sacrypt', 'accept',
  'Proceedings 1994-2025 "Selected Areas in Cryptography", Springer'),
 ((SELECT id FROM conf_core_venue WHERE title = 'Selected Areas in Cryptography'), 'conf/sac', 'reject',
  'ACM/SIGAPP Symposium on Applied Computing, namesake'),
 ((SELECT id FROM conf_core_venue WHERE title = 'Simulation and Synthesis in Medical Imaging'), NULL, 'absent',
  'No series of its own; papers inside conf/miccai (SASHIMI@MICCAI); DBLP venue search: no hit');
```

| verdict | `dblp_key` | meaning |
|---|---|---|
| `accept` | the series | the series is this venue's (a venue may have several series) |
| `reject` | the series | the series is not this venue. Record every pair you examined and turned down, so later runs do not re-examine it |
| `absent` | `NULL` | the venue has no DBLP series of its own |

- Refer to venues by `id` from `conf_review.core_id`, or by title sub-select as above. Titles are unique; ids are not stable across CORE reloads.
- `reason` is one line of concrete evidence: the proceedings name and year span, the publisher, and the predecessor proof. "Proceedings" alone is not a reason.
- **Reason tags** on the newest accepted series of a venue, appended with `UPDATE conf_decision SET reason = reason || '; …' WHERE id = ?`:
  - `JOURNAL: since <year> in journals/<key>`: the main track moved into a mixed journal that cannot be accepted (§2).
  - `ENDED: last DBLP edition <year>`: you confirmed there is no later series anywhere. The series stopped, or DBLP stopped indexing it.
- The database refuses:
  - a series accepted for two venues;
  - a venue both accepted and absent;
  - duplicate pairs;
  - an empty reason;
  - an unknown series or venue.

  If an insert fails, your decision is wrong somewhere: fix it, never work around the constraint.
- Correct a decision with `UPDATE` or `DELETE` by `id`. Write in transactions, and re-read what you wrote.

---

## 4. Procedure

**Step 0. Preflight.**

```sql
SELECT issue, count(*) FROM conf_review GROUP BY issue;
SELECT verdict, count(*) FROM conf_decision GROUP BY verdict;
```

On a first run every candidate and every CORE venue is open. On a rerun after a new DBLP dump, CORE edition or matcher change, only the new pairs are open, but the audits in steps 4–7 always run in full.

**Step 1. `INVALID`.** Delete each named decision. The pair or venue reappears as `UNREVIEWED` or `UNCOVERED` if it still needs a decision.

**Step 2. `UNREVIEWED`** (`dblp_key` → `core_title`). Apply §2 to the series' full history and write `accept` or `reject`. A series with several candidates gets at most one `accept`. Expect most of these rows to be namesakes.

**Step 3. `UNCOVERED`** (no accepted series and no `absent` row). Run all of these searches before concluding anything:
1. The acronym, every former acronym and every former name from the CORE title, in series names, booktitles and proceedings titles:

   ```sql
   SELECT dblp_key, acronym, papers, first_year, last_year FROM conf_dblp_venue WHERE acronym LIKE '%abc%';
   SELECT dblp_key, booktitle, papers, first_year, last_year FROM conf_dblp_booktitle WHERE booktitle LIKE '%ABC%';
   SELECT dblp_key, year, title, publisher FROM conf_dblp_title WHERE title LIKE '%Distinctive Words%';
   ```
2. Distinctive words of the full title, which catches venues that DBLP files under an unexpected key (`conf/uss` for USENIX Security, `conf/mm` for ACM Multimedia, `conf/si3d` for I3D).
3. The journal issue streams: `SELECT * FROM conf_dblp_venue WHERE dblp_key LIKE 'journals/%#%'`.
4. The DBLP venue search API and the conference website.

Accept every series you find (§2); reject the near misses you examined. Only if nothing is found, write `absent` and list what you searched in the reason.

**Step 4. Sweep for missed series (must end at 0 rows).** These are undecided series whose acronym, or whose non-workshop booktitle, equals a CORE acronym. Most rows are namesakes: write a `reject` for each. Real ones get an `accept`.

```sql
WITH bt AS (
  SELECT dblp_key, lower(trim(CASE WHEN instr(booktitle, ' (') > 0
           THEN substr(booktitle, 1, instr(booktitle, ' (') - 1) ELSE booktitle END)) AS b
  FROM conf_dblp_booktitle WHERE booktitle NOT LIKE '%@%'
), hit AS (
  SELECT v.id AS core_id, k.dblp_key FROM conf_core_venue v JOIN conf_dblp_venue k ON lower(k.acronym) = lower(v.acronym)
  UNION
  SELECT v.id, bt.dblp_key FROM conf_core_venue v JOIN bt ON bt.b = lower(v.acronym)
)
SELECT h.core_id, v.rank, v.acronym, v.title, h.dblp_key, k.papers, k.first_year, k.last_year, e.proceedings_titles
FROM hit h JOIN conf_core_venue v ON v.id = h.core_id JOIN conf_dblp_venue k ON k.dblp_key = h.dblp_key
JOIN conf_summary e ON e.dblp_key = h.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM conf_decision d WHERE d.dblp_key = h.dblp_key
                  AND (d.core_id = h.core_id OR d.verdict = 'accept'))
ORDER BY v.rank, v.acronym;
```

**Step 5. Sweep for stale venues (must end at 0 rows).** These are venues whose newest accepted series ends more than two years ago and carries no tag yet. For each one, find where the venue went:
- a new key (the Step 3 searches, then the DBLP venue page of the old key);
- a journal issue stream or a whole journal;
- a mixed journal;
- or nowhere.

Then accept the new series, or tag the newest accepted series with `JOURNAL:` or `ENDED:`.

```sql
SELECT v.id, v.rank, v.acronym, v.title,
       group_concat(d.dblp_key || ' ' || k.first_year || '-' || k.last_year, ', ') AS series,
       max(k.last_year) AS newest
FROM conf_core_venue v
JOIN conf_decision d ON d.core_id = v.id AND d.verdict = 'accept'
JOIN conf_dblp_venue k ON k.dblp_key = d.dblp_key
GROUP BY v.id
HAVING newest < CAST(strftime('%Y', 'now') AS INTEGER) - 2
   AND sum(d.reason LIKE '%JOURNAL:%' OR d.reason LIKE '%ENDED:%') = 0
ORDER BY CASE v.rank WHEN 'A*' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, newest;
```

A venue can also stay active in DBLP while its main track left: its newest booktitles are only `Companion`, `Abstracts`, `Workshops`, `Posters` or `X@Y`. Check that with `conf_dblp_booktitle` year spans and `conf_dblp_title` paper counts. Find where the main track went, then accept its journal series or add the `JOURNAL:` tag.

**Step 6. Audit accepts the matcher never proposed.** These were found by hand, so they carry the highest false-positive risk. Re-check each against §2, especially the predecessor proof. Fix any wrong one.

```sql
SELECT d.id, v.acronym, v.title, d.dblp_key, d.reason, e.booktitles, e.proceedings_titles
FROM conf_decision d JOIN conf_core_venue v ON v.id = d.core_id JOIN conf_summary e ON e.dblp_key = d.dblp_key
WHERE d.verdict = 'accept'
  AND NOT EXISTS (SELECT 1 FROM conf_candidate c WHERE c.dblp_key = d.dblp_key AND c.core_id = d.core_id);
```

**Step 7. Audit every `absent`.** Re-run the Step 3 searches, including the journal streams and the DBLP venue search API, for each `absent` venue. DBLP adds venues over time.

**Step 8. Final gate.** All three must hold; otherwise go back to step 1.
- `SELECT count(*) FROM conf_review;` returns 0.
- The Step 4 query returns 0 rows.
- The Step 5 query returns 0 rows.

**Step 9. Report** (ten lines at most):
- `SELECT ranking, count(*) FROM conf_list GROUP BY ranking;`
- CORE venues covered vs `absent`, out of all CORE venues;
- decisions added, changed and deleted in this run;
- every `accept` you reversed, or added during the audits (Steps 4–7), as series → venue;
- the journal series you accepted, and the venues tagged `JOURNAL:`.

---

## 5. Traps already seen in this data

| looks like | actually | the CORE venue is at |
|---|---|---|
| `conf/sac` | ACM Symposium on Applied Computing | Selected Areas in Cryptography → `conf/sacrypt` |
| `conf/fse` | Fast Software Encryption (CORE B "FSE") | ACM FSE (A*) → `conf/sigsoft`, `conf/esec`, `journals/pacmse#FSE` |
| `conf/iswc` | Int. Symposium on Wearable Computers | Semantic Web Conference → `conf/semweb` |
| `conf/pact` | Parallel Computing Technologies (PaCT) | PACT → `conf/IEEEpact` |
| `conf/iv` / `conf/ivs` | Information Visualisation / IEEE Intelligent Vehicles | CORE "IV" (C) / "IEEE-IV" (B) |
| `conf/cloud`, `conf/bigdata` | ACM SoCC / IEEE BigData Congress | IEEE CLOUD → `conf/IEEEcloud`; IEEE BigData → `conf/bigdataconf` |
| `conf/chase`, `conf/ismm`, `conf/idc`, `conf/icis` | Connected Health; Mathematical Morphology; Intelligent Distributed Computing; AIS Information Systems | `conf/icse-chase`, `conf/iwmm`, `conf/acmidc`, `conf/ACISicis` |
| `conf/gi`, `conf/ec`, `conf/aamas` | German GI Jahrestagung; 1994 e-commerce workshop; ALAMAS workshop volumes | `conf/graphicsinterface`, `conf/sigecom`, `conf/atal` + `conf/ifaamas` |
| duplicate CORE acronyms | FSE, GI, ICCSA, ICEC, ISC each name two CORE venues | decide by full title |
| CORE acronyms written differently in DBLP | ACMMM, USENIX-Security, I3DG, HOTCHIPS (HCS), IJCRS (was RSCTC) | `conf/mm`, `conf/uss`, `conf/si3d`, `conf/hotchips`, `conf/rsctc` |

## 6. Hard rules

- One series maps to at most one CORE venue; one venue may have many series. Find them all.
- Never accept on acronym alone: the proceedings must name the same series, or a proven predecessor of it.
- Never accept a mixed journal; never write `absent` without running every search in Step 3.
- Every pair you examine gets a written verdict.
- You are done only when the three checks in Step 8 hold.
