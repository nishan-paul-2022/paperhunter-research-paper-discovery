# Master Prompt: Finish the Conference List (DBLP ↔ CORE)

You are the reviewer. Complete this task end to end with no human help and no questions to the user.

**Goal.** Fill and audit the table `conf_decision` in `archive/paperhunter.db` so that the view `list_of_conferences` contains **every** DBLP conference key (`conf/<name>`) that is the proceedings series of a CORE A*–C venue, and **nothing else**. Zero false positives, zero false negatives.

**Why it matters.** `list_of_conferences` feeds `paper_inventory`: every DBLP paper under an accepted key, published 2023–2026, is labelled with that venue's CORE rank. A wrong accept puts foreign papers under an A* label; a missed key silently loses a venue's papers. Keys active in 2023 or later deserve the most care.

**Ground rules.**
- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. Use `sqlite3 archive/paperhunter.db` or Python's `sqlite3`, and start every connection with `PRAGMA foreign_keys = ON;`.
- Change only `conf_decision`. Never alter the schema or any other table.
- Trust nothing in the database blindly: `conf_candidate` is a machine proposal (about 5% of its pairs are wrong, and about 10% of the true pairs are never proposed), and existing `conf_decision` rows may be wrong or incomplete. You audit both.
- You may use scripts to read, filter and batch-insert, but every single decision must rest on evidence you looked at for that pair. Never accept or reject by a score, a pattern or a rule alone.

---

## 1. The data

| object | what it holds |
|---|---|
| `core_venue(id, title, acronym, rank)` | CORE 2026 venues, rank A*/A/B/C. The title often names former names and mergers in brackets: "(was WICSA)", "(merger of DALT, AOSE and PROMAS)". These are authoritative predecessor hints. |
| `dblp_venue(dblp_key, papers, first_year, last_year)` | one row per DBLP conference key; `papers` counts `inproceedings` records, years span those papers |
| `dblp_booktitle(dblp_key, booktitle, papers)` | every `booktitle` the key's papers use, with counts. `X@Y` means a workshop X held at Y; `Y (Findings)`, `Y Companion`, `Y Workshops` are sub-volumes |
| `dblp_proceedings(dblp_key, year, title, publisher)` | full title of **every** proceedings volume of the key. The strongest evidence. `year` is the volume's publication year (it can trail the event by a year; 0 means missing). `publisher` (ACM, IEEE, Springer, LIPIcs, …) separates namesakes |
| `conf_candidate(dblp_key, core_id, score, proceedings_title)` | proposals from `b_match.py`: key's acronym matches, and ≥60% of the CORE title's words appear in one of the key's 3 newest proceedings titles |
| `conf_decision(id, core_id, dblp_key, verdict, reason, decided_at)` | **your output** |
| `venue_evidence` | one summary row per key: `acronym`, years, top 5 booktitles, **only the 3 newest** proceedings titles |
| `conf_needs_review` | to-do list: `UNREVIEWED` candidates, `UNCOVERED` CORE venues, `INVALID` decisions |
| `list_of_conferences` | final output; it stays empty while `conf_needs_review` has any row |

Look at a key's whole history before deciding, not just `venue_evidence`:

```sql
SELECT year, title, publisher FROM dblp_proceedings WHERE dblp_key = 'conf/x' ORDER BY year;
SELECT booktitle, papers FROM dblp_booktitle WHERE dblp_key = 'conf/x' ORDER BY papers DESC;
SELECT * FROM dblp_venue WHERE dblp_key = 'conf/x';
```

Then check the web, which is authoritative and settles most doubts:
- DBLP venue page `https://dblp.org/db/conf/<name>/` gives the series' full name and often links its predecessor or successor ("continued as", "renamed").
- DBLP venue search `https://dblp.org/search/venue/api?q=<words>&format=json&h=100` lists every DBLP venue whose name matches, with its key in `url`. Send at most one request per second.
- The CORE portal and the conference's own website.

If `dblp_proceedings` has no `publisher` column, stop and tell the humans to run `circumstance/b_schema.py` and then `conference-hunter/a_extract.py`.

---

## 2. When a key belongs to a CORE venue

The unit is the **whole DBLP key**. Accept `K` for venue `V` only when `K` is `V`'s own proceedings series, meaning the bulk of `K`'s volumes are proceedings of `V`'s own event under its current or a former name.

