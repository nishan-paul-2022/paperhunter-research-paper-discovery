# Master Prompt: Finish the Journal List (DBLP ↔ Scopus)

You are the reviewer. Complete this task end to end with no human help and no questions to the user.

**Goal.** Fill and audit the table `journal_decision` in `archive/paperhunter.db` so that the view `journal_list` contains **every** DBLP journal key (`journals/<name>`) that is a journal of the Scopus 2025 computer-science ranking (quartile Q1–Q4), and **nothing else**. Zero false positives, zero false negatives.

**Ground rules.**
- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. Use `sqlite3 archive/paperhunter.db` or Python's `sqlite3`, and start every connection with `PRAGMA foreign_keys = ON;`.
- Change only `journal_decision`. Never alter the schema or any other table.
- Trust nothing in the database blindly. `journal_candidate` is a wide machine proposal: most name-only pairs are namesakes, and a true pair can still be missing. Existing `journal_decision` rows may be wrong or incomplete. You audit both.
- You may use scripts to read, filter and batch-insert, but every single decision must rest on evidence you looked at for that pair. Never accept or reject by a score, a method or a pattern alone.

---

## 1. The data

DBLP's XML has **no** journal title and **no** ISSN field. A DBLP key is only a bag of articles that carry an abbreviated journal name (`<journal>`), a year and links (`<ee>`). The evidence below was derived from those articles.

| object | what it holds |
|---|---|
| `journal_scopus_venue(id, title, issn_1, issn_2, publisher, quartile, coverage)` | Scopus 2025 CS journals with Q1–Q4 (`id` = Scopus Sourceid). `coverage` is the Scopus year span, e.g. `2010-2026`. Empty ISSN = `''` |
| `journal_dblp_venue(dblp_key, papers, first_year, last_year)` | one row per DBLP journal key |
| `journal_dblp_name(dblp_key, name, papers, first_year, last_year)` | every `<journal>` name the key's articles use (ISO-4 style abbreviations), with year span. A name change inside a key is a rename |
| `journal_dblp_crossref(doi, dblp_key, name, year, status, issn_1, issn_2, title, publisher)` | **the strongest evidence.** Sampled article DOIs of the key (oldest and newest per name, plus the two newest years) looked up in Crossref: the journal's ISSNs (print, electronic), its **full** title and publisher as the publisher registered them. `status` 404 = DOI not in Crossref (DataCite, mEDRA, arXiv); no row = the key has no DOIs |
| `journal_dblp_issn(dblp_key, issn, papers)` | checksum-valid ISSNs embedded at the start of DOIs or in Elsevier PIIs (old Elsevier, Wiley SICI, Springer BMC). Weaker than Crossref, still near-proof |
| `journal_dblp_doi(dblp_key, prefix, papers)` | DOI prefixes = publisher: 10.1145 ACM, 10.1109 IEEE, 10.1016 Elsevier, 10.1007 Springer, 10.1002/10.1111 Wiley, 10.1080 Taylor & Francis, 10.3390 MDPI, 10.1142 World Scientific, 10.3233 IOS Press, 10.1504 Inderscience, 10.4018 IGI Global, 10.1515 De Gruyter, 10.1108 Emerald, 10.1587 IEICE, 10.1093 Oxford |
| `journal_candidate(dblp_key, scopus_id, method, score, detail)` | proposals from `b_match.py`; `method` is the strongest evidence of the pair (see below), `detail` lists **all** evidence of the pair |
| `journal_decision(id, scopus_id, dblp_key, verdict, reason, decided_at)` | **your output** |
| `journal_summary` | one row per key: names with year spans, Crossref journals with ISSNs and year spans, link ISSNs, DOI prefixes |
| `journal_review` | to-do list: `UNREVIEWED` candidates, `UNCOVERED` Scopus journals, `INVALID` decisions |
| `journal_list` | final output; it stays empty while `journal_review` has any row |

