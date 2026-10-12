# Academic Publishing Guide: Journals, Conferences, Rankings & Metrics

A simple reference. Every metric has: **what it measures → formula → example → time window**.

---

## 1. Quick Map (Who Does What)

| Role | Who | What they do |
|---|---|---|
| **Publishers** | Elsevier, Springer Nature, Wiley, Taylor & Francis, SAGE (the "Big Five"); IEEE, ACM, USENIX (societies) | Publish journals and conference proceedings |
| **Indexers / Databases** | Scopus (Elsevier), Web of Science (Clarivate), Google Scholar, DBLP | Collect papers and count citations |
| **Metric providers** | Clarivate (JIF, JCI, Immediacy), Elsevier/Scopus (CiteScore, SNIP, FWCI), SCImago (SJR), Google Scholar (h-index, h-median, i10), CORE (conference tiers) | Calculate scores |

**Key point:** Clarivate is **not** a publisher. It is a data and analytics company (the "referee and stat-keeper"). Elsevier is both a publisher **and** owns Scopus.

| Database | Owner | Famous metric |
|---|---|---|
| Web of Science (WoS) | Clarivate | JIF, JCI, Immediacy Index |
| Scopus | Elsevier | CiteScore, SNIP, FWCI |
| SCImago (uses Scopus data) | SCImago Lab | SJR |
| Google Scholar | Google | h-index, h-median, i10-index |
| CORE | Computing Research & Education Assoc. (Australasia) | Conference tiers A*, A, B, C |

---

## 2. Basic Terms

| Term | Meaning |
|---|---|
| **Journal** | Periodical (monthly/quarterly) with peer-reviewed articles. Reviewed on rolling deadlines. |
| **Conference** | Live event where research is presented; papers are published in **proceedings**. Fixed deadlines. |
| **Proceedings** | Collection of papers from a conference (e.g., LNCS, Procedia, IEEE Xplore, ACM DL). |
| **Peer review** | Experts check the paper before acceptance. **Single-blind:** reviewers know the authors. **Double-blind:** both sides anonymous. |
| **Citation** | When one paper references another. More citations ≈ more influence. |
| **Citable items** | Research articles and reviews. Editorials, letters, news, obituaries are usually excluded. |
| **Quartile (Q1–Q4)** | Journals in a subject are ranked and split into 4 groups of 25%. **Q1 = top 25%**, Q4 = bottom 25%. |
| **Open Access (OA)** | Free to read. Gold OA = author pays an APC. Diamond OA = no fee for author or reader. |
| **APC** | Article Processing Charge: fee paid by author to publish OA. |
| **Predatory journal** | Charges fees but gives no real peer review. Avoid. |
| **Indexed** | The journal is accepted into a database (Scopus, WoS). It is a quality filter. |
| **DOI** | Permanent unique ID of a paper (e.g., `10.1000/xyz123`). |
| **Preprint** | Paper shared publicly **before** peer review (e.g., arXiv). |
| **Self-citation** | Author cites their own work. Can inflate metrics. |

---

## 3. Journal Metrics

Notation: **Y** = the year the metric is reported for.

### 3.1 Clarivate (Web of Science / Journal Citation Reports)

#### A. JIF: Journal Impact Factor
- **Measures:** Average citations per paper for a journal's recent papers.
- **Window:** Citations in year **Y** to papers published in **Y-1 and Y-2**.
- **Formula:**

```
JIF = Citations in Y to items from (Y-1, Y-2)
      ---------------------------------------
      Citable items published in (Y-1, Y-2)
```

- **Example (JIF 2025):**
  - Citations in 2025 to papers from 2023 + 2024 = **400**
  - Citable items published in 2023 + 2024 = **100**
  - JIF = 400 / 100 = **4.0**
- Note: Clarivate also publishes a 5-year JIF.

#### B. JCI: Journal Citation Indicator
- **Measures:** Field-normalized impact. **1.0 = world average** for that field.
- **Window:** 3 years of papers.
- **Formula:** Average of **CNCI** (Category Normalized Citation Impact) over all papers.

