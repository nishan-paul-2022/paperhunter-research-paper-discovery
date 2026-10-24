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
python3 paper-hunter/a_extract.py --from-year 2023 --to-year 2026
python3 paper-hunter/b_arxiv.py
python3 paper-hunter/c_fetch.py
```

Give `paper-hunter/d_arxiv.md` to the AI, then run `c_fetch.py` again.

```bash
python3 paper-hunter/e_embed.py
python3 paper-hunter/f_screen.py
```

Give `paper-hunter/g_lookup.md` to the AI, then run `e_embed.py` and `f_screen.py` again. Then give `paper-hunter/h_classify.md`, then `paper-hunter/i_infer.md`, then `h_classify.md` once more for the papers `i_infer.md` moved.

**Output:** view `paper_list`

#### Workflow diagram (proposed a→m design, not built yet)

Green = Python script, amber = AI prompt, blue = table, purple = view, dashed = input file or rerun.

```mermaid
%%{init: {"theme": "base", "flowchart": {"curve": "basis", "nodeSpacing": 28, "rankSpacing": 46}, "themeVariables": {"fontFamily": "IBM Plex Sans, system-ui, sans-serif", "fontSize": "14px", "lineColor": "#6b7180", "primaryTextColor": "#1d2330", "clusterBkg": "#f3f1ea", "clusterBorder": "#cfc9ba", "edgeLabelBackground": "#fcfbf7", "titleColor": "#3b4252"}}}%%
flowchart TB
  XML[/"dblp.xml"/]:::ext
  LISTS[/"conf_list · journal_list"/]:::view

  subgraph S1["1 · Papers"]
    A(["a · a_extract.py"]):::py
    DBLP[("paper_dblp")]:::tbl
  end

  subgraph ABS["Abstract store"]
    STATUS[("paper_abstract_status")]:::tbl
    PA[("paper_abstract")]:::tbl
    HIT[("paper_abstract_hit")]:::tbl
    MISS[("paper_abstract_miss")]:::tbl
  end

  subgraph S2["2 · arXiv IDs"]
    B(["b · b_arxiv.py"]):::py
    CORR[("paper_corr")]:::tbl
    CAND[("paper_arxiv_candidate")]:::tbl
    MATCH[("paper_arxiv_match")]:::tbl
    F{{"f · f_arxiv.md (AI)"}}:::ai
  end

  subgraph S3["3 · Fetch abstracts"]
    QA[/"paper_queue_arxiv"/]:::view
    QD[/"paper_queue_doi"/]:::view
    QT[/"paper_queue_title"/]:::view
    C(["c · c_fetch_arxiv_id.py"]):::py
    D(["d · d_fetch_openalex_doi.py"]):::py
    E(["e · e_fetch_semantic_doi.py"]):::py
    G(["g · g_fetch_openalex_title.py"]):::py
    H(["h · h_fetch_semantic_title.py"]):::py
  end

  subgraph S4["4 · Screening"]
    I(["i · i_embed.py"]):::py
    EMB[("paper_embedding")]:::tbl
    EMM[("paper_embedding_model")]:::tbl
    SEED[("paper_seed")]:::tbl
    J(["j · j_screen.py"]):::py
    SCR[("paper_screen")]:::tbl
    THR[("paper_screen_threshold")]:::tbl
  end

  subgraph S5["5 · Decide"]
    LQ[/"paper_lookup_queue"/]:::view
    K{{"k · k_lookup.md (AI)"}}:::ai
    L{{"l · l_classify.md (AI)"}}:::ai
    M{{"m · m_infer.md (AI)"}}:::ai
    VW[("paper_verdict_with_abstract")]:::tbl
    VWO[("paper_verdict_without_abstract")]:::tbl
  end

  OUT[/"paper_list · final result"/]:::final

  XML --> A
  LISTS --> A
  A --> DBLP
  A --> STATUS

  XML --> B
  DBLP --> B
  B --> CORR
  B --> CAND
  B --> MATCH
  B -. "sets arxiv_id" .-> DBLP
  CAND --> F
  CORR --> F
  F --> MATCH
  F -. "sets arxiv_id" .-> DBLP
  F -. "then rerun" .-> C

  DBLP --> QA
  DBLP --> QD
  DBLP --> QT
  QA --> C
  QD --> D
  QD --> E
  QT --> G
  QT --> H
  C --> ABS
  D --> ABS
  E --> ABS
  G --> ABS
  H --> ABS

  DBLP --> I
  PA --> I
  I --> EMB
  I --> EMM
  I --> SCR
  EMB --> J
  SEED --> J
  J --> SCR
  J --> THR
  J --> VW

  SCR --> LQ
  LQ --> K
  K --> ABS
  K -. "then rerun i, j" .-> I
  VW --> L
  L --> VW
  LQ --> M
  M --> VWO
  M -. "abstract found" .-> VW

  VW --> OUT
  VWO --> OUT

  classDef py fill:#dff1ec,stroke:#2f7d6d,stroke-width:1.5px,color:#12352e
  classDef ai fill:#fbe8c8,stroke:#b7791f,stroke-width:1.5px,color:#4a3108
  classDef tbl fill:#e6eaf2,stroke:#4a5a7a,stroke-width:1.2px,color:#1d2638
  classDef view fill:#efe6f6,stroke:#7a4fa0,stroke-width:1.2px,color:#2e1a40
  classDef ext fill:#ffffff,stroke:#8a8f98,stroke-width:1.2px,stroke-dasharray:4 3,color:#2a2d33
  classDef final fill:#2f7d6d,stroke:#1d5247,stroke-width:1.5px,color:#ffffff
```
