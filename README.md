# 🏹 PaperHunter

All data lives in `archive/paperhunter.db`.

### 1. Install

```bash
pip install lxml
```

### 2. Source data

Save to `archive/`:

- CORE conference ranking ([portal.core.edu.au/conf-ranks](http://portal.core.edu.au/conf-ranks/)) → `publication-core-2026.csv`
- Scopus CS journal ranking ([scimagojr.com](https://www.scimagojr.com/journalrank.php?category=1700)) → `publication-scopus-computer-science-journal-2025.csv`

### 3. Context

```bash
python3 circumstance/a_download.py
python3 circumstance/b_schema.py
```

**Output:** `archive/paper-dblp.xml`, `archive/paperhunter.db` with every table and view

### 4. Conference list

```bash
python3 conference-hunter/a_extract.py
python3 conference-hunter/b_match.py
```

Give `conference-hunter/c_conference_matching_guide.md` to the AI.

**Output:** view `conf_list`

### 5. Journal list

```bash
python3 journal-hunter/a_extract.py
python3 journal-hunter/b_match.py
```

`a_extract.py` scans the XML, then looks up about 5.5k sampled DOIs in Crossref (about 1 hour the first time; answers stay in the database, so later runs only look up new DOIs).

Give `journal-hunter/c_journal_matching_guide.md` to the AI.

**Output:** view `journal_list`

### 6. Papers

Put API keys in `paper-hunter/.env`:

```
OPENALEX_API_KEY_1=...
OPENALEX_API_KEY_2=...
S2_API_KEY=...
```

```bash
cd paper-hunter
python3 a_extract.py
python3 b_fetch.py fetch
python3 b_fetch.py status
```

Give `paper-hunter/e_paper_matching_guide.md` to the AI.

```bash
python3 d_review.py status
python3 d_review.py accepted
```

**Output:** tables `paper_dblp`, `paper_detail`, `paper_term`, `paper_reference`, `paper_accepted`, `paper_rejected`
