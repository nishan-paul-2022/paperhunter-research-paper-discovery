# paperhunter

**Staged, auditable pipeline for finding baseline papers: dblp ingest, keyword prefilter, LLM screening with a versioned rubric, human audit.**

Given a research topic, paperhunter narrows hundreds of thousands of papers to a short, defensible list of candidate baselines. Each step is cheap before it is expensive, every decision is logged, and recall is measured against papers you already know are relevant.

## Status

| Stage | Description | Status |
|---|---|---|
| 0. Ingest | dblp dump to SQLite/CSV for CORE A* venues, 2023-2026 | **Working** (`build_dblp_db.py`) |
| 1. Tier metadata | CORE A*/A/B/C for conferences, SJR Q1-Q4 for journals | Planned |
| 2. Prefilter | keyword/regex cut of the full set | Planned |
| 3. Title screen | LLM, fixed rubric, structured JSON, cached | Planned |
| 4. Enrich | abstracts and code links via OpenAlex / Semantic Scholar / arXiv | Planned |
| 5. Abstract screen | stricter baseline rubric on the shortlist | Planned |
| 6. Human audit | sample check and agreement report | Planned |

## Why this exists

General tools (Elicit, Semantic Scholar, Google Scholar) search broadly. Writing a survey or a literature chapter needs something different:

- **Baseline-specific judgment:** same task, comparable benchmark, public code.
- **Reproducible:** versioned rubric, logged model outputs, rerunnable when the rubric changes.
- **Measurable:** recall is checked against a gold set of known-relevant papers.
- **Topic-agnostic:** swap the rubric and keywords to reuse it for another project.

## Pipeline

```
dblp.xml ──► papers.db ──► prefilter ──► title screen ──► enrich ──► abstract screen ──► human audit
 (160k+)                   (~3-8k)       (LLM, batch)    (~300-800)   (LLM, strict)      (~50-100)
```

Design rules:

1. **Cheap before expensive.** Regex first, small model second, stronger model last.
2. **Tier is metadata, not a filter.** Ingest everything, store `tier`, filter at query time.
3. **Cache by paper key.** Reruns only process what changed.
4. **Temperature 0, structured output.** Same input, same label.
5. **Log everything.** Model, prompt version and raw output go to JSONL.

## Quick start (Stage 0 works today)

```bash
git clone https://github.com/<you>/paperhunter.git
cd paperhunter
python3 -m venv venv && source venv/bin/activate
pip install lxml
python3 build_dblp_db.py
```

The script downloads `dblp.dtd` and `dblp.xml.gz` if missing (about 1 GB, about 4-5 GB unzipped), scans the dump once and writes `papers.db` and `papers.csv`. Expect 10-30 minutes.

Update later:

```bash
python3 build_dblp_db.py --refresh                      # newest dump, rebuilds from scratch
python3 build_dblp_db.py --from 2024 --to 2027          # change the year range
```

### Why the DTD

`dblp.xml` uses entities such as `&uuml;` that are defined only in `dblp.dtd`. The parser fails without it, so it must sit next to the XML. The script handles this.

### How venues are matched

Filtering uses dblp **keys** (e.g. `conf/ccs/Smith23`), not title text.

- **Conferences:** key prefix to acronym (`CONF` dict at the top of the script).
- **Journal-published venues** that a `conf/` filter would miss:

| Venue | dblp journal | Rule |
|---|---|---|
| POPL, PLDI | PACMPL | issue = POPL / PLDI |
| FSE | PACMSE | issue = FSE |
| SIGMOD | PACMMOD | all issues (PODS mixed in) |
| SIGMETRICS | POMACS | all issues |
| SIGGRAPH / SIGGRAPH Asia | ACM TOG | issue 4 / issue 6 |
| VLDB | PVLDB | all |

ICAPS moved from `conf/aips` to `conf/icaps` in 2024; both are handled.

### Database schema