**Accept**
- The proceedings titles name `V` itself.
- A predecessor or successor series of `V` (rename, merger, takeover). It must be backed by at least one of:
  - the CORE title (`was`, `previously`, `merger of`, `combined`);
  - the DBLP venue page linking the two;
  - continuous edition numbering ("15th" follows "14th") or an explicit statement on the conference site.

  Examples: NIPS→NeurIPS, WCRE→SANER, ICSM→ICSME, ICMAS/Agents/ATAL→AAMAS, WICSA→ICSA, TPHOLs→ITP.
- A **new key for the same series**. DBLP sometimes starts a fresh key (`conf/atal`→`conf/ifaamas` for AAMAS 2025+, `conf/alife`→`conf/isalalife` for ALIFE 2018+). A venue is not finished when its first key is found.
- A key that holds a **track of `V`'s own event** split out by DBLP. Example: `conf/icse-seip`, whose title is "International Conference on Software Engineering: Software Engineering in Practice". The main key already counts the same tracks in other years.
- `V`'s companion, Findings, workshop or poster volumes **inside `V`'s own key**. These count with the key; you cannot split a key.

**Reject**
- **Namesakes**: another conference with the same acronym. Decide by full title and publisher, never by acronym.
- **Separate events held with `V`**, even when the title mentions `V`. This covers co-located workshops and symposia with their own name and edition count (`conf/forge` and `conf/mobilesoft` at ICSE; `conf/ecom` "co-located with SIGIR").
- **Regional or national spin-offs**: `conf/sigir-ap` (SIGIR Asia-Pacific), `conf/dcscw` (German CSCW).
- **Umbrella or federated keys** whose volumes are mostly other venues: `conf/visigrapp`, and the Springer SCF series (`conf/icws2`, `conf/cloud2`, `conf/bigdata2`, `conf/services2`).
- **Keys where `V` is only a minority partner**, joint for a few years: `conf/ram` (CIS joint only since 2015), `conf/imagapp`.
- **Workshop post-proceedings** of `V`: `conf/aamas` holds the ALAMAS workshop volumes, not AAMAS itself.

**One key → at most one venue.** If a key plausibly fits two CORE venues, accept the one it actually is and reject the other with the reason.

**`absent`** means `V` has **no DBLP key of its own**. Use it only after the search in §4 step 3 finds nothing. If `V`'s papers sit inside another venue's key, it is still `absent`, and the reason must point there: `papers indexed inside conf/ic3k (booktitle KEOD)`.

**Venues now published in a journal.** Since some year, many venues publish their main track in a journal, which this `conf/`-only list cannot hold:
- PACMPL: POPL, PLDI, OOPSLA, ICFP
- PACMSE: FSE, ISSTA
- PACMMOD: SIGMOD, PODS
- PACMHCI: CSCW, MobileHCI, GROUP and others
- PACMNET: CoNEXT
- POMACS: SIGMETRICS
- PVLDB: VLDB
- TOG: SIGGRAPH
- TVCG: IEEE VIS, VR, ISMAR
- CGF: EuroVis, SGP
- PoPETs: PETS
- TCHES: CHES
- ToSC: FSE crypto
- Bioinformatics: ISMB

In DBLP the venue is in the issue `number`, e.g. `journals/pacmpl` number `POPL`. For such a venue:
- still accept all its `conf/` keys;
- never mark it `absent`;
- append a `JOURNAL:` tag to the reason of its newest accepted key (§3).

---

## 3. Writing decisions

```sql
INSERT INTO conf_decision (core_id, dblp_key, verdict, reason) VALUES
 ((SELECT id FROM core_venue WHERE title = 'Selected Areas in Cryptography'), 'conf/sacrypt', 'accept',
  'Proceedings 1994-2025 "Selected Areas in Cryptography", Springer'),
 ((SELECT id FROM core_venue WHERE title = 'Selected Areas in Cryptography'), 'conf/sac', 'reject',
  'ACM/SIGAPP Symposium on Applied Computing, namesake'),
 ((SELECT id FROM core_venue WHERE title = 'Simulation and Synthesis in Medical Imaging'), NULL, 'absent',
  'No key of its own; papers inside conf/miccai (SASHIMI@MICCAI); DBLP venue search: no hit');
```

| verdict | `dblp_key` | meaning |
|---|---|---|
| `accept` | the key | the key is this venue's series (a venue may have several keys) |
| `reject` | the key | the key is not this venue. Record every pair you examined and turned down, so later runs do not re-examine it |
| `absent` | `NULL` | the venue has no DBLP key of its own |

