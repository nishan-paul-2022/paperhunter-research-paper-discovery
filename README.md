# 🏹 PaperHunter

All data lives in `archive/paperhunter.db`.

### 1. Environment

```bash
cp .env.example .env
```

### 2. Install

```bash
pip install lxml
```

### 3. Source data

Save to `archive/`:

- CORE conference ranking ([portal.core.edu.au/conf-ranks](http://portal.core.edu.au/conf-ranks/)) → `venue-core-2026.csv`
- Scopus CS journal ranking ([scimagojr.com](https://www.scimagojr.com/journalrank.php?category=1700)) → `venue-scopus-computer-science-journal-2025.csv`

### 4. Context

```bash
python3 circumstance/a_download.py
python3 circumstance/b_schema.py
```

**Output:** `archive/dblp.xml`, `archive/paperhunter.db` with every table and view

### 5. Conference list

```bash
python3 conference-hunter/a_extract.py
python3 conference-hunter/b_match.py
```

Give `conference-hunter/c_search.md` to the AI.

**Output:** view `conf_list`

### 6. Journal list

```bash
python3 journal-hunter/a_extract.py
python3 journal-hunter/b_match.py
```

Give `journal-hunter/c_search.md` to the AI.

**Output:** view `journal_list`

### 7. Papers

```bash
python3 paper-hunter/a_extract.py
python3 paper-hunter/b_details.py
```

Give `paper-hunter/c_search.md` to the AI; it reviews every paper fetched so far. When an API's daily budget stops `b_details.py`, run it again the next day, then give the prompt to the AI again: only the newly fetched papers are reviewed.

**Output:** view `paper_list`