```
CNCI of a paper = Citations of the paper / Average citations of similar papers
                  (same field, same year, same document type)
JCI = average of all papers' CNCI
```

- **Example:** A history journal has 3 papers.
  - Paper 1: 6 citations, field average 2 → CNCI = 6/2 = **3.0**
  - Paper 2: 1 citation, field average 2 → CNCI = 1/2 = **0.5**
  - Paper 3: 2 citations, field average 2 → CNCI = 2/2 = **1.0**
  - JCI = (3.0 + 0.5 + 1.0) / 3 = **1.5** (50% above world average)

#### C. Immediacy Index
- **Measures:** How fast papers get cited (same year as publication).
- **Window:** 1 year.
- **Formula:**

```
Immediacy = Citations in Y to items published in Y
            ---------------------------------------
            Items published in Y
```

- **Example (2025):** Journal published 100 items in 2025; those got 120 citations in 2025.
  Immediacy = 120 / 100 = **1.2**

---

### 3.2 Elsevier (Scopus)

#### D. CiteScore
- **Measures:** Average citations per document.
- **Window:** 4 years (Y-3 to Y), for both citations and documents.
- **Counts:** All document types in the window (articles, reviews, conference papers, book chapters, data papers, etc.).
- **Formula:**

```
CiteScore = Citations received in the 4 years to documents published in the same 4 years
            --------------------------------------------------------------------------
            Documents published in those 4 years
```

- **Example (CiteScore 2025):**
  - Documents published 2022–2025 = **300**
  - Citations to them during 2022–2025 = **1,200**
  - CiteScore = 1,200 / 300 = **4.0**

#### E. SNIP: Source Normalized Impact per Paper
- **Measures:** Journal impact, adjusted for how heavily the field cites. Allows fair comparison between fields.
- **Window:** 3 years.
- **Why it exists:** Biology papers list 60+ references, math papers about 10. So biology journals get cited more just because of field habits. SNIP removes that advantage.
- **Formula:**

```
SNIP = RIP / RDCP

RIP  = Raw Impact per Paper
     = Citations in year Y to the journal's papers from (Y-3 to Y-1)
       / Number of those papers

RDCP = Relative Database Citation Potential
     = Journal's Citation Potential / Median Citation Potential of ALL journals
```

- **How "Citation Potential" is calculated (step by step):**
  1. Find all papers that **cite** this journal.
  2. For each citing paper, count how many of its references point to **recent papers** (last 3 years).
  3. Average that number. This is the journal's citation potential. A field with long reference lists gets a **high** value, a field with short lists gets a **low** one.
  4. Divide by the median value of all journals in the database. A typical journal gets **RDCP = 1.0**.

- **Worked example (one journal):**
  - RIP: 100 papers published 2022–2024 received 300 citations in 2025 → RIP = 300 / 100 = **3.0**
  - Papers citing this journal have on average 12 recent references → citation potential = **12**
  - Median of all journals in the database = **10** → RDCP = 12 / 10 = **1.2**
  - SNIP = 3.0 / 1.2 = **2.5**

- **Worked example (two fields compared):**

| Journal | RIP (raw avg cites) | Field citation potential | Database median | RDCP | SNIP |
|---|---|---|---|---|---|
| Math | 1.0 | 5 | 10 | 0.5 | 1.0 / 0.5 = **2.0** |
| Biology | 4.0 | 40 | 10 | 4.0 | 4.0 / 4.0 = **1.0** |

  The biology journal has 4x more raw citations, but the math journal scores **higher** because it beats the low citing habits of its field.
- **Reading it:** 1.0 = typical journal. Above 1.0 = above typical for its field.

#### F. SJR: SCImago Journal Rank
- **Measures:** Prestige. A citation from a top journal is worth more than one from a weak journal (PageRank-style).
- **Window:** 3 years.
- **Formula:** Complex iterative network algorithm (not a simple division).
- **Idea-example:** Journal A gets 10 citations from *Nature*. Journal B gets 10 citations from an unknown newsletter. Same count, but **A gets a higher SJR**.
- Typical values: most journals 0.1 to 3; elite journals can be far higher.