- Refer to venues by `id` from `conf_needs_review.core_id`, or by title sub-select as above. Titles are unique; ids are not stable across CORE reloads.
- `reason` is one line of concrete evidence: the proceedings name and year span, the publisher, and the predecessor proof. "Proceedings" alone is not a reason.
- **Reason tags** on the newest accepted key of a venue, appended with `UPDATE conf_decision SET reason = reason || '; …' WHERE id = ?`:
  - `JOURNAL: since <year> in journals/<key> (number <X>)`: the main track moved to a journal.
  - `ENDED: last DBLP edition <year>`: you confirmed there is no later key and no journal home. The series stopped, or DBLP stopped indexing it.
- The database refuses:
  - a key accepted for two venues;
  - a venue both accepted and absent;
  - duplicate pairs;
  - an empty reason;
  - an unknown key or venue.

  If an insert fails, your decision is wrong somewhere: fix it, never work around the constraint.
- Correct a decision with `UPDATE` or `DELETE` by `id`. Write in transactions, and re-read what you wrote.

---

## 4. Procedure

**Step 0. Preflight.**

```sql
SELECT issue, count(*) FROM conf_needs_review GROUP BY issue;
SELECT verdict, count(*) FROM conf_decision GROUP BY verdict;
```

On a first run every candidate and every CORE venue is open (about 1,600 rows). On a rerun after a new DBLP dump or CORE edition, only the changes are open, but the audits in steps 4–7 always run in full.

**Step 1. `INVALID`.** Delete each named decision. The pair or venue reappears as `UNREVIEWED` or `UNCOVERED` if it still needs a decision.

**Step 2. `UNREVIEWED`** (`dblp_key` → `core_title`). Apply §2 to the key's full history and write `accept` or `reject`. A key with several candidates gets at most one `accept`.

**Step 3. `UNCOVERED`** (no accepted key and no `absent` row). Run all of these searches before concluding anything:
1. The acronym, every former acronym and every former name from the CORE title, in key names, booktitles and proceedings titles:

   ```sql
   SELECT dblp_key, papers, first_year, last_year FROM dblp_venue WHERE dblp_key LIKE 'conf/%abc%';
   SELECT dblp_key, booktitle, papers FROM dblp_booktitle WHERE booktitle LIKE '%ABC%';
   SELECT dblp_key, year, title, publisher FROM dblp_proceedings WHERE title LIKE '%Distinctive Words%';
   ```
2. Distinctive words of the full title, which catches venues that DBLP files under an unexpected key (`conf/uss` for USENIX Security, `conf/mm` for ACM Multimedia, `conf/si3d` for I3D).
3. The DBLP venue search API and the conference website.

Accept every key you find (§2); reject the near misses you examined. Only if nothing is found, write `absent` and list what you searched in the reason.

**Step 4. Sweep for missed keys (must end at 0 rows).** These are undecided keys whose name, or whose non-workshop booktitle, equals a CORE acronym. This catches new keys of already-covered venues and keys the matcher never proposed. Most rows are namesakes: write a `reject` for each. Real ones get an `accept`.

```sql
WITH bt AS (
  SELECT dblp_key, lower(trim(CASE WHEN instr(booktitle, ' (') > 0
           THEN substr(booktitle, 1, instr(booktitle, ' (') - 1) ELSE booktitle END)) AS b
  FROM dblp_booktitle WHERE booktitle NOT LIKE '%@%'
), hit AS (
  SELECT v.id AS core_id, k.dblp_key FROM core_venue v
  JOIN dblp_venue k ON lower(substr(k.dblp_key, 6)) = lower(v.acronym)
  UNION
  SELECT v.id, bt.dblp_key FROM core_venue v JOIN bt ON bt.b = lower(v.acronym)
)
SELECT h.core_id, v.rank, v.acronym, v.title, h.dblp_key, k.papers, k.first_year, k.last_year, e.proceedings_titles
FROM hit h JOIN core_venue v ON v.id = h.core_id JOIN dblp_venue k ON k.dblp_key = h.dblp_key
JOIN venue_evidence e ON e.dblp_key = h.dblp_key
WHERE NOT EXISTS (SELECT 1 FROM conf_decision d WHERE d.dblp_key = h.dblp_key
                  AND (d.core_id = h.core_id OR d.verdict = 'accept'))
ORDER BY v.rank, v.acronym;
```

The query takes about 15 seconds.

**Step 5. Sweep for stale series (must end at 0 rows).** These are venues whose newest accepted key ends more than two years ago and carries no tag yet. For each one, find where the series went: a new key (Step 3 searches, then the DBLP venue page of the old key), a journal, or nowhere. Then accept the new key, or tag the newest accepted key with `JOURNAL:` or `ENDED:`.