Table `papers`: `key, venue, year, title, authors, booktitle, pages, ee, doi`. `authors` is separated by `; `. `ee` is the paper link.

```bash
sqlite3 papers.db "SELECT venue, COUNT(*) FROM papers GROUP BY venue ORDER BY 2 DESC;"
sqlite3 -header -csv papers.db "SELECT venue, year, title, ee FROM papers WHERE venue IN ('CCS','SP','NDSS','USENIX-Security') AND lower(title) LIKE '%llm%';" > out.csv
```

## Planned layout

```
paperhunter/
  index.py                # Stage 0 entry point (with index)
  build_dblp_db.py        # Stage 0 legacy entry point (delegates to index.py)
  dblp/                   # Modular dblp pipeline package
    config.py             # URLs, paths, and conference mappings
    classifier.py         # Venue classification
    downloader.py         # DTD and XML download/decompression
    indexer.py            # SQLite index management
    builder.py            # XML parser & database builder
    exporter.py           # CSV export and summary reporting
    cli.py                # Command-line interface
  screen/
    rubric.md             # baseline criteria, versioned
    prefilter.py
    llm_screen.py         # batch, cached, JSONL output
    enrich.py             # abstracts + code links
  gold/
    known_relevant.csv    # papers the pipeline must find
  data/                   # gitignored: dblp.xml, papers.db
  results/                # committed: screened.jsonl, shortlist.csv
```

## Scope and tiers

Target coverage: conferences CORE A*/A/B/C and journals SJR Q1-Q4.

- **Conference tiers:** from the published CORE ICORE list.
- **Journal quartiles:** from SJR (free). Quartiles are per subject category, so a journal can be Q1 in one and Q3 in another; the rule (best quartile) will be written down in the repo.
- **Suggested rollout:** A*/A and Q1/Q2 first, plus arXiv cs.CR/cs.AI. Add B and Q3 only if the gold-set recall check shows gaps. C and Q4 stay behind an optional flag.
- **Tier is not quality.** A lower-tier paper with open code and a clean benchmark can be a better baseline than a top-tier paper without code.

## Quality and audit

- **Gold set:** known-relevant papers (for example, those in an existing survey) must be found. This gives a measured recall.
- **Sample audit:** manually check about 100 excludes and 100 includes and report agreement.
- **Reasons are not evidence.** The model's justification is plausible text; judge by the abstract and the code.
- **Disclosure:** if results feed a publication, state the LLM-assisted screening, model, rubric version and audit agreement in the methodology.

## Known limits

- dblp has titles and metadata only, no abstracts (hence the enrich stage).
- dblp is CS-only; other fields need another source.
- Recent years can be incomplete; dblp indexes proceedings after publication. The script prints venues with no papers.
- PODS (2024+) is inside the SIGMOD set; SIGMETRICS and the two SIGGRAPH sets may include a few extras from the same journal.
- Workshops have their own dblp keys and are excluded.
- LLMs favor well-known papers; the gold set and audit are there to catch this.

## Roadmap

- [x] Stage 0: dblp ingest, A* venues, 2023-2026
- [ ] Add tier metadata (CORE, SJR)
- [ ] Prefilter with configurable keyword file
- [ ] `rubric.md` and title screen (batch, cached, JSONL)
- [ ] OpenAlex / Semantic Scholar enrichment
- [ ] Abstract screen and shortlist export
- [ ] Gold-set recall report and audit sampling
- [ ] arXiv source (cs.CR, cs.AI)

## Troubleshooting

| Problem | Fix |
|---|---|
| `Missing lxml` | `pip install lxml` inside the venv |
| Entity or DTD error | keep `dblp.dtd` next to `dblp.xml`, or run with `--refresh` |
| Nothing printed at start | progress appears after 500k records; wait a minute |
| Download fails | fetch `dblp.dtd` and `dblp.xml.gz` from https://dblp.org/xml/ manually into the folder |

## License

TBD. Note that dblp data is released under CC0.