`journal_candidate.method`:
- `crossref`: a Crossref ISSN of the key's sampled DOIs is this Scopus journal's ISSN. `score` = share of the key's found DOIs carrying it. The `detail` says which years carry it and the key's newest DOI year.
- `issn`: only a link ISSN agrees (`score` = share of the key's ISSN-bearing links).
- `name`: only the name agrees (a DBLP name or a Crossref title abbreviates the Scopus title; `score` = share of Scopus title words covered). When the key has Crossref evidence pointing elsewhere, the `detail` ends with `NO CROSSREF ISSN MATCH: the key's newest Crossref DOI says …`.

Look at a key's whole history before deciding, not just the summary:

```sql
SELECT name, papers, first_year, last_year FROM journal_dblp_name WHERE dblp_key = 'journals/x' ORDER BY first_year;
SELECT year, name, status, issn_1, issn_2, title, publisher, doi FROM journal_dblp_crossref WHERE dblp_key = 'journals/x' ORDER BY year;
SELECT * FROM journal_dblp_issn WHERE dblp_key = 'journals/x';
SELECT * FROM journal_dblp_doi WHERE dblp_key = 'journals/x' ORDER BY papers DESC;
SELECT * FROM journal_dblp_venue WHERE dblp_key = 'journals/x';
```

Web sources, authoritative when the data above leaves doubt:
- **Crossref journal record** `https://api.crossref.org/journals/<ISSN>`: title, publisher and **all** ISSNs of a journal. Settles whether two ISSNs are the print and electronic editions of one journal.
- **Crossref recent works** `https://api.crossref.org/journals/<ISSN>/works?rows=5&sort=published&order=desc&select=DOI,container-title,published`: recent DOIs of a Scopus journal. Find their DBLP key in the dump: `grep -i -F -m1 'doi.org/<DOI></ee>' -B15 archive/paper-dblp.xml | grep 'key="journals/'`. Many DOIs at once: write them, one per line as `doi.org/<DOI><`, to a file and run `grep -i -F -f <file> archive/paper-dblp.xml` in one pass (≈1 minute).
- **Scopus/SJR page** `https://www.scimagojr.com/journalsearch.php?q=<Sourceid>&tip=sid`: title history, ISSNs and coverage.
- **DBLP journal page** `https://dblp.org/db/journals/<name>/`: full title and ISSNs, often links predecessor and successor ("continued as").
- The publisher's journal page, for renames ("formerly …") and mergers.
- Send at most one request per second to any one API.

**If dblp.org is unreachable.** dblp.org and its mirrors (`dblp.uni-trier.de`, `dblp.dagstuhl.de`) sit behind one bot wall. If one answers with a "Making sure you're not a bot" page, the others will too: do not retry them. The tables above, the dump `archive/paper-dblp.xml` (`grep 'key="journals/x/'` for a key's articles) and Crossref replace it. Never write that you checked DBLP when you could not; append `DBLP web unreachable` to every `absent` reason written without it.

If `journal_dblp_crossref` does not exist, or `SELECT count(*) FROM journal_dblp_crossref WHERE status IS NULL` is not 0, stop and tell the humans to run `circumstance/b_schema.py`, then `journal-hunter/a_extract.py` (a rerun also retries failed Crossref lookups), then `journal-hunter/b_match.py`.

---

## 2. When a DBLP key is a Scopus journal

The unit is the **whole key**. Accept key `K` for Scopus journal `J` only when `K`'s articles are `J`'s articles, under its current or a former title.

**Accept**
- Crossref (or link) ISSNs of `K`'s DOIs are `J`'s ISSNs **in its newest years**, and the names agree.
- **Print/online gap.** Scopus lists only one edition's ISSN and Crossref gives the other (`journals/cma`: Crossref 0898-1221, Scopus 1873-7668). Prove it with `api.crossref.org/journals/<ISSN>` listing both ISSNs for the same title, and put `ISSN GAP:` with both ISSNs in the reason.
- **Rename inside one key.** The name changes in `journal_dblp_name` and the Crossref ISSN changes at the same year, with the newest DOIs on `J`'s ISSN (`journals/jeric`: J. Educ. Resour. Comput. → ACM Trans. Comput. Educ.; `journals/program` → Data Technologies and Applications; `journals/vlc` → Journal of Computer Languages; `journals/ir` → Discover Computing).
- **Predecessor key.** `K` ended and DBLP continues the same journal in a new key (`journals/jeric` 2001–2023 → `journals/toce`). Accept every key of the journal's lineage, but only for a **rename of the same journal**: the publisher or Scopus says "formerly `K`", or the ISSN carries over. Tag the reason `PREDECESSOR: continued as journals/<new> (<proof>)`. When the journal was renamed and DBLP has no key for the new title yet (`journals/nle`, `journals/aicom`), tag it `PREDECESSOR: renamed <new title> <year> (<proof>)`. A different journal **absorbed** by `J` is a separate journal: reject it.
- Journals that publish conference proceedings (PVLDB, PACMPL, TOG, TCHES, PoPETs) are still journals: accept them if Scopus lists them.

**Reject**
- **Namesakes**: same or similar title, different ISSN and publisher (`journals/ijaiml` IGI 2642-1577 vs SvedbergOpen 2789-2557; `journals/jcybsec` Tech Science Press vs the Chinese Academy of Sciences journal; `journals/iot` Elsevier "Internet of Things" vs MDPI "IoT"; Argentine vs Chinese "Journal of Computer Science and Technology"; Lithuanian vs Slovenian "Informatica").
- **Title containment**: a different journal whose title contains the DBLP name (`Artif. Intell.` vs `Artificial Intelligence in Medicine`; `Oper. Res.` vs `Operational Research`).
- **Misfiled articles**: one or two isolated DOIs of `K` carry `J`'s ISSN while the rest carry another (`journals/scl` with one Science of Computer Programming DOI; `journals/ejasmp` with one EURASIP JASP DOI). A low `crossref` score with scattered years means this.
- **`journals/corr`** is arXiv (CoRR). It holds preprints of every journal: reject every pair.
- **Shared keys**: one key holding several journals at once (`journals/jct` = JCT Series A **and** B until 2020; `journals/ieicet` = four IEICE Transactions until 2020). The key cannot be split, so reject it for each of them with `SHARED KEY: <the journals>` in the reason. DBLP's per-journal keys (`journals/jcta`, `journals/jctb`, `journals/ieiceta` … `ieicetd`) are the ones to accept.
- **Sections or series** with their own ISSN (`Series A` vs `Series B`, `Part B: Cybernetics` vs `Systems`): accept only the section the key's newest DOIs belong to.

**One key → at most one Scopus journal.** If a key plausibly fits two, accept the one its newest DOIs belong to and reject the other with the reason.

**`absent`** means `J` has **no DBLP key of its own**. Use it only after every search in §4 Step 3 finds nothing. Many Scopus CS journals (applied, regional, medical, engineering, management) are not in DBLP; the Scopus file also holds conference series typed as journals (e.g. "IEEE … Conference, ITNEC"), which DBLP files under `conf/`: these are `absent` too.

---

## 3. Writing decisions

```sql
INSERT INTO journal_decision (scopus_id, dblp_key, verdict, reason) VALUES
 (19700183047, 'journals/jeric', 'accept',
  'PREDECESSOR: continued as journals/toce; Crossref 1531-4278 J. Educ. Resour. Comput. 2001-2009 then 1946-6226 TOCE 2009-2023, ACM 10.1145'),
 (23456, 'journals/ior', 'reject',
  'Namesake: INFORMS Operations Research (Crossref 0030-364X), not Springer Operational Research 1109-2858'),
 (34567, NULL, 'absent',
  'No DBLP key: ISSNs 1234-5678/2345-6789 in no Crossref or link ISSN; 5 recent Crossref DOIs not in the dump; name search Foo Bar: no hit');
```

| verdict | `dblp_key` | meaning |
|---|---|---|
| `accept` | the key | the key is this Scopus journal (a journal may have several keys) |
| `reject` | the key | the key is not this journal. Record every pair you examined and turned down |
| `absent` | `NULL` | this Scopus journal has no DBLP key of its own |

- Take `scopus_id` from `journal_review.scopus_id` or `journal_scopus_venue.id` (the stable Scopus Sourceid).
- `reason` is one line of concrete evidence: the ISSN and its source (Crossref / link), the years, the publisher, and for renames the proof. "Same name" alone is not a reason.
- **Reason tags**, required where they apply (the sweeps in §4 read them):
  - `ISSN GAP: <Crossref ISSN> = <Scopus ISSN> (<proof>)` on an accept whose Crossref ISSNs are not in Scopus.
  - `PREDECESSOR: continued as journals/<new> (<proof>)` on every accepted key that is not the newest accepted key of its journal (Step 4e), or `PREDECESSOR: renamed <new title> <year> (<proof>)` on a key whose journal now has a title DBLP has not indexed yet (Step 4d).
  - `ENDED: last DBLP year <y>` on the newest accepted key of a journal that DBLP stopped indexing, after you confirmed there is no later key.
  - `SHARED KEY: <journals>` on rejects of a key holding several journals.
  - `NEW TITLE: <no predecessor, or conference series> (<what you checked>)` on `absent` journals whose Scopus coverage starts in the last three years (Step 4d).
- The database refuses:
  - a key accepted for two journals;
  - a journal both accepted and absent;
  - duplicate pairs;
  - an empty reason;
  - an unknown key or journal.

  If an insert fails, your decision is wrong somewhere: fix it, never work around the constraint.
- Correct a decision with `UPDATE` or `DELETE` by `id`. Write in transactions, and re-read what you wrote.
- A Scopus or DBLP reload deletes the decisions of journals and keys that disappeared (foreign keys cascade); their pairs come back as `UNREVIEWED` or `UNCOVERED` if still relevant.

---

## 4. Procedure

**Step 0. Preflight.**

```sql
SELECT issue, count(*) FROM journal_review GROUP BY issue;
SELECT verdict, count(*) FROM journal_decision GROUP BY verdict;
SELECT coalesce(status, 0), count(*) FROM journal_dblp_crossref GROUP BY 1;
```

On a first run every candidate and every Scopus journal is open. On a rerun after a new DBLP dump, Scopus edition or matcher change, only the new pairs are open, but the sweeps and audits in Steps 4–6 always run in full.

**Step 1. `INVALID`.** Delete each named decision.

**Step 2. `UNREVIEWED`** (`dblp_key` → `scopus_title`). Work key by key: read the key's whole history (§1), then decide all its candidates together, at most one `accept`.
- `crossref` with a high score whose years include the key's newest year: usually an accept, after the names and publisher agree.
- `crossref` with a low score: read the per-DOI rows. Contiguous newest years on `J`'s ISSN with a name change = rename (accept). Contiguous oldest years only = predecessor title (accept only with `PREDECESSOR:`, or reject if `J` is another journal). Scattered single DOIs = misfiled (reject).
- `issn`: the same reading over link ISSNs; they are mostly pre-2010, so confirm the key's newest years by name and Crossref.
- `name` with `NO CROSSREF ISSN MATCH`: almost always a namesake. Accept only with an `ISSN GAP:` proof. Expect to reject nearly all of these.
- `name` on a key without Crossref evidence (no DOIs, or all 404): decide by full title (expand the ISO-4 name), publisher (DOI prefix or the `<ee>` host vs `publisher`) and years vs `coverage`. Check the journal's own website or the Scopus page when in doubt.

**Step 3. `UNCOVERED`** (Scopus journal with no accepted key and no `absent` row). Run all of these before concluding anything:
1. Its ISSNs in the evidence:

   ```sql
   SELECT dblp_key, year, title, issn_1, issn_2 FROM journal_dblp_crossref WHERE ? IN (issn_1, issn_2);
   SELECT * FROM journal_dblp_issn WHERE issn = ?;
   ```
2. Distinctive title words, its ISO-4 abbreviation and former titles in names and Crossref titles:

   ```sql
   SELECT dblp_key, name, papers, first_year, last_year FROM journal_dblp_name WHERE name LIKE '%Geosci%';
   SELECT DISTINCT dblp_key, title, issn_1, issn_2 FROM journal_dblp_crossref WHERE title LIKE '%Geoscience%';
   ```
3. The reverse probe: its 5 most recent DOIs from Crossref recent works (§1), searched in the dump. Batch this for all `UNCOVERED` journals in one `grep -F -f` pass. A hit names the key directly.
4. Former titles from the Scopus/SJR page or the publisher, then repeat 1–2 for each.

Accept every key you find (§2); reject the near misses you examined. Only if nothing is found, write `absent` and list what you searched in the reason.

**Step 4. Sweeps. Each must end at 0 rows.**

4a. Absent journals that the evidence contradicts. Fix the decision: accept the key, or prove the hit is a misfiled DOI and reject that pair.

```sql
WITH si AS (SELECT id, issn_1 AS issn FROM journal_scopus_venue WHERE issn_1 <> ''
            UNION SELECT id, issn_2 FROM journal_scopus_venue WHERE issn_2 <> '')
SELECT d.id, s.title, x.dblp_key, 'crossref ' || x.year AS hit
FROM journal_decision d JOIN journal_scopus_venue s ON s.id = d.scopus_id JOIN si ON si.id = s.id
JOIN journal_dblp_crossref x ON x.status = 200 AND si.issn IN (x.issn_1, x.issn_2)
WHERE d.verdict = 'absent'
  AND NOT EXISTS (SELECT 1 FROM journal_decision r WHERE r.scopus_id = s.id AND r.dblp_key = x.dblp_key)
UNION
SELECT d.id, s.title, i.dblp_key, 'link ' || i.papers
FROM journal_decision d JOIN journal_scopus_venue s ON s.id = d.scopus_id JOIN si ON si.id = s.id
JOIN journal_dblp_issn i ON i.issn = si.issn
WHERE d.verdict = 'absent'
  AND NOT EXISTS (SELECT 1 FROM journal_decision r WHERE r.scopus_id = s.id AND r.dblp_key = i.dblp_key);
```

4b. Accepts whose newest Crossref DOI does not carry the journal's ISSN. Each must be a proven `ISSN GAP:` or `PREDECESSOR:`; otherwise the accept is wrong.

```sql
WITH si AS (SELECT id, issn_1 AS issn FROM journal_scopus_venue WHERE issn_1 <> ''
            UNION SELECT id, issn_2 FROM journal_scopus_venue WHERE issn_2 <> ''),
newest AS (SELECT x.* FROM journal_dblp_crossref x WHERE x.status = 200 AND x.year =
           (SELECT max(year) FROM journal_dblp_crossref y WHERE y.dblp_key = x.dblp_key AND y.status = 200))
SELECT DISTINCT d.id, d.dblp_key, s.title, n.year, n.title AS crossref_title, n.issn_1, n.issn_2, d.reason
FROM journal_decision d JOIN journal_scopus_venue s ON s.id = d.scopus_id JOIN newest n ON n.dblp_key = d.dblp_key
WHERE d.verdict = 'accept' AND d.reason NOT LIKE '%ISSN GAP:%' AND d.reason NOT LIKE '%PREDECESSOR:%'
  AND NOT EXISTS (SELECT 1 FROM newest m JOIN si ON si.id = s.id
                  WHERE m.dblp_key = d.dblp_key AND si.issn IN (m.issn_1, m.issn_2));
```

4c. Stale journals: Scopus still covers them, but every accepted key ended more than two years ago. Find the successor key (Step 3 searches plus the reverse probe), accept it and tag the old key `PREDECESSOR:`; or, if DBLP stopped indexing, tag the newest key `ENDED:`.

```sql
SELECT s.id, s.title, s.coverage, group_concat(d.dblp_key || ' ' || k.first_year || '-' || k.last_year, ', ') AS keys,
       max(k.last_year) AS newest
FROM journal_scopus_venue s
JOIN journal_decision d ON d.scopus_id = s.id AND d.verdict = 'accept'
JOIN journal_dblp_venue k ON k.dblp_key = d.dblp_key
GROUP BY s.id
HAVING newest < CAST(strftime('%Y', 'now') AS INTEGER) - 2
   AND CAST(substr(s.coverage, -4) AS INTEGER) >= CAST(strftime('%Y', 'now') AS INTEGER) - 1
   AND sum(d.reason LIKE '%ENDED:%') = 0
   AND sum(d.reason NOT LIKE '%PREDECESSOR:%') > 0;
```

4d. New Scopus titles marked `absent`. A journal renamed with a new ISSN gets a new Scopus record, while DBLP still files its papers under the old key and has not indexed the new title yet. Neither the matcher nor the reverse probe can see that (*Natural Language Engineering* → *Natural Language Processing* 2025, `journals/nle`; *AI Communications* → *European Journal on Artificial Intelligence* 2025, `journals/aicom`). For each row, check the publisher's journal page, the Scopus/SJR page and the ISSN portal for "formerly", "continues" or "renamed". If it continues an older journal, find that journal's key (Step 3 searches by the old title and ISSN), delete the `absent` row and accept the key with `PREDECESSOR:`. Otherwise append `NEW TITLE: no predecessor (<what you checked>)` to the `absent` reason. Conference series that Scopus types as journals get `NEW TITLE: conference series`.

```sql
SELECT d.id, s.id AS scopus_id, s.title, s.issn_1, s.issn_2, s.publisher, s.coverage
FROM journal_decision d JOIN journal_scopus_venue s ON s.id = d.scopus_id
WHERE d.verdict = 'absent' AND d.reason NOT LIKE '%NEW TITLE:%'
  AND CAST(substr(s.coverage, 1, 4) AS INTEGER) >= CAST(strftime('%Y', 'now') AS INTEGER) - 3;
```

4e. Older keys of a journal without a `PREDECESSOR:` tag. Every accepted key that is not the newest accepted key of its journal is a predecessor, and its accept must carry the proof that it is the same journal under a former title, not a different journal absorbed by it (§2). Add the tag with the proof, or reject the key if there is no proof.

```sql
SELECT d.id, d.dblp_key, k.first_year, k.last_year, s.title, d.reason
FROM journal_decision d JOIN journal_dblp_venue k ON k.dblp_key = d.dblp_key JOIN journal_scopus_venue s ON s.id = d.scopus_id
WHERE d.verdict = 'accept' AND d.reason NOT LIKE '%PREDECESSOR:%'
  AND EXISTS (SELECT 1 FROM journal_decision n JOIN journal_dblp_venue m ON m.dblp_key = n.dblp_key
              WHERE n.scopus_id = d.scopus_id AND n.verdict = 'accept' AND m.last_year > k.last_year);
```

**Step 5. Audit name-only accepts.** These carry the highest false-positive risk. Re-check each against §2: full title, publisher, years, and the Crossref journal record. Fix any wrong one.

```sql
SELECT d.id, d.dblp_key, s.title, s.publisher, s.coverage, d.reason, e.names, e.crossref, e.doi_prefixes
FROM journal_decision d JOIN journal_scopus_venue s ON s.id = d.scopus_id JOIN journal_summary e ON e.dblp_key = d.dblp_key
WHERE d.verdict = 'accept'
  AND NOT EXISTS (SELECT 1 FROM journal_candidate c WHERE c.dblp_key = d.dblp_key AND c.scopus_id = d.scopus_id
                  AND c.method IN ('crossref', 'issn'));
```

**Step 6. Audit every `absent`.** Re-run Step 3 for each `absent` journal whose reason lacks the reverse probe. If dblp.org is reachable in this run, also re-check every reason that carries `DBLP web unreachable` against the DBLP journal search, and drop the suffix once checked; if it is still unreachable, leave those rows as they are.

**Step 7. Final gate.** All six must hold; otherwise go back to Step 1.
- `SELECT count(*) FROM journal_review;` returns 0.
- The Step 4a, 4b, 4c, 4d and 4e queries return 0 rows.

**Step 8. Report** (ten lines at most):
- `SELECT quartile, count(*) FROM journal_list GROUP BY quartile;`
- Scopus journals covered vs `absent`, out of all Scopus journals;
- decisions added, changed and deleted in this run;
- every `accept` you reversed or added during Steps 4–6;
- counts of `ISSN GAP:`, `PREDECESSOR:`, `ENDED:`, `SHARED KEY:` and `NEW TITLE:` tags;
- whether DBLP and Crossref were reachable, and how many reasons carry `DBLP web unreachable`.

---

## 5. Traps already seen in this data

| looks like | actually | correct |
|---|---|---|
| `journals/or` "Oper. Res." ~ Operations Research | Springer *Operational Research* (1109-2858) | `journals/ior` = INFORMS Operations Research |
| `journals/iot` ~ MDPI "IoT" (2624-831X) | Elsevier *Internet of Things* (2542-6605) | Scopus "Internet of Things (The Netherlands)" |
| `journals/cma` with no Crossref ISSN match | print 0898-1221 vs Scopus e-ISSN 1873-7668 | accept with `ISSN GAP:` |
| `journals/corr` with LMCS and many other names | arXiv preprints | reject every pair |
| `journals/jct`, `journals/ieicet` | shared keys (JCT A+B; four IEICE Transactions) up to 2020 | `journals/jcta`, `jctb`, `ieiceta` … `ieicetd` |
| `journals/jeric`, `journals/vlc`, `journals/program`, `journals/ir`, `journals/jdi` | renamed journals (TOCE, J. Comput. Lang., Data Technol. Appl., Discover Computing, J. Imaging Inform. Med.) | accept, with `PREDECESSOR:` when the key ended |
| `journals/cl` vs `journals/vlc` for Journal of Computer Languages | both claim the lineage; Crossref puts the new ISSN 2590-1184 on `vlc` | prove `cl`'s link with the publisher before any accept |
| `journals/nle`, `journals/aicom` (ended 2024, no candidate) | renamed 2025 with new ISSNs: *Natural Language Processing* 2977-0424, *European Journal on Artificial Intelligence* 3050-4546 | accept with `PREDECESSOR:` (Step 4d) |
| `journals/ton` "IEEE/ACM Trans. Netw." | renamed "IEEE Trans. Netw." in 2025, new ISSN 2998-4157 | Scopus "IEEE Transactions on Networking" |
| `journals/scl`, `journals/ejasmp` with one foreign DOI | misfiled article | reject that pair |
| two journals called "Informatica", "Journal of Computer Science and Technology", "Journal of Cyber Security", "Int. J. Artificial Intelligence and Machine Learning" | namesakes from different countries/publishers | decide by ISSN |

## 6. Hard rules

- One key maps to at most one Scopus journal; one journal may have several keys. Find them all.
- Never accept on a name alone: an ISSN (Crossref or link) or a proven print/online gap must agree, or, for a key with no Crossref evidence, the full title, publisher and years must all agree.
- Never accept `journals/corr` or a shared key; never write `absent` without running every search in Step 3.
- Never claim a DBLP web check you did not make.
- Every pair you examine gets a written verdict.
- You are done only when the six checks in Step 7 hold.