#### G. FWCI: Field-Weighted Citation Impact
- **The problem it solves:** Medical papers get cited far more than math papers, and old papers have had more time to collect citations. Raw counts are unfair, so FWCI compares a paper only with its **true peers**.
- **Measures:** How well **one paper** (or an author or institution) is cited vs. similar papers. **1.0 = world average.**
- **Peers = papers that match on all 3:**
  1. Same **subject field**
  2. Same **publication year**
  3. Same **document type** (article, review, etc.)
- **Window:** Citations in the publication year plus the following 3 years (Scopus standard).
- **Formula:**

```
FWCI = Actual citations of the paper
       ------------------------------------------
       Expected citations (average of its peer group)
```

- **Step by step:**
  1. Count the citations your paper received.
  2. Find all papers in the world with the same field, year and type. Average their citations. This is the **expected** value.
  3. Divide actual by expected.

- **Example 1 (math paper):** actual = 4 citations, expected = 2 → FWCI = 4 / 2 = **2.0**
- **Example 2 (medical paper):** actual = 12 citations, expected = 11 → FWCI = 12 / 11 ≈ **1.1**
- **Comparison:** The medical paper has 3x more raw citations (12 vs 4), but the math paper has the **higher relative impact** (2.0 vs 1.1).

| FWCI | Meaning |
|---|---|
| 0.5 | 50% **below** world average |
| 1.0 | Exactly average |
| 1.5 | 50% above average |
| 2.0 | Twice the average (100% above) |

- **For an author or institution:** FWCI is the **average FWCI of all their papers**. Example: three papers with FWCI 2.0, 1.0 and 0.0 → author FWCI = (2.0 + 1.0 + 0.0) / 3 = **1.0**.
- **Changes over time?** Yes, in the early years. Example: a paper has 3 citations after year 1 while the world average is 1 → FWCI = **3.0**. By year 3 the paper still has 3 citations but the world average has grown to 3 → FWCI = **1.0**. It stabilizes once citations slow down.

---

### 3.3 Journal Metrics Comparison

| Metric | Owner | Database | Window | Field-normalized? | Measures |
|---|---|---|---|---|---|
| JIF | Clarivate | WoS | 2 yrs | No | Avg citations |
| JCI | Clarivate | WoS | 3 yrs | **Yes** (1.0 = avg) | Relative impact |
| Immediacy | Clarivate | WoS | 1 yr (same year) | No | Speed of citation |
| CiteScore | Elsevier | Scopus | 4 yrs | No | Avg citations |
| SNIP | Elsevier / CWTS | Scopus | 3 yrs | **Yes** | Relative impact |
| SJR | SCImago | Scopus | 3 yrs | Partly (prestige-weighted) | Prestige |
| h-index | Scopus / Scholar | Both | Lifetime | No | Productivity + impact |

**Important:** Elsevier does **not** calculate JIF (Clarivate owns it). Elsevier journals show their JIF on their own pages, but it comes from Clarivate.

---

## 4. Author-Level Metrics

### 4.1 h-index
- **Measures:** Balance of productivity (number of papers) and impact (citations).
- **Window:** Lifetime. It never goes down.
- **Rule:** h = the largest number such that **h papers each have at least h citations**.
- **Applies to:** Authors, journals, conferences. **Not** to a single paper (a paper only has a citation count).
- **Example:** Papers sorted by citations: **50, 22, 4, 3, 1**

| Rank | Citations | Rank ≤ Citations? |
|---|---|---|
| 1 | 50 | Yes |
| 2 | 22 | Yes |
| 3 | 4 | Yes |
| 4 | 3 | **No** (4 > 3) |
| 5 | 1 | No |

  **h-index = 3**
- **Weakness:** Favors older researchers (more time to collect citations).

