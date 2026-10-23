# Paper Hunter: Rebuild Specification

## Scope

The `paper-hunter/` folder needs a major overhaul. **Nothing that exists today can be assumed correct or reusable.** This includes code, prompts, tables, views, and anything else related to Paper Hunter. All of the Python files, prompt files, tables, and views listed below must be created from scratch.

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `a_extract.py` | Python | Extract papers from DBLP and initialize their status |
| 2 | `b_abstract.py` | Python | Fetch abstracts via the OpenAlex and Semantic Scholar APIs |
| 3 | `c_abstract.md` | AI prompt | Find abstracts that the APIs missed by searching online |

---

## 1. `a_extract.py`: Extract papers from DBLP

### 1.1 Source and filtering
- Read papers from `archive/dblp.xml`.
- Include only papers whose venue appears in the **`conf_list`** table (conferences) or the **`journal_list`** table (journals).

### 1.2 Table: `paper_dblp`
Stores every paper selected in 1.1.

| Field | Description |
|---|---|
| `paper_id` | Auto-incrementing integer ID, starting from 1 |
| *(all DBLP fields)* | All information fetched from `dblp.xml` for that paper |

### 1.3 Table: `paper_status`
Tracks the abstract-retrieval status of each paper.

| Field | Default | Description |
|---|---|---|
| `paper_id` | — | References `paper_dblp.paper_id` |
| `failure_count` | `0` | Number of API calls that returned no abstract |
| `success_count` | `0` | Number of API calls that returned an abstract |
| `status` | `0` | `0` = no abstract found yet; `1` = abstract found |

- `a_extract.py` inserts **only `paper_id`**. All other fields take their default value of `0`.
- `failure_count` and `success_count` are derived from the `paper_failure` and `paper_success` tables (see 2.3).

**Definitions**
- **Success:** an API call whose response contains the paper's abstract.
- **Failure:** an API call whose response does not contain the abstract.

### 1.4 View: `paper_review`
This view is auto-generated on top of `paper_status`.

| Field | Description |
|---|---|
| `paper_id` | Paper ID |
| `failure_count` | Number of failed API calls |

- **Filter:** only papers with `status = 0`.
- **Sort:** by `failure_count`, ascending. The first row is the highest-priority paper and the last row is the lowest.

---

## 2. `b_abstract.py`: Fetch abstracts via APIs

### 2.1 Behavior
- Fetch the abstract for every paper in `paper_dblp`.

### 2.2 API strategy

**Sources**
- Call **OpenAlex** and **Semantic Scholar** simultaneously for each paper.
- Load the API keys from the `.env` file.

**Lookup order**
1. Query both APIs by **DOI** first.
2. If the DOI lookup fails, retry using the paper's **title**.

**Efficiency requirements**
The code must be designed with all of the following in mind together:
- **Maximum yield:** get as many abstracts as possible.
- **Minimum time:** finish as quickly as possible.
- **Minimum API calls:** avoid unnecessary calls to reduce credit usage.
- **API limits:** respect each API's rate limits, throttling rules, and any other usage restrictions.

### 2.3 Table: `paper_abstract`

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `abstract` | The paper's abstract |

### 2.4 Tables: `paper_success` and `paper_failure`
Every API call made for every paper is logged in exactly one of these two tables, depending on whether it succeeded or failed. Both tables share the same structure:

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `api` | The full API call (complete request URL/endpoint) |
| `response` | The raw API response, stored exactly as received (empty if there was no response) |

### 2.5 Tables updated by `b_abstract.py`

| Table | Update |
|---|---|
| `paper_abstract` | Insert the abstract when one is found |
| `paper_success` | Log every successful API call |
| `paper_failure` | Log every failed API call |
| `paper_status` | Refresh `success_count` and `failure_count` from the two log tables; set `status = 1` once an abstract is found |

---

## 3. `c_abstract.md`: AI prompt for an online abstract search

### 3.1 Purpose
Many papers may still be listed in `paper_review` because the APIs could not find their abstracts. This prompt directs an AI to search the web for those abstracts.

### 3.2 Workflow
For each paper, in the order listed in `paper_review` (highest priority first):

1. **Get the details.** Take the next `paper_id` from `paper_review` and fetch its full record from `paper_dblp`.
2. **Check past failures.** Read `paper_failure` for this `paper_id`. Skip every API or website already logged there, because searching them again wastes tokens.
3. **Search online.** Use every available detail to search for the abstract: DOI, venue, title, authors, year, and every other field in `paper_dblp`.
4. **Log each source.** Record every API or website the AI tries:
   - **Abstract found:** log it in `paper_success`.
   - **Abstract not found:** log it in `paper_failure`, with whatever information the source returned in the `response` field.
5. **Update the results.**
   - **If a genuine abstract is found:** update `paper_abstract`, `paper_success`, and `paper_status`.
   - **If no abstract is found:** update `paper_failure`.

### 3.3 Rules
- **Accuracy:** record an abstract only if the AI has truly found it. Never guess or generate one.
- **No repeats:** never query a source already logged in `paper_failure` for the same paper.
