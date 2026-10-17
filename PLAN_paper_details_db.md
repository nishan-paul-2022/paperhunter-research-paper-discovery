# Plan: Paper Details Database (DBLP + OpenAlex + Semantic Scholar) and LLM Reference-Paper Selection

## 1. Goal

Build a local database describing ~1,030,000 CS papers (2023-2026) so that an LLM can read each paper's details and decide whether it counts as a **reference paper** for our research domain.

Inputs already decided:
- Venues: CORE-ranked conferences (A*, A, B, C) and Scopus-indexed journals (Q1-Q4), CS only.
- Papers: shortlisted from DBLP, 2023-2026 (~1,030,000; ~70,000 have no DOI).
- Enrichment sources: OpenAlex and Semantic Scholar (S2), both through their APIs. No full-snapshot downloads.

Principle: **DBLP is the master record.** OpenAlex and S2 only add information, keyed by `dblp_key`. Every match carries a method and a confidence.

---

## 2. Pipeline overview

| Step | Output | Depends on |
|---|---|---|
| 0. DBLP XML to base tables | `conferences`, `journals`, `papers`, `authors`, `paper_authors` | current `dblp.xml.gz` + `dblp.dtd` |
| 1. Pilot (1,000 papers) | go/no-go numbers, final schema | step 0 |
| 2. Enrichment, DOI papers (~960k) | `openalex_raw`, `s2_raw` | step 1 |
| 3. Reconcile and merge | `paper_details` | step 2 |
| 4. Enrichment, non-DOI papers (~70k) | more rows in the raw and detail tables | step 3 |
| 5. LLM reference-paper decision | `llm_decisions`, `reference_papers` | steps 3-4 + prompt file |
| 6. Maintenance | refreshed tables | all |

---

## 3. Step 0: DBLP tables from the current DBLP XML

Source: the current DBLP dump (`dblp.xml.gz` and `dblp.dtd` from dblp.org/xml). Parse it as a stream (the file is very large), with the DTD available so entities resolve.

Relevant XML elements: `inproceedings` (conference papers), `article` (journal papers), `proceedings` (volumes). Useful fields: `key`, `title`, `author`, `year`, `booktitle` (conference) / `journal`, `ee` (DOI or external link), `url`, `pages`, `volume`, `number`, `mdate`.

### Table: `conferences`
| Column | Notes |
|---|---|
| conf_id | DBLP key prefix, e.g. `conf/ics` |
| acronym | e.g. ICS |
| full_name | |
| core_rank | A*, A, B, C (from the CORE list) |
| core_list_version | which CORE edition was used |
| field_codes | CORE field-of-research codes, if kept |

### Table: `journals`
| Column | Notes |
|---|---|
| journal_id | DBLP key prefix, e.g. `journals/tdsc` |
| title | |
| issn / eissn | for matching to Scopus, when available |
| scopus_quartile | Q1-Q4 |
| scopus_list_year | which list edition was used |

### Table: `papers`
| Column | Notes |
|---|---|
| dblp_key | primary key |
| title | trailing period stripped |
| year | |
| venue_type | conference / journal |
| venue_id | foreign key to `conferences` or `journals` |
| venue_name | as in DBLP |
| ee | original link from DBLP |
| doi | normalized (lowercase, no URL prefix); arXiv links converted to `10.48550/arxiv.<id>` |
| doi_source | doi / arxiv / none |
| pages, volume, number | |
| dblp_url | |

### Tables: `authors`, `paper_authors`
- `authors`: author_id, name (DBLP disambiguation suffix such as `0006` stripped, original kept in a separate column).
- `paper_authors`: dblp_key, author_id, position.

### Table: `reference_papers` (final selection, filled in step 5)
| Column | Notes |
|---|---|
| dblp_key | primary key |
| decision | reference / not_reference / needs_review |
| confidence | 0-1 |
| reason | one-sentence justification from the LLM |
| prompt_version | which prompt file produced it |
| model | which model produced it |
| decided_at | timestamp |

Cleaning rules in step 0: normalize DOIs, strip author suffixes, flag duplicate papers (same paper under two DBLP keys, e.g. preprint and published), and keep the original text next to cleaned text.

Known risk: matching DBLP venues to CORE and Scopus lists is by name or ISSN and can be imperfect. Since the shortlist already exists, keep it as the source and record the matching method per venue.

---