```sql
SELECT v.id, v.rank, v.acronym, v.title,
       group_concat(d.dblp_key || ' ' || k.first_year || '-' || k.last_year, ', ') AS keys,
       max(k.last_year) AS newest
FROM core_venue v
JOIN conf_decision d ON d.core_id = v.id AND d.verdict = 'accept'
JOIN dblp_venue k ON k.dblp_key = d.dblp_key
GROUP BY v.id
HAVING newest < CAST(strftime('%Y', 'now') AS INTEGER) - 2
   AND sum(d.reason LIKE '%JOURNAL:%' OR d.reason LIKE '%ENDED:%') = 0
ORDER BY CASE v.rank WHEN 'A*' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, newest;
```

Also look for venues that are active but moved their main track to a journal: an accepted key whose recent booktitles are only `Companion`, `Abstracts`, `Workshops`, `Posters` or `X@Y`. Tag them `JOURNAL:` as well.

**Step 6. Audit accepts the matcher never proposed.** These were found by hand, so they carry the highest false-positive risk. Re-check each against §2, especially the predecessor proof. Fix any wrong one.

```sql
SELECT d.id, v.acronym, v.title, d.dblp_key, d.reason, e.booktitles, e.proceedings_titles
FROM conf_decision d JOIN core_venue v ON v.id = d.core_id JOIN venue_evidence e ON e.dblp_key = d.dblp_key
WHERE d.verdict = 'accept'
  AND NOT EXISTS (SELECT 1 FROM conf_candidate c WHERE c.dblp_key = d.dblp_key AND c.core_id = d.core_id);
```

**Step 7. Audit every `absent`.** Re-run the Step 3 searches, including the DBLP venue search API, for each `absent` venue. DBLP adds venues over time.

**Step 8. Final gate.** All three must hold; otherwise go back to step 1.
- `SELECT count(*) FROM conf_needs_review;` returns 0.
- The Step 4 query returns 0 rows.
- The Step 5 query returns 0 rows.

**Step 9. Report** (ten lines at most):
- `SELECT ranking, count(*) FROM list_of_conferences GROUP BY ranking;`
- CORE venues covered vs `absent`, out of 800;
- decisions added, changed and deleted in this run;
- every `accept` you reversed, or added during the audits (Steps 4–7), as key → venue;
- the venues tagged `JOURNAL:`, as acronym → journal key.

---

## 5. Traps already seen in this data

| looks like | actually | the CORE venue is at |
|---|---|---|
| `conf/sac` | ACM Symposium on Applied Computing | Selected Areas in Cryptography → `conf/sacrypt` |
| `conf/fse` | Fast Software Encryption (CORE B "FSE") | ACM FSE (A*) → `conf/sigsoft`, `conf/esec` |
| `conf/iswc` | Int. Symposium on Wearable Computers | Semantic Web Conference → `conf/semweb` |
| `conf/pact` | Parallel Computing Technologies (PaCT) | PACT → `conf/IEEEpact` |
| `conf/iv` / `conf/ivs` | Information Visualisation / IEEE Intelligent Vehicles | CORE "IV" (C) / "IEEE-IV" (B) |
| `conf/cloud`, `conf/bigdata` | ACM SoCC / IEEE BigData Congress | IEEE CLOUD → `conf/IEEEcloud`; IEEE BigData → `conf/bigdataconf` |
| `conf/chase`, `conf/ismm`, `conf/idc`, `conf/icis` | Connected Health; Mathematical Morphology; Intelligent Distributed Computing; AIS Information Systems | `conf/icse-chase`, `conf/iwmm`, `conf/acmidc`, `conf/ACISicis` |
| `conf/gi`, `conf/ec`, `conf/aamas` | German GI Jahrestagung; 1994 e-commerce workshop; ALAMAS workshop volumes | `conf/graphicsinterface`, `conf/sigecom`, `conf/atal` + `conf/ifaamas` |
| duplicate CORE acronyms | FSE, GI, ICCSA, ICEC, ISC each name two CORE venues | decide by full title |
| CORE acronyms written differently in DBLP | ACMMM, USENIX-Security, I3DG, HOTCHIPS (HCS), IJCRS (was RSCTC) | `conf/mm`, `conf/uss`, `conf/si3d`, `conf/hotchips`, `conf/rsctc` |

## 6. Hard rules

- One key maps to at most one CORE venue; one venue may have many keys. Find them all.
- Never accept on acronym alone: the proceedings must name the same series, or a proven predecessor of it.
- Never write `absent` without running every search in Step 3.
- Every pair you examine gets a written verdict.
- You are done only when the three checks in Step 8 hold.
