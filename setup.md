# A* Conference Paper Database (dblp)

Builds a local SQLite database + CSV of papers from the CORE ICORE2026 A* conferences (62 venues), years 2023-2026, from the dblp dump.

## Quick start

```bash
cd ~/Downloads/research-paper-database
python3 -m venv venv && source venv/bin/activate   # first time only
pip install lxml                                    # first time only
python3 hunter/index.py
```

That is all. The script:

1. downloads `dblp.dtd` and `dblp.xml.gz` if they are missing (about 1 GB, then unzipped to about 4-5 GB)
2. scans `dblp.xml` once and keeps only A* venues in the year range
3. writes `papers.db` and `papers.csv`
4. prints papers per venue and year

Expect 10-30 minutes. Progress is printed as a percentage.

## Update later (new papers, new conferences)

```bash
source venv/bin/activate
python3 hunter/index.py --refresh
```

`--refresh` deletes the old dump and downloads the newest one. The database is rebuilt from scratch, so there is nothing to merge or clean up. dblp updates its dump daily, but a conference only appears after its proceedings are indexed. Refresh every few months, or after a conference you care about has finished.

Other options:

| Option | Meaning |
|---|---|
| `--from 2023` | first year to keep |
| `--to 2027` | last year to keep (change when a new year starts) |

Example: `python3 hunter/index.py --refresh --from 2024 --to 2027`

## Why a DTD is needed

`dblp.xml` uses entities like `&uuml;` that are defined only in `dblp.dtd`. Without the DTD in the same folder, the parser fails. The script downloads it automatically.

## How papers are matched

Filtering uses dblp **keys** (e.g. `conf/ccs/Smith23`), not title text.

**Conference proceedings** (`<inproceedings>`): the key prefix is mapped to an acronym, e.g. `conf/ccs` = CCS, `conf/sp` = SP, `conf/uss` = USENIX-Security, `conf/ndss` = NDSS. The full table is the `CONF` dict at the top of the script.

**Venues published in journals** (`<article>`), which a plain `conf/` filter misses:

| Venue | dblp journal | Rule |
|---|---|---|
| POPL, PLDI | PACMPL (`journals/pacmpl`) | issue = POPL or PLDI |
| FSE | PACMSE (`journals/pacmse`) | issue = FSE (ISSTA skipped) |
| SIGMOD | PACMMOD (`journals/pacmmod`) | all issues |
| SIGMETRICS | POMACS (`journals/pomacs`) | all issues |
| SIGGRAPH / SIGGRAPH Asia | ACM TOG (`journals/tog`) | issue 4 / issue 6 |
| VLDB | PVLDB (`journals/pvldb`) | all |

ICAPS uses `conf/aips` for older years and `conf/icaps` from 2024. Both are handled, and the booktitle must be exactly `ICAPS`.

## Known limits

- **PODS** papers from 2024 onward are inside the SIGMOD set (same journal, no clean split).
- **SIGMETRICS** may include a few non-main-track POMACS papers.
- **SIGGRAPH / SiggraphA** come from TOG issues 4 and 6 and may include a few extras.
- **Recent years can be incomplete.** dblp indexes proceedings after publication, so latest-year counts may be partial, and venues not yet held (or not yet indexed) show no papers. The script prints which venues have none.
- dblp has **titles and metadata only**, no abstracts.
- Workshops have their own dblp keys and are excluded.

## Database

Table `papers`: `key, venue, year, title, authors, booktitle, pages, ee, doi`.
`authors` is separated by `; `. `ee` is the paper link.

Useful queries:

```bash
# papers per venue
sqlite3 papers.db "SELECT venue, COUNT(*) FROM papers GROUP BY venue ORDER BY 2 DESC;"

# top security venues, LLM-related titles
sqlite3 -header -csv papers.db "SELECT venue, year, title, authors, ee FROM papers WHERE venue IN ('CCS','SP','NDSS','USENIX-Security') AND (lower(title) LIKE '%llm%' OR lower(title) LIKE '%language model%') ORDER BY year DESC;" > security_llm.csv

# pentesting / vulnerability papers
sqlite3 -header -csv papers.db "SELECT venue, year, title, ee FROM papers WHERE lower(title) LIKE '%penetration%' OR lower(title) LIKE '%pentest%' ORDER BY year DESC;" > pentest.csv
```

## Add or change venues

Edit the `CONF` dict in `dblp/config.py` (key prefix to acronym) and re-run. To find a venue's key prefix:

```bash
grep -m3 '<booktitle>ICAPS' dblp.xml          # shows records for that booktitle
grep -m3 'key="conf/icaps/' dblp.xml          # check a guessed prefix
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `Missing lxml` | `pip install lxml` (inside the venv) |
| Entity or DTD error | make sure `dblp.dtd` is next to `dblp.xml`; run with `--refresh` |
| Looks stuck at start | it prints nothing until 500k records are scanned; wait a minute |
| Memory keeps growing | should stay flat; if not, report it |
| Download fails | download `dblp.dtd` and `dblp.xml.gz` manually from https://dblp.org/xml/ into the folder and re-run |
