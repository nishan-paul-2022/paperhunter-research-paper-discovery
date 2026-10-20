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

### 3. Download DBLP

```bash
python3 archive/download.py
```

### 4. Create database

```bash
python3 archive/schema.py
```

**Output:** `archive/paperhunter.db` with every table and view

### 5. Conference list

```bash
python3 conference-hunter/a_extract.py
python3 conference-hunter/b_match.py
```

Give `conference-hunter/c_conference_matching_guide.md` to the AI.

**Output:** view `list_of_conferences`

### 6. Journal list

```bash
python3 journal-hunter/a_extract.py
python3 journal-hunter/b_match.py
```

Give `journal-hunter/c_journal_matching_guide.md` to the AI.

**Output:** view `list_of_journals`

### 7. Papers

```bash
python3 paper-hunter/a_extract.py
```

**Output:** table `dblp_paper`

### 8. Paper details

Put API keys in `paper-hunter/.env`:

```
OPENALEX_API_KEY_1=...
OPENALEX_API_KEY_2=...
S2_API_KEY=...
```

```bash
cd paper-hunter
python3 b_fetch.py fetch
python3 b_fetch.py status
```

**Output:** tables `paper_detail`, `paper_term`, `paper_reference`

### 9. Review

Give `paper-hunter/e_paper_matching_guide.md` to the AI.

```bash
python3 d_review.py status
python3 d_review.py accepted
```

**Output:** tables `paper_accepted`, `paper_rejected`
