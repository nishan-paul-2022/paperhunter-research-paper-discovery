# Master Prompt: Find arXiv IDs for Papers (Paper Hunter)

You are an autonomous research assistant with shell access and, only where this prompt allows it, web access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Fill `paper_dblp.arxiv_id` for papers whose arXiv preprint the script `b_arxiv.py` could not settle on its own. `c_fetch.py` then looks those papers up by arXiv ID, which recovers abstracts that the DOI lookups miss. A wrong arXiv ID is worse than none: it would attach another paper's abstract. Precision comes first.

## Background

DBLP files every arXiv preprint as its own record under `journals/corr/` (the key encodes the arXiv ID: `journals/corr/abs-2308-06782` is `2308.06782`), but never links a preprint to its published version. `paper-hunter/b_arxiv.py` already loaded those records into `paper_corr`, matched the clear cases by title and DBLP's disambiguated author names (`Yi Liu 0069`), and wrote them to `paper_arxiv_match` with `method` `exact` or `close`. The pairs it could not settle are waiting in `paper_arxiv_candidate`. This prompt finishes the job.

## Cost policy (strict order)

Tokens are expensive, so use the cheapest tool that can decide a paper.

1. **Your judgment, local evidence only.** Step 1 shows you the pairs the script could not settle (a preprint retitled before publication, or a same-group paper with a similar title). Decide from titles, authors and years. No web access in this step.
2. **Online, only where badly needed.** Step 2 covers only screened-in papers (`paper_screen.passed = 1`) that still have no abstract, no arXiv ID and no local candidate. Query the free arXiv API first. Use a general web search only when the arXiv API result is ambiguous, and at most one search per paper.

## When to run

After `paper-hunter/c_fetch.py` has completed its first full pass, so only papers that still lack an abstract cost tokens. Then rerun `c_fetch.py` so its arXiv-ID lookups use the new IDs.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `archive/paperhunter.db`. Use `.venv/bin/python`. Start every connection with `PRAGMA foreign_keys = ON;`.
- Write only to `paper_arxiv_match` and the `arxiv_id` column of `paper_dblp`. Never change the schema or any other table.
- Every decision is one `paper_arxiv_match` row, written in the same transaction as the `paper_dblp.arxiv_id` update. `method` is `ai`, `arxiv_api`, `web`, or `none`; `none` means "decided: no arXiv version found" and keeps the paper from being reviewed again.
- Never overwrite a non-NULL `paper_dblp.arxiv_id`.
- Store arXiv IDs in canonical form without the version suffix: `2308.06782`, or `cs/0601001` for old-style IDs.
- One arXiv ID may belong to several papers (a conference paper and its journal extension share a preprint). That is allowed.

## Step 1: Judge the ambiguous pairs (your intelligence, local evidence only)

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

## Step 2: Online lookup, only where badly needed

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

Accept a result only when the title matches (allowing punctuation and casing differences, or a retitling you can verify from the authors and the method), at least one author surname is shared, and the arXiv year is no later than the DBLP year plus one. Record it with `method = 'arxiv_api'` and `source` = the arXiv abstract URL (`https://arxiv.org/abs/<id>`), using the Step 1 recording snippet.

**Only if the arXiv API is ambiguous**, run one web search for the paper's title together with the word "arXiv". If it leads to a verifiable arXiv page for this exact paper, record it with `method = 'web'` and `source` = that URL. Otherwise record `none`.

## Finish

Report the totals:

```sh
sqlite3 -readonly archive/paperhunter.db "
SELECT method, count(*) FROM paper_arxiv_match GROUP BY method ORDER BY count(*) DESC;
SELECT 'papers with arxiv_id', count(*) FROM paper_dblp WHERE arxiv_id IS NOT NULL;"
```

**Afterwards:** rerun `paper-hunter/c_fetch.py` so its arXiv-ID lookups use the new IDs.