## 4. Step 1: Pilot (1,000 papers)

Pick a mix: different venues, years, ranks, and DOI and non-DOI papers. Measure:
- Match rate per source (OpenAlex, S2) and agreement between them.
- Share of papers with an abstract (each source and combined).
- Actual API cost and time per 1,000 papers.
- Size of stored responses (to estimate total disk).
- Whether S2 accepts DBLP keys as IDs (unverified; test 5 papers). If yes, non-DOI lookup becomes easier.

Decision: proceed, change field selection, or adjust the plan before the full run.

---

## 5. Step 2: Enrichment for DOI papers (~960k)

### OpenAlex
- Lookup by DOI with OR filters, **100 DOIs per call** (the maximum).
- Cost: list calls are $0.0001 each, so ~9,600 calls is about **$1**, which fits the free key's $1/day budget. Single lookups by DOI are free but one call per paper.
- Use a free API key. Store the response, or the chosen fields, compressed.
- Fields of interest: title, year, abstract (inverted index), topics/keywords, authorships (ORCID, institutions), cited_by_count and counts_by_year, referenced_works, related_works, open-access status and PDF URL, primary location (venue), license, funders.

### Semantic Scholar
- Batch endpoint, up to **500 IDs per request**; IDs like `DOI:10.xxx/yyy`.
- Free key; expect about **1 request per second**, so ~1,920 requests is roughly 35 minutes plus retries.
- Fields of interest: abstract, TLDR, citationCount, influentialCitationCount, fieldsOfStudy, openAccessPdf, externalIds, publicationVenue, references/citations (only if needed).

### Operation rules
- Both jobs are resumable and write results as they arrive.
- Global request throttle, exponential backoff on 429/5xx, stop gracefully when a daily budget is exhausted and resume the next UTC day.
- Per-paper, per-source status: `matched`, `notfound`, `error`.
- Keep raw responses so no re-query is needed if a field is wanted later.

---

## 6. Step 3: Reconcile and build `paper_details`

One row per `dblp_key`, merging the three sources.

| Column group | Source and rule |
|---|---|
| Identity: dblp_key, doi, openalex_id, s2_paper_id, corpus_id | DBLP + APIs |
| Bibliographic: title, authors, venue, year | **DBLP wins** |
| Abstract | S2 abstract, else rebuilt OpenAlex abstract, else arXiv/other fallback; `abstract_source` recorded |
| TLDR | S2 |
| Topics: OpenAlex topic/subfield/field, keywords; S2 fields of study | both kept |
| Citations: openalex_cited_by, s2_citation_count, s2_influential_citation_count | both kept (they differ) |
| References: referenced_works IDs | OpenAlex (and S2 if needed) |
| Access: oa_status, oa_pdf_url, license | OpenAlex / S2 |
| Quality: match_method_openalex, match_method_s2, sources_agree, data_tier, flags | computed |

### Data tiers
| Tier | Has | Use |
|---|---|---|
| A | Abstract | normal pipeline |
| B | No abstract, but title + topics or TLDR | title/topic signals, stricter threshold |
| C | Only DBLP data (title, authors, venue) | title and venue only, lower confidence |

Validation: compare DOI and title between OpenAlex and S2 for the same paper. Agreement is strong evidence of a correct match; disagreements go to a review list.

---

## 7. Step 4: Non-DOI papers (~70k)

Order of attempts (cheapest first), for each paper:
1. arXiv link converted to DOI, if present in `ee` (done in step 0, free).
2. S2 title-match endpoint (free, one paper per request, so ~19 hours at 1 req/sec for 70k).
3. Free title-to-DOI lookup in Crossref, then a cheap OpenAlex DOI lookup (optional).
4. OpenAlex exact-phrase title search (`search="full title"`, year +/-1, ~$0.001 per call) for the remainder.
5. Otherwise: keep DBLP data only (tier C).

**Strict acceptance rule (exact match or nothing):** normalized title is identical, year within +/-1, first author's last name matches, and at least half of the authors match. If several candidates pass (e.g. preprint and published version), prefer the exact year, then the most citations.

Not recommended: OpenAlex semantic search for this step. It costs about $0.01 per call, is limited to 1 request per second, and finds similar papers rather than the same paper.

Spot-check ~50 title-matched papers by hand.

---

## 8. Step 5: LLM decision, "is this a reference paper?"

