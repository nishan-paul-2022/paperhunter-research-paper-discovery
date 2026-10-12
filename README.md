# 🏹 PaperHunter

## Quick Start

### 1. Install Requirements

```bash
pip install lxml
```

### 2. Setup Source Data

```bash
# Download SJR journal rankings
# Visit: https://www.scimagojr.com/journalrank.php?category=1700
# Save as: journal-context/sjr-2025-cs.csv

# Download CORE conference rankings
# Visit: http://portal.core.edu.au/conf-ranks/
# Save as: conference-context/core-2026.csv
```

### 3. Download DBLP Database
```bash
cd database
python3 download.py
cd ..
```

### 4. Build Journal List

Provide `journal-context/journal-matching-guide.md` to AI.

**Output:** `journal-context/list-of-journals.csv`

### 5. Build Conference List

Provide `conference-context/conference-matching-guide.md` to AI.

**Output:** `conference-context/list-of-conferences.csv`

### 6. Extract Papers

```bash
python3 database/paper.py
```

**Output:** `papers/*.csv` (32 files organized by ranking and year)
