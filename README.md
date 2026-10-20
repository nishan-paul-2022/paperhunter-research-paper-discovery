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

- CORE conference ranking ([portal.core.edu.au/conf-ranks](http://portal.core.edu.au/conf-ranks/)) → `publication-core-2026.csv`
- Scopus CS journal ranking ([scimagojr.com](https://www.scimagojr.com/journalrank.php?category=1700)) → `publication-scopus-computer-science-journal-2025.csv`

### 4. Context

```bash
python3 circumstance/a_download.py
python3 circumstance/b_schema.py
```

**Output:** `archive/paper-dblp.xml`, `archive/paperhunter.db` with every table and view

### 5. Conference list

```bash
python3 conference-hunter/a_extract.py
python3 conference-hunter/b_match.py
```

Give `conference-hunter/c_conference_matching_guide.md` to the AI.

**Output:** view `conf_list`

### 6. Journal list

```bash
python3 journal-hunter/a_extract.py
python3 journal-hunter/b_match.py
```

Give `journal-hunter/c_journal_matching_guide.md` to the AI.

**Output:** view `journal_list`

### 7. Papers

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
