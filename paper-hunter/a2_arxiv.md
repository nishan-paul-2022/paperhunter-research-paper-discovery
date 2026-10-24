# Master Prompt: Find arXiv IDs for Papers (Paper Hunter)

You are an autonomous research assistant with shell access and, only where this prompt allows it, web access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Fill `paper_dblp.arxiv_id` for every paper whose arXiv preprint can be identified reliably. `b_fetch.py` then looks those papers up by arXiv ID, which recovers abstracts that OpenAlex and Semantic Scholar lack. A wrong arXiv ID is worse than none: it would attach another paper's abstract. Precision comes first.

## Cost policy (strict order)

Tokens are expensive, so use the cheapest tool that can decide a paper.

1. **Script, zero tokens.** DBLP files every arXiv preprint as its own record under `journals/corr/` (the key encodes the arXiv ID: `journals/corr/abs-2308-06782` is `2308.06782`). DBLP never links a preprint to its published version, so Step 1 matches them by title and by DBLP's disambiguated author names (`Yi Liu 0069`). Clear matches are written automatically.
2. **Your judgment, local evidence only.** Step 2 shows you the pairs the script could not settle (a preprint retitled before publication, or a same-group paper with a similar title). Decide from titles, authors and years. No web access in this step.
3. **Online, only where badly needed.** Step 3 covers only screened-in papers (`paper_screen.passed = 1`) that still have no abstract, no arXiv ID and no local candidate. Query the free arXiv API first. Use a general web search only when the arXiv API result is ambiguous, and at most one search per paper.

## When to run

- **Step 1** right after `paper-hunter/a_extract.py`, before `paper-hunter/b_fetch.py`.
- **Steps 2 and 3** after `b_fetch.py` has completed its first full pass, so only papers that still lack an abstract cost tokens. Then rerun `b_fetch.py`: its arXiv step picks up the new IDs.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Use `.venv/bin/python`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_corr`, `paper_arxiv_candidate`, `paper_arxiv_match`, and the `arxiv_id` column of `paper_dblp`. Never change the schema or any other table.
- Every decision is one `paper_arxiv_match` row, written in the same transaction as the `paper_dblp.arxiv_id` update. `method` is `exact`, `close`, `ai`, `arxiv_api`, `web`, or `none`; `none` means "decided: no arXiv version found" and keeps the paper from being reviewed again.
- Never overwrite a non-NULL `paper_dblp.arxiv_id`.
- Store arXiv IDs in canonical form without the version suffix: `2308.06782`, or `cs/0601001` for old-style IDs.
- One arXiv ID may belong to several papers (a conference paper and its journal extension share a preprint). That is allowed.

Make sure the tables exist (this changes nothing if they do):

```sh
.venv/bin/python paper-hunter/a_extract.py --schema-only
```

## Step 1: Load DBLP's arXiv records and write the clear matches

Run this script once. It takes several minutes (one pass over the 5 GB `dblp.xml`, then matching). It is idempotent: rerunning it refreshes `paper_corr` and skips papers that already have a decision.

```sh
.venv/bin/python - <<'PY'
import json, re, sqlite3, time
from collections import defaultdict
from difflib import SequenceMatcher
from lxml import etree

DB, XML = "archive/paperhunter.db", "archive/dblp.xml"
TAGS = ("article", "inproceedings", "proceedings", "book", "incollection",
        "phdthesis", "mastersthesis", "www", "person", "data")
YEAR_GAP, CLOSE_SIM, CAND_SIM, CAND_JACCARD, PER_PAPER = 3, 0.90, 0.60, 0.50, 3
norm = lambda t: " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())

def name_prefix(title):
    head, sep, _ = (title or "").partition(":")
    return norm(head) if sep and len(head.split()) <= 3 else ""

conn = sqlite3.connect(DB, timeout=120)
conn.execute("PRAGMA foreign_keys = ON")
first_year = conn.execute("SELECT min(year) FROM paper_dblp").fetchone()[0] - YEAR_GAP

started, corr = time.time(), []
with open(XML, "rb") as f:
    for _, el in etree.iterparse(f, events=("end",), tag=TAGS, load_dtd=True,
                                 resolve_entities=True, huge_tree=True):
        key = el.get("key", "")
        if el.tag == "article" and key.startswith("journals/corr/"):
            year = (el.findtext("year") or "").strip()
            if year.isdigit() and int(year) >= first_year:
                arxiv = None
                for e in el.findall("ee"):
                    m = re.search(r"arxiv\.org/abs/([^\s?#]+?)(?:v\d+)?$", (e.text or "").strip())
                    if m:
                        arxiv = m.group(1).lower()
                        break
                if arxiv is None:
                    m = re.match(r"journals/corr/abs-(\d{4})-(\d{4,5})$", key)
                    arxiv = f"{m.group(1)}.{m.group(2)}" if m else None
                t = el.find("title")
                title = " ".join("".join(t.itertext()).split()) if t is not None else ""
                authors = [" ".join("".join(a.itertext()).split()) for a in el.findall("author")]
                if arxiv and title:
                    corr.append((key, arxiv, title, int(year), authors))
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
with conn:
    conn.executemany("INSERT OR REPLACE INTO paper_corr (dblp_key, arxiv_id, title, year, authors) "
                     "VALUES (?, ?, ?, ?, ?)", [(k, a, t, y, json.dumps(au, ensure_ascii=False))
                                                for k, a, t, y, au in corr])
