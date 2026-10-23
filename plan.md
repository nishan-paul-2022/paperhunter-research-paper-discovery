Here's the updated spec, cleaned up:

---

# Paper Hunter: Rebuild Specification

## Scope

The `paper-hunter/` folder needs a major overhaul. **Nothing that exists today can be assumed correct or reusable.** This includes code, prompts, tables, views, and anything else related to Paper Hunter. All of the Python files, prompt files, tables, and views listed below must be created from scratch.

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
- **Sort:** by `failure_count`, ascending.

---

## 2. `b_abstract.py`: Fetch abstracts

### 2.1 Behavior
- Fetch the abstract for every paper in `paper_dblp`.

### 2.2 Table: `paper_abstract`

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `abstract` | The paper's abstract |

### 2.3 Tables: `paper_success` and `paper_failure`
Every API call made for every paper is logged in exactly one of these two tables, depending on whether it succeeded or failed. Both tables share the same structure:

| Field | Description |
|---|---|
| `paper_id` | References `paper_dblp.paper_id` |
| `api` | The full API call (complete request URL/endpoint) |
| `response` | The raw API response, stored exactly as received (empty if there was no response) |

### 2.4 Tables updated by `b_abstract.py`

| Table | Update |
|---|---|
| `paper_abstract` | Insert the abstract when one is found |
| `paper_success` | Log every successful API call |
| `paper_failure` | Log every failed API call |
| `paper_status` | Refresh `success_count` and `failure_count` from the two log tables; set `status = 1` once an abstract is found |
