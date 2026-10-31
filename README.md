<p align="center">
  <img src="b-docs/logo-brand.svg" width="400" height="200" />
</p>

# PaperHunter: Evidence-Grounded Research Paper Discovery and Categorization at Scale

All data lives in `a-database/paperhunter.db`.

### 1. Environment

```bash
cp .env.example .env
```

### 2. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Source data

Save to `c-config/`:

- CORE conference ranking ([portal.core.edu.au/conf-ranks](http://portal.core.edu.au/conf-ranks/)) → `venue-core-2026.csv`
- Scopus CS journal ranking ([scimagojr.com](https://www.scimagojr.com/journalrank.php?category=1700)) → `venue-scopus-computer-science-journal-2025.csv`

### 4. Context

Edit `c-config/semantic.json` (topic, queries, keywords, seeds) first.

```bash
python d-context/a_download.py
python d-context/b_schema.py
python d-context/c_threshold.py
```

**Output:** `e-download/dblp.xml`, `a-database/paperhunter.db` with every table and view, and `paper_semantic_threshold`

Run `c_threshold.py` again whenever `semantic.json` changes.

### 5. Conference list

```bash
python f-conference-hunter/a_extract.py
python f-conference-hunter/b_match.py
```

Give `f-conference-hunter/c_search.md` to the AI.

**Output:** view `conf_list`

### 6. Journal list

```bash
python g-journal-hunter/a_extract.py
python g-journal-hunter/b_match.py
```

Give `g-journal-hunter/c_search.md` to the AI.

**Output:** view `journal_list`

### 7. Papers

Every paper moves through status `A` → `G` in `paper_status`. Each step takes only papers at the previous status, so any step can be rerun safely and continues where it stopped.

```bash
python h-paper-hunter/a_extract.py --from-year 2023 --to-year 2026
python h-paper-hunter/b_arxiv.py
python h-paper-hunter/c_fetch.py
```

Give `h-paper-hunter/d_abstract.md` to the AI.

```bash
python h-paper-hunter/e_embed.py
python h-paper-hunter/f_semantic.py
```

Give `h-paper-hunter/g_search.md` to the AI.

**Output:** view `paper_list`

---

<div align="center">
  <img src="b-docs/logo-company.svg" alt="Author Logo" width="80" height="80" />
  <p>Built with ❤️ by <b><a href="https://kaiofficial.xyz/">KAI</a></b></p>
</div>