### Inputs per paper
Title, abstract (or TLDR if no abstract), venue, year, OpenAlex topics/keywords, tier. Never send more than needed.

### Funnel (to keep cost and noise down)
1. **Free filters:** venue, OpenAlex topics, keyword rules on title and abstract.
2. **Embedding similarity** against ~20-50 seed papers that define the domain (cheap for 1M papers).
3. **LLM only for papers that remain ambiguous.** Running the LLM on all 1M papers is the expensive option and should be justified by pilot results.

### Prompt file (to be created next)
`prompts/reference_paper_decision.md` should contain:
- A precise **domain definition**: topics that count, topics that do not, and borderline cases with explicit rulings (e.g. "LLM agents for pentesting: yes; generic ML malware detection: only if ...").
- Inclusion and exclusion criteria written as rules, plus 5-10 labeled examples of each class.
- What to do with missing abstracts (lower confidence, rely on title/topics, prefer `needs_review`).
- A **strict JSON output schema:** `decision` (`reference` / `not_reference` / `needs_review`), `confidence` (0-1), `reason` (one sentence), `matched_topics` (list).
- Instruction to answer only from the supplied fields and not guess about papers it cannot judge.
- A version number in the file header, recorded in `llm_decisions`.

### Evaluation before the full run
- Hand-label 200-300 papers (include tier B and C).
- Measure precision and recall of the funnel and of the LLM step, per tier.
- Tune thresholds on the labeled sample, then freeze the prompt version.
- Run the full set in batches, store raw model output, and review a sample of `reference` and `needs_review` outputs.

### Tables
- `llm_decisions`: dblp_key, prompt_version, model, decision, confidence, reason, matched_topics, raw_output, run_id, decided_at.
- `reference_papers`: the current accepted decision per paper (see section 3).

---

## 9. Step 6: Maintenance
- Monthly or quarterly: re-pull citation counts and newly available abstracts; retry tier B/C papers.
- Ingest new papers from DBLP updates for the shortlisted venues.
- When the prompt changes, rerun on the labeled sample first, then on affected papers; keep old decisions with their prompt version.
- Back up the database and the raw response store.

---

## 10. Cost and time estimate (to be confirmed by the pilot)

| Item | Estimate |
|---|---|
| OpenAlex DOI batch (~960k papers) | ~9,600 calls, about $1 |
| S2 DOI batch (~960k papers) | ~1,920 requests, about 35 min at 1 req/sec |
| Non-DOI via S2 title match | free, ~19 hours |
| Non-DOI leftovers via OpenAlex search | $0.001 per search; a fraction of 70k |
| LLM step | depends on funnel; measure on the pilot |
| Disk | tens of GB if full OpenAlex records are kept |

---

## 11. Risks and open questions
- **Terms of use:** S2's API is described as for academic/non-commercial use; confirm that paper hunter's intended use fits. Credit OpenAlex in outputs.
- **API key requirement:** OpenAlex now requires a key; use one from the start.
- **Venue matching** to CORE/Scopus lists can be imperfect (names, ISSN gaps).
- **Coverage gaps:** very new papers, workshops, and venues without DOIs (some USENIX/regional venues) may be thin or missing in both sources.
- **Duplicates and versions:** preprint vs published versions can produce two records for one paper.
- **LLM consistency:** decisions can drift between models and prompt versions; always store the version.
- **Unverified items to test in the pilot:** S2 accepting DBLP keys, real S2 throughput, real OpenAlex cost headers, abstract coverage per source.

---

## 12. Checklist

- [ ] Download current `dblp.xml.gz` + `dblp.dtd`; parse into base tables
- [ ] Load CORE and Scopus lists; fill `conferences` and `journals`
- [ ] Load the existing 1.03M shortlist into `papers`; clean DOIs and authors
- [ ] Get OpenAlex and S2 API keys
- [ ] Run the 1,000-paper pilot; record metrics; decide go/no-go
- [ ] Full DOI enrichment: OpenAlex, then S2
- [ ] Reconcile into `paper_details`; assign tiers; review disagreements
- [ ] Non-DOI enrichment with the strict acceptance rule
- [ ] Write the domain definition and `reference_paper_decision.md` prompt
- [ ] Hand-label 200-300 papers; evaluate; freeze prompt version
- [ ] Run the LLM funnel; fill `llm_decisions` and `reference_papers`
- [ ] Review a sample; set up refresh schedule and backups