#### h-median
- **Measures:** How strong the papers **inside** the h-index are. The h-index only says each of the top h papers has *at least* h citations; the h-median shows whether they barely pass or far exceed it.
- **Definition:** The **median** citation count of the h papers that make up the h-index (called the **h-core**).
- **Steps:** (1) find h, (2) take the top h papers, (3) find the middle value of their citations.
- **Example 1:** Citations **50, 22, 4, 3, 1** → h = 3. The h-core is 50, 22, 4. Middle value = **22**. So **h-median = 22**.
- **Example 2 (even count):** Citations **40, 30, 20, 10, 2** → h = 4 (4 papers with ≥ 4 citations). The h-core is 40, 30, 20, 10. Median = (30 + 20) / 2 = **25**.
- **Comparison:** Two authors both have h = 3. Author X has h-median = 4 (papers barely qualify), Author Y has h-median = 95 (papers far exceed the threshold). Y has the stronger core.
- **Applies to:** Authors, journals, conferences (same as the h-index).

### 4.2 i10-index
- **Measures:** Number of papers with **at least 10 citations**.
- **Available in:** Google Scholar only. Shows "All" and "Since last 5 years".
- **Example:** Citations: **120, 50, 14, 11, 10, 9, 2**
  Papers with ≥ 10 citations: 120, 50, 14, 11, 10 → **i10-index = 5**
- **Weakness:** A paper with 10 citations counts the same as one with 10,000. A paper with 9 counts zero.

### 4.3 Total citations
Simple count of all citations to all of an author's papers. Example: 50 + 22 + 4 + 3 + 1 = **80**.

### 4.4 h-index vs i10 on the same author

| Citations (sorted) | 50, 22, 4, 3, 1 |
|---|---|
| h-index | 3 |
| i10-index | 2 (only 50 and 22 reach 10) |
| Total citations | 80 |

---

## 5. Paper-Level Metrics

| Metric | What it is | Example |
|---|---|---|
| **Citation count** | Times the paper was cited | "Cited 45 times" |
| **FWCI** (Scopus) | Citations vs. world average (see 3.2 G) | 8 citations ÷ 4 expected = **2.0** |
| **CNCI** (WoS) | Same idea in Clarivate's database | 6 ÷ 2 = **3.0** |
| **PlumX / Altmetrics** | Online attention in 5 groups: Usage (views, downloads), Captures (bookmarks), Mentions (blogs, Wikipedia), Social media, Citations | Paper has 500 downloads, 20 tweets, 3 Wikipedia mentions |

---

## 6. Conference Evaluation

### 6.1 Do journal metrics apply to conferences?

| Metric | Applies to conferences? |
|---|---|
| JIF, JCI | **No.** Only for indexed journals. |
| CiteScore, SNIP | **No.** Only for serial journals. |
| SJR | Rarely (only some proceedings treated as serials in Scopus). |
| **h-index / h-median** | **Yes** (venue-level, e.g., Google Scholar). |
| **FWCI** | **Yes** for an individual paper in proceedings. |
| **Tier ranking (CORE)** | **Yes**, the dedicated system. |
| **Acceptance rate** | **Yes**, the main selectivity signal. |

### 6.2 CORE Ranking (Computer Science)
Expert panels assign a tier (no formula):

| Tier | Meaning | Examples |
|---|---|---|
| **A\*** | Flagship, elite, highly selective | IEEE S&P, USENIX Security, ACM CCS, CVPR, NeurIPS |
| **A** | Excellent international venue | |
| **B** | Good, solid venue | |
| **C** | Meets baseline standards, local or narrow scope | |

### 6.3 Acceptance Rate
```
Acceptance Rate = (Accepted papers / Submitted papers) x 100
```
- **Example:** 800 submissions, 120 accepted → 120 / 800 = **15%**
- Rule of thumb: below ~20% is very competitive.

### 6.4 Other prestige signals
- Who sits on the program committee
- Keynote / plenary speakers
- Review process (double-blind is preferred)
- Where proceedings are indexed (IEEE Xplore, ACM DL, Springer LNCS, Scopus, WoS)
- h-index / h-median of the conference