print(f"paper_corr: {len(corr):,} arXiv records from {first_year} on ({time.time() - started:.0f}s)")

by_title, by_author = defaultdict(list), defaultdict(list)
for i, (_, _, t, _, au) in enumerate(corr):
    by_title[norm(t)].append(i)
    for a in au:
        by_author[a].append(i)

papers = conn.execute(
    "SELECT paper_id, title, authors, year FROM paper_dblp "
    "WHERE arxiv_id IS NULL AND paper_id NOT IN (SELECT paper_id FROM paper_arxiv_match)").fetchall()
matches, candidates = [], []
for pid, title, authors, year in papers:
    ours = set(json.loads(authors))
    if not ours:
        continue
    hit = next((i for i in by_title.get(norm(title), [])
                if ours & set(corr[i][4]) and abs(corr[i][3] - year) <= YEAR_GAP), None)
    if hit is not None:
        matches.append((pid, corr[hit], "exact", "same title, shared author"))
        continue
    scored = []
    for i in {i for a in ours for i in by_author.get(a, [])}:
        k, arxiv, ct, cy, cau = corr[i]
        if abs(cy - year) > YEAR_GAP:
            continue
        theirs = set(cau)
        jaccard = len(ours & theirs) / len(ours | theirs)
        if jaccard < CAND_JACCARD:
            continue
        sm = SequenceMatcher(None, norm(title), norm(ct))
        sim = sm.ratio() if sm.real_quick_ratio() >= CAND_SIM * 0.8 else 0.0
        same_name = bool(name_prefix(title)) and name_prefix(title) == name_prefix(ct)
        if sim >= CAND_SIM or same_name:
            scored.append((sim, jaccard, i))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= CLOSE_SIM and (len(scored) == 1 or scored[1][0] < CLOSE_SIM):
        matches.append((pid, corr[scored[0][2]], "close", f"title similarity {scored[0][0]:.2f}, "
                        f"author overlap {scored[0][1]:.2f}"))
        continue
    candidates += [(pid, corr[i][0], round(sim, 4), round(jac, 4)) for sim, jac, i in scored[:PER_PAPER]]

with conn:
    for pid, (k, arxiv, *_), method, reason in matches:
        conn.execute("INSERT INTO paper_arxiv_match (paper_id, arxiv_id, method, source, reason) "
                     "VALUES (?, ?, ?, ?, ?)", (pid, arxiv, method, k, reason))
        conn.execute("UPDATE paper_dblp SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL", (arxiv, pid))
    conn.execute("DELETE FROM paper_arxiv_candidate WHERE paper_id IN (SELECT paper_id FROM paper_arxiv_match)")
    conn.executemany("INSERT OR REPLACE INTO paper_arxiv_candidate (paper_id, dblp_key, title_sim, author_jaccard) "
                     "VALUES (?, ?, ?, ?)", candidates)
print(f"matched: exact {sum(m[2] == 'exact' for m in matches):,}  close {sum(m[2] == 'close' for m in matches):,}"
      f"  |  candidate pairs left for review: {len(candidates):,} "
      f"({len({c[0] for c in candidates}):,} papers)  |  {time.time() - started:.0f}s")
PY
```

## Step 2: Judge the ambiguous pairs (your intelligence, local evidence only)

Review only papers that still need it: no abstract yet, no decision yet, at least one candidate. Screened-in papers come first. Read a batch of **50 papers**:

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT c.paper_id, p.year, p.title, p.authors,
       c.dblp_key, k.arxiv_id, k.year, k.title, k.authors, c.title_sim, c.author_jaccard
FROM paper_arxiv_candidate c
JOIN paper_dblp p ON p.paper_id = c.paper_id
JOIN paper_corr k ON k.dblp_key = c.dblp_key
JOIN paper_abstract_status s ON s.paper_id = c.paper_id
LEFT JOIN paper_screen sc ON sc.paper_id = c.paper_id
WHERE s.has_abstract = 0
  AND c.paper_id NOT IN (SELECT paper_id FROM paper_arxiv_match)
  AND c.paper_id IN (
      SELECT c2.paper_id FROM paper_arxiv_candidate c2
      JOIN paper_abstract_status s2 ON s2.paper_id = c2.paper_id
      LEFT JOIN paper_screen sc2 ON sc2.paper_id = c2.paper_id
      WHERE s2.has_abstract = 0
        AND c2.paper_id NOT IN (SELECT paper_id FROM paper_arxiv_match)
      GROUP BY c2.paper_id
      ORDER BY coalesce(max(sc2.passed), 0) DESC, max(sc2.similarity) DESC, c2.paper_id
      LIMIT 50)
ORDER BY c.paper_id, c.title_sim DESC;"
```

