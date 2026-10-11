# paperhunter

Research paper discovery pipeline: DBLP → filtered journals/conferences → paper extraction.

## Quick Start

### 1. Download DBLP Database
```bash
cd database
python3 download.py
cd ..
```

### 2. Extract Journal List
```bash
python3 journal-hunter/a_extract.py
```
Provide `journal-context/journal-matching-guide.md` to AI.

Output: `journal-context/list-of-journals.csv` (~990 journals)

### 3. Extract Conference List
```bash
python3 conference-hunter/a_extract.py
```
Provide `conference-context/conference-matching-guide.md` to AI.

Output: `conference-context/list-of-conferences.csv` (~756 conferences)

## Project Structure

```
paperhunter/
├── database/
│   ├── download.py              # Download DBLP XML dump
│   ├── dblp.xml                 # DBLP database (5GB)
│   ├── dblp.xml.gz              # Compressed download (1.1GB)
│   └── dblp.dtd                 # XML schema
│
├── journal-context/
│   ├── sjr-2025-cs.csv          # SJR source data
│   ├── journal-matching-guide.md
│   └── list-of-journals.csv     # Final output: 990 journals
│
├── journal-supplement/
│   ├── 1-issn-matched.csv       # ISSN exact matches
│   ├── 2-dblp-todo.csv          # DBLP journals needing match
│   ├── 3-sjr-pool.csv           # SJR journals available
│   └── 4-ai-matched.csv         # Algorithm matched journals
│
├── journal-hunter/
│   ├── a_extract.py             # Extract + ISSN matching
│   ├── b_match.py               # Fuzzy matching algorithm
│   └── c_merge.py               # Merge into final list
│
├── conference-context/
│   ├── core-2026.csv            # CORE source data
│   ├── conference-matching-guide.md
│   └── list-of-conferences.csv  # Final output: 756 conferences
│
├── conference-supplement/
│   ├── 1-conf-exact-matched.csv # Acronym exact matches
│   ├── 2-conf-todo.csv          # DBLP conferences needing match
│   ├── 3-core-pool.csv          # CORE conferences available
│   └── 4-conf-matched.csv       # Algorithm matched conferences
│
└── conference-hunter/
    ├── a_extract.py             # Extract + acronym matching
    ├── b_match.py               # Fuzzy matching algorithm
    └── c_merge.py               # Merge into final list
```

## Requirements

```bash
python3 -m venv venv
source venv/bin/activate
pip install lxml
```

## Output Statistics

### Journals (SJR Q1-Q4)
- Total matched: ~990 journals
- Q1: 274 | Q2: 259 | Q3: 233 | Q4: 224

### Conferences (CORE A*-C)
- Total matched: 756 conferences
- A*: 62 | A: 111 | B: 251 | C: 332

## Methodology

### Journal Matching
1. **ISSN exact match** (6% of journals)
2. **Algorithmic fuzzy matching** with curated mappings (94%)
3. Conservative HIGH-confidence-only filtering

### Conference Matching
1. **Acronym exact match** (82% of conferences)
2. **Algorithmic fuzzy matching** with title similarity (13%)
3. Conservative HIGH-confidence-only filtering

## Update Sources

```bash
# Download latest SJR data
# Visit: https://www.scimagojr.com/journalrank.php?category=1700
# Save as: journal-context/sjr-2025-cs.csv

# Download latest CORE rankings
# Visit: http://portal.core.edu.au/conf-ranks/
# Save as: conference-context/core-2026.csv

# Re-run extraction after updating source files
```

## License

TBD. DBLP data is released under CC0.