### 6.5 Journal acceptance rate
Journals also have acceptance rates, but they are **optional to publish**, and are a **secondary** signal.

| | Journal | Conference |
|---|---|---|
| Space | Flexible (add pages/issues) | Fixed slots and rooms |
| Deadline | Rolling, any time | Fixed date |
| Review time | Months, with revisions | Weeks |
| Role in prestige | Secondary (impact metrics matter more) | **Primary** |
| Example | 220 accepted / 2,000 submitted = **11%** | 120 / 800 = **15%** |

---

## 7. Publishers and Venues

### 7.1 Big Five commercial publishers

| Publisher | Strength | Sample journals | Conference vehicle |
|---|---|---|---|
| **Elsevier** | Medicine, engineering, AI | The Lancet, Cell | Procedia series |
| **Springer Nature** | Sciences, computing | Nature, Scientific Reports | LNCS, LNEE |
| **Wiley** | Chemistry, life sci., engineering | Advanced Materials | Society proceedings |
| **Taylor & Francis** | Social sci., humanities, engineering | Connection Science | Proceedings books (CRC Press) |
| **SAGE** | Social sci., medicine | Journal of Management | Society meeting outputs |

*Nature* is a journal owned by **Springer Nature** (Nature Portfolio).

### 7.2 Top publishers and venues in Computer Science
In CS, **conferences are often more prestigious than journals.**

| Organization | Type | Examples |
|---|---|---|
| **IEEE** | Society | Journals: TPAMI, TSE, TDSC. Conferences: CVPR, ICCV, IEEE S&P |
| **ACM** | Society | Journals: CSUR, JACM, TOG. Conferences: SOSP, CCS, STOC, SIGGRAPH |
| **USENIX** | Non-profit (conferences, open access) | USENIX Security, OSDI, NSDI, FAST, SOUPS |
| **Springer** | Commercial | IJCV, Nature Machine Intelligence, LNCS (ECCV) |
| **Independent / OA** | | NeurIPS, ICLR, ICML (PMLR, OpenReview), JMLR |

### 7.3 Is there a metric for the publisher itself?
No single score exists, because publishers print hundreds of journals in unrelated fields. People judge **each journal** (JIF, CiteScore, SJR, quartile) and check whether the publisher is reputable (member of COPE, indexed in Scopus / WoS, not on predatory lists).

---

## 8. How to Read a SCImago (SJR) Table

### 8.1 Filter panel

| Filter | Meaning |
|---|---|
| Subject Area | Broad field (e.g., Computer Science) |
| Subject Category | Subfield (AI, Networks, Software...) |
| Region / Country | Where the publisher is based |
| Type | Journals, book series, conference proceedings |
| Year | Data year |
| Only Open Access | Freely readable journals |
| Only Diamond OA | No fees for readers or authors |
| **Only SciELO** | Journals in SciELO, a Latin America / Iberia / South Africa open-access library |
| **Only WoS** | Journals also indexed in Web of Science (both Scopus and WoS) |
| Min. Total Citations (3 yrs) | Hide journals with fewer citations than this number (removes inactive journals) |
| Min. Citable Docs (3 yrs) | Hide journals that published fewer papers than this number (removes tiny journals) |

### 8.2 Table columns