For each paper, decide whether one candidate is **its own preprint**:

- **Same authors.** DBLP names are disambiguated (`Yang Liu 0003` is one person), so a strong overlap is solid evidence. A preprint may add or drop an author or two.
- **Same work.** The core contribution must be the same: the same system or method name, the same problem, the same key terms. Preprints are often retitled before publication (`PentestGPT: An LLM-empowered Automatic Penetration Testing Tool` became `PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing`). A shared system name before the colon is a strong signal.
- **Plausible timing.** The preprint usually appears before or within about a year of publication.
- **Reject** a candidate that is a different paper by the same group: a follow-up, a companion, or an unrelated paper in the same area. When two candidates both look plausible and you cannot tell which one is the preprint, decide `none` rather than guess.

Record each paper's decision, one transaction per paper:

```sh
.venv/bin/python - <<'PY'
import sqlite3
decisions = [
    (210341, "2308.06782", "ai", "journals/corr/abs-2308-06782",
     "Same disambiguated authors; same PentestGPT system, retitled for USENIX."),
    (123456, None, "none", None, "Both candidates are different papers by the same group."),
]
conn = sqlite3.connect("archive/paperhunter.db", timeout=120)
conn.execute("PRAGMA foreign_keys = ON")
for pid, arxiv, method, source, reason in decisions:
    with conn:
        conn.execute("INSERT INTO paper_arxiv_match (paper_id, arxiv_id, method, source, reason) "
                     "VALUES (?, ?, ?, ?, ?)", (pid, arxiv, method, source, reason))
        if arxiv:
            conn.execute("UPDATE paper_dblp SET arxiv_id = ? WHERE paper_id = ? AND arxiv_id IS NULL",
                         (arxiv, pid))
print(f"recorded {len(decisions)} decisions")
PY
```

Replace the example rows with your own decisions. Repeat batches until the query returns nothing.

## Step 3: Online lookup, only where badly needed

This step covers only screened-in papers that still have no abstract, no arXiv ID, no decision, and no local candidate:

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT p.paper_id, p.year, p.title, p.authors
FROM paper_dblp p
JOIN paper_screen sc ON sc.paper_id = p.paper_id AND sc.passed = 1
JOIN paper_abstract_status s ON s.paper_id = p.paper_id AND s.has_abstract = 0
WHERE p.arxiv_id IS NULL
  AND p.paper_id NOT IN (SELECT paper_id FROM paper_arxiv_match)
  AND p.paper_id NOT IN (SELECT paper_id FROM paper_arxiv_candidate)
ORDER BY sc.similarity DESC
LIMIT 20;"
```

**First, the arXiv API** (free; send at most one request every 3 seconds). For each paper, search by exact title:

```sh
.venv/bin/python - <<'PY'
import re, time, requests, xml.etree.ElementTree as ET
ns = {"a": "http://www.w3.org/2005/Atom"}
titles = {210341: "PentestGPT: Evaluating and Harnessing Large Language Models for Automated Penetration Testing"}
for pid, title in titles.items():
    q = 'ti:"' + re.sub(r'["]', " ", title).rstrip(".") + '"'
    r = requests.get("http://export.arxiv.org/api/query", params={"search_query": q, "max_results": 5}, timeout=60)
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        print(pid, "|", e.find("a:id", ns).text.rsplit("/", 1)[-1], "|",
              e.find("a:published", ns).text[:4], "|", " ".join(e.find("a:title", ns).text.split()), "|",
              ", ".join(a.find("a:name", ns).text for a in e.findall("a:author", ns)))
    time.sleep(3.1)
PY
```

If the exact title returns nothing, send one looser query, still free: the system name before the colon (`ti:"PentestGPT"`), or three to five distinctive title words joined with `AND` (`ti:ChainReactor AND ti:escalation`). Preprints are often retitled, and the name usually survives.

Accept a result only when the title matches (allowing punctuation and casing differences, or a retitling you can verify from the authors and the method), at least one author surname is shared, and the arXiv year is no later than the DBLP year plus one. Record it with `method = 'arxiv_api'` and `source` = the arXiv abstract URL (`https://arxiv.org/abs/<id>`), using the Step 2 recording snippet.

**Only if the arXiv API is ambiguous**, run one web search for the paper's title together with the word "arXiv". If it leads to a verifiable arXiv page for this exact paper, record it with `method = 'web'` and `source` = that URL. Otherwise record `none`.

## Finish

Report the totals:

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT method, count(*) FROM paper_arxiv_match GROUP BY method ORDER BY count(*) DESC;
SELECT 'papers with arxiv_id', count(*) FROM paper_dblp WHERE arxiv_id IS NOT NULL;"
```

**Afterwards:** rerun `paper-hunter/b_fetch.py` so its arXiv-ID lookups use the new IDs.