| Column | Meaning |
|---|---|
| **SJR** | Prestige score. **Q1/Q2/Q3/Q4** shown beside it. |
| **H index** | Lifetime h-index of the journal |
| **Total Docs (year)** | All items published that year |
| **Total Docs (3 years)** | All items over 3 years |
| **Total Refs** | References listed in that year's papers |
| **Total Cites (3 years)** | Citations received by the journal's papers in 3 years |
| **Citable Docs (3 years)** | Articles and reviews only (no editorials, letters, news) |
| **Cites / Doc (2 years)** | Total cites ÷ citable docs over 2 years (similar to JIF) |
| **Ref / Doc** | Avg references per paper (shows the field's citing habit) |
| **% Female** | Share of female authors |
| **Country** | Publisher's country |

**Example:** 127 docs in 3 yrs, 76 citable, 29,333 cites → cites per citable doc = 29,333 / 76 ≈ **386** (the 2-year column uses a different window, so values differ).

**How the two numbers work together:**
- Journal A: 100 citable docs, 400 cites → **4.0** per paper
- Journal B: 1,000 citable docs, 400 cites → **0.4** per paper

---

## 9. Time Windows at a Glance

| Metric | Window | Can it go down? |
|---|---|---|
| h-index | Lifetime | **No** |
| i10-index | Lifetime (or last 5 yrs version) | All-time: No |
| Immediacy Index | 1 year (same year) | Yes |
| JIF | 2 years | Yes |
| JCI | 3 years | Yes |
| SNIP, SJR | 3 years | Yes |
| CiteScore | 4 years | Yes |
| FWCI | Pub. year + 3 following years | Yes (young papers) |

---

## 10. Master Cheat Sheet

| Metric | Applies to | Provider | One-line meaning | Formula |
|---|---|---|---|---|
| JIF | Journal | Clarivate | Avg cites, 2-yr | cites(Y) to (Y-1,Y-2) ÷ items (Y-1,Y-2) |
| JCI | Journal | Clarivate | Field-normalized, 1.0 = avg | mean of paper CNCI |
| Immediacy | Journal | Clarivate | Same-year cites | cites(Y) to items(Y) ÷ items(Y) |
| CiteScore | Journal | Elsevier | Avg cites, 4-yr | cites(4 yrs) ÷ docs(4 yrs) |
| SNIP | Journal | Elsevier | Field-adjusted | RIP ÷ citation potential |
| SJR | Journal | SCImago | Prestige-weighted | PageRank-style |
| h-index | Author, Journal, Conference | All | Productivity + impact | h papers with ≥ h cites |
| h-median | Author, Journal, Conference | All | Strength of the h-core | median citations of the h papers |
| i10-index | Author | Google | Papers with ≥ 10 cites | count |
| FWCI | Paper, Author, Institution | Elsevier | Relative to world | actual ÷ expected |
| CNCI | Paper | Clarivate | Relative to world | actual ÷ expected |
| Quartile | Journal | WoS / Scopus | Rank group | position ÷ total in category |
| CORE rank | Conference | CORE | Tier A*/A/B/C | Expert panel |
| Acceptance rate | Conference, Journal | Venue | Selectivity | accepted ÷ submitted |

---

## 11. Quartile: Worked Example

A category has **100 journals**, ranked by impact (JIF or SJR):

| Rank | Quartile |
|---|---|
| 1–25 | **Q1** (top 25%) |
| 26–50 | Q2 |
| 51–75 | Q3 |
| 76–100 | Q4 |

A journal ranked **#18** is **Q1**. A journal can be Q1 in one category and Q2 in another, and the quartile may differ between JCR (JIF-based) and Scopus (CiteScore/SJR-based).

---

## 12. Practical Checklist: Is a Venue Good?

**Journal**
1. Indexed in Scopus and/or Web of Science?
2. Quartile Q1 or Q2 in its category?
3. Reasonable JIF / CiteScore / SJR for the field? (Compare **within the same field only.**)
4. Real peer review, clear editorial board, transparent APC?
5. Not on predatory lists; publisher is a COPE member.

**Conference**
1. CORE rank A* or A (for CS)?
2. Low acceptance rate (below ~20%)?
3. High h-index / h-median on Google Scholar?
4. Proceedings indexed (IEEE Xplore, ACM DL, Springer, Scopus, WoS)?
5. Double-blind review, strong program committee.

**Rules to remember**
- Never compare raw metrics across different fields. Use field-normalized ones (SNIP, JCI, FWCI) or compare within a field.
- A metric of the **journal** says nothing about your **single paper**; use FWCI / citations for that.
- h-index grows with age, so compare people at similar career stages.
- Self-citations can inflate every citation-based score.
